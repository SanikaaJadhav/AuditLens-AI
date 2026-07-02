from collections import defaultdict

from app.pipeline.reference_data import (
    load_medical_necessity_rules,
    load_mue_limits,
    load_ncci_pairs,
)
from app.schemas import Claim, ClaimLine, ClinicalEvidenceSet, ConfidenceBreakdown, DocumentedEvidence, ValidationFlag


def deterministic_confidence_breakdown() -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        rule_match="Deterministic (certain)",
        retrieval_score=None,
        retrieval_passages_found=0,
        llm_verdict="Not applicable",
        conflicting_evidence=False,
    )


def _line_date(claim: Claim, line: ClaimLine) -> str:
    service_date = line.service_date or claim.service_date
    return service_date.isoformat()


def _line_key(claim: Claim, line: ClaimLine) -> tuple[str, str, str | None, tuple[str, ...]]:
    return (_line_date(claim, line), line.code, line.place_of_service, tuple(line.modifiers))


def _procedure_evidence_by_code(evidence: ClinicalEvidenceSet) -> dict[str, list[DocumentedEvidence]]:
    by_code: dict[str, list[DocumentedEvidence]] = defaultdict(list)
    for item in evidence.documented_procedures:
        if item.code:
            by_code[item.code].append(item)
    return by_code


def _diagnosis_evidence_by_code(evidence: ClinicalEvidenceSet) -> dict[str, list[DocumentedEvidence]]:
    by_code: dict[str, list[DocumentedEvidence]] = defaultdict(list)
    for item in evidence.documented_diagnoses:
        if item.code:
            by_code[item.code].append(item)
    return by_code


def run_duplicate_checks(claim: Claim) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    seen: dict[tuple[str | None, str, str | None, tuple[str, ...]], ClaimLine] = {}

    for line in claim.lines:
        key = _line_key(claim, line)
        original = seen.get(key)
        if original:
            flags.append(
                ValidationFlag(
                    line_id=line.line_id,
                    status="rule_violation",
                    rule="DUPLICATE",
                    severity="medium",
                    citation=None,
                    page=None,
                    confidence=1.0,
                    confidence_breakdown=deterministic_confidence_breakdown(),
                    recommended_action="deny_line",
                    message=(
                        f"Line {line.line_id} duplicates line {original.line_id} for code "
                        f"{line.code} on {_line_date(claim, line)}."
                    ),
                )
            )
        else:
            seen[key] = line

    return flags


def run_mue_checks(claim: Claim) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    limits = load_mue_limits()

    for line in claim.lines:
        limit = limits.get(line.code)
        if not limit:
            continue
        if line.units > limit.max_units_per_day:
            flags.append(
                ValidationFlag(
                    line_id=line.line_id,
                    status="rule_violation",
                    rule="MUE_UNITS",
                    severity="medium",
                    citation=None,
                    page=None,
                    confidence=1.0,
                    confidence_breakdown=deterministic_confidence_breakdown(),
                    recommended_action="deny_line",
                    message=(
                        f"Code {line.code} was billed with {line.units} units, exceeding the "
                        f"prototype limit of {limit.max_units_per_day}. {limit.rule_description}"
                    ),
                )
            )

    return flags


def run_ncci_checks(claim: Claim) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    pairs = load_ncci_pairs()
    lines_by_code: dict[str, list[ClaimLine]] = defaultdict(list)
    for line in claim.lines:
        lines_by_code[line.code].append(line)

    for pair in pairs:
        primary_lines = lines_by_code.get(pair.primary_code, [])
        bundled_lines = lines_by_code.get(pair.bundled_code, [])
        if not primary_lines or not bundled_lines:
            continue

        for bundled_line in bundled_lines:
            matching_primary = next(
                (
                    line
                    for line in primary_lines
                    if _line_date(claim, line) == _line_date(claim, bundled_line)
                    and line.place_of_service == bundled_line.place_of_service
                ),
                primary_lines[0],
            )
            flags.append(
                ValidationFlag(
                    line_id=bundled_line.line_id,
                    status="rule_violation",
                    rule="NCCI_BUNDLE",
                    severity="high",
                    citation=None,
                    page=None,
                    confidence=1.0,
                    confidence_breakdown=deterministic_confidence_breakdown(),
                    recommended_action="deny_line",
                    message=(
                        f"Code {bundled_line.code} on line {bundled_line.line_id} is bundled "
                        f"into code {matching_primary.code} on line {matching_primary.line_id}. "
                        f"{pair.rule_description}"
                    ),
                )
            )

    return flags


def run_rulebook_checks(claim: Claim) -> list[ValidationFlag]:
    return run_duplicate_checks(claim) + run_mue_checks(claim) + run_ncci_checks(claim)


def run_medical_necessity_checks(
    claim: Claim,
    evidence: ClinicalEvidenceSet,
) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    necessity_rules = load_medical_necessity_rules()
    diagnosis_evidence = _diagnosis_evidence_by_code(evidence)
    documented_diagnoses = set(diagnosis_evidence)
    procedure_evidence = _procedure_evidence_by_code(evidence)

    for line in claim.lines:
        rule = necessity_rules.get(line.code)
        if not rule:
            continue
        supported_codes = documented_diagnoses & rule.allowed_diagnosis_codes
        if supported_codes:
            continue

        procedure_spans = procedure_evidence.get(line.code, [])
        citation = None
        page = None
        if procedure_spans:
            citation = procedure_spans[0].source_span
            page = procedure_spans[0].page

        allowed = ", ".join(sorted(rule.allowed_diagnosis_codes))
        documented = ", ".join(sorted(documented_diagnoses)) or "none"
        severity = "high" if not procedure_spans else "medium"
        recommended_action = "escalate" if severity == "high" else "request_records"

        flags.append(
            ValidationFlag(
                line_id=line.line_id,
                status="rule_violation",
                rule="MEDICAL_NECESSITY",
                severity=severity,
                citation=citation,
                page=page,
                confidence=1.0,
                confidence_breakdown=deterministic_confidence_breakdown(),
                recommended_action=recommended_action,
                message=(
                    f"Code {line.code} lacks a documented diagnosis from the allowed set "
                    f"({allowed}). Documented diagnoses: {documented}. {rule.rule_description}"
                ),
            )
        )

    return flags
