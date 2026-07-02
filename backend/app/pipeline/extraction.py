from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from app.config import LLM_MODE, SAMPLE_CLAIM_JSON, SAMPLE_EVIDENCE_JSON
from app.pipeline.llm_client import LLMCallError, LLMConfigurationError, LLMMessage, call_openrouter_json
from app.pipeline.ocr import extract_text_from_document
from app.schemas import Claim, ClinicalEvidenceSet, DocumentedEvidence, VisitComplexityEvidence


def load_claim_from_json(path: Path = SAMPLE_CLAIM_JSON) -> Claim:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return Claim.model_validate(payload)


def load_clinical_evidence_from_json(path: Path = SAMPLE_EVIDENCE_JSON) -> ClinicalEvidenceSet:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return ClinicalEvidenceSet.model_validate(payload)


def extract_claim_lines(path: Path) -> Claim:
    return load_claim_from_json(path)


def extract_clinical_evidence(path: Path) -> ClinicalEvidenceSet:
    """Use live extraction when enabled; fall back to deterministic parsing."""
    if path.suffix.lower() == ".json":
        return load_clinical_evidence_from_json(path)

    note_text = extract_text_from_document(path)
    if LLM_MODE == "live":
        try:
            return extract_clinical_evidence_with_llm(note_text)
        except (LLMConfigurationError, LLMCallError, ValueError):
            return extract_clinical_evidence_mock(note_text)
    return extract_clinical_evidence_mock(note_text)


def extract_clinical_evidence_with_llm(note_text: str) -> ClinicalEvidenceSet:
    payload = call_openrouter_json(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are a clinical documentation extraction engine for a payment-integrity "
                    "prototype. Extract only facts explicitly stated in the record. Use exact "
                    "source spans from the note. Do not infer diagnoses or procedures."
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    "Extract diagnoses, procedures, and visit complexity from this synthetic "
                    "clinical record. Return JSON matching the provided schema.\n\n"
                    f"{note_text}"
                ),
            ),
        ],
        json_schema=ClinicalEvidenceSet.model_json_schema(),
        schema_name="clinical_evidence_set",
        temperature=0.0,
    )
    return ClinicalEvidenceSet.model_validate(payload)


def extract_clinical_evidence_mock(note_text: str) -> ClinicalEvidenceSet:
    claim_id = _extract_first(r"Claim ID:\s*(\S+)", note_text, default="CLM-1001")
    patient_id = _extract_first(r"Patient ID:\s*(\S+)", note_text, default="PAT-001")
    service_date = _safe_service_date(
        _extract_first(r"Date of Service:\s*(\d{4}-\d{2}-\d{2})", note_text, default="2025-03-04")
    )

    diagnoses = _extract_diagnoses(note_text, service_date)
    procedures = _extract_procedures(note_text, service_date)
    complexity = _extract_visit_complexity(note_text)

    return ClinicalEvidenceSet(
        claim_id=claim_id,
        patient_id=patient_id,
        documented_diagnoses=diagnoses,
        documented_procedures=procedures,
        documented_visit_complexity=complexity,
    )


def _extract_first(pattern: str, text: str, default: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else default


def _safe_service_date(value: str, default: str = "2025-03-04") -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        return default
    return value


def _page_for_span(note_text: str, span: str) -> int | None:
    position = note_text.find(span)
    if position == -1:
        return None
    page = 1
    for marker in re.finditer(r"--- Page (\d+) ---", note_text):
        if marker.start() <= position:
            page = int(marker.group(1))
    return page


def _extract_diagnoses(note_text: str, service_date: str) -> list[DocumentedEvidence]:
    diagnoses: list[DocumentedEvidence] = []
    for index, match in enumerate(
        re.finditer(r"\d+\.\s+(.+?)\s+\(([A-Z0-9]{3}(?:\.[A-Z0-9]+)?)\)\.", note_text),
        start=1,
    ):
        description = match.group(1).strip()
        code = _normalize_ocr_icd_code(match.group(2).strip(), description)
        source_span = match.group(0).split(". ", 1)[1] if ". " in match.group(0) else match.group(0)
        diagnoses.append(
            DocumentedEvidence(
                evidence_id=f"DX{index}",
                type="diagnosis",
                code=code,
                description=description,
                date=service_date,
                source_span=source_span,
                page=_page_for_span(note_text, match.group(0)),
            )
        )
    return diagnoses


def _normalize_ocr_icd_code(code: str, description: str) -> str:
    """Repair common OCR confusions in synthetic ICD codes."""
    normalized = code.upper().replace("O", "0")
    if len(normalized) > 2 and normalized[1] == "Q" and normalized[0].isalpha():
        normalized = f"{normalized[0]}0{normalized[2:]}"
    if normalized.startswith("1") and "hypertension" in description.lower():
        normalized = f"I{normalized[1:]}"
    return normalized


def _extract_procedures(note_text: str, service_date: str) -> list[DocumentedEvidence]:
    procedure_patterns = [
        (
            "PX1",
            "71046",
            "Chest X-ray, two views",
            r"Chest X-ray, two views, was ordered and completed.+?pleural effusion\.",
        ),
        (
            "PX2",
            "93000",
            "Electrocardiogram complete",
            r"Electrocardiogram was performed and interpreted.+?ST-T changes\.",
        ),
        (
            "PX3",
            "36415",
            "Venipuncture",
            r"One venous blood sample was collected.+?2025-03-04\.",
        ),
        (
            "PX4",
            "76700",
            "Complete abdominal ultrasound",
            r"Complete abdominal ultrasound was performed.+?supporting indication\.",
        ),
    ]
    procedures: list[DocumentedEvidence] = []
    for evidence_id, code, description, pattern in procedure_patterns:
        match = re.search(pattern, note_text, flags=re.DOTALL)
        if not match:
            continue
        source_span = " ".join(match.group(0).split())
        procedures.append(
            DocumentedEvidence(
                evidence_id=evidence_id,
                type="procedure",
                code=code,
                description=description,
                date=service_date,
                source_span=source_span,
                page=_page_for_span(note_text, match.group(0)),
            )
        )
    return procedures


def _extract_visit_complexity(note_text: str) -> VisitComplexityEvidence | None:
    match = re.search(r"The visit involved (minimal|low|moderate|high) complexity\.", note_text)
    if not match:
        return None
    level = match.group(1)
    supported_code = {"minimal": "99211", "low": "99213", "moderate": "99214", "high": "99215"}[level]
    return VisitComplexityEvidence(
        supported_level=level,
        supported_code=supported_code,
        source_span=match.group(0),
        page=_page_for_span(note_text, match.group(0)),
    )
