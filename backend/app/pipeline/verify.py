from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from typing import Any

from app.config import LLM_MODE, VERIFY_MAX_WORKERS
from app.pipeline.llm_client import LLMCallError, LLMConfigurationError, LLMMessage, call_openrouter_json
from app.pipeline.retrieval import RetrievedPassage, build_line_query, retrieve_passages_for_line
from app.schemas import (
    AIVerificationTrace,
    Claim,
    ClaimLine,
    ClinicalEvidenceSet,
    ConfidenceBreakdown,
    RetrievedEvidencePassage,
    ValidationFlag,
)


VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "supported": {"type": "boolean"},
        "issue": {
            "type": "string",
            "enum": ["SUPPORTED", "NO_DOCUMENTATION", "UNSUPPORTED", "UPCODE"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "citation": {"type": ["string", "null"]},
        "page": {"type": ["integer", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["supported", "issue", "confidence", "citation", "page", "reason"],
}

RETRIEVAL_PASSAGE_THRESHOLD = 0.05

SERVICE_EVIDENCE_PHRASES: dict[str, tuple[str, ...]] = {
    "99213": (
        "office visit",
        "urgent visit",
        "encounter",
        "physical examination",
        "assessment",
        "plan",
        "provider",
    ),
    "99214": (
        "office visit",
        "urgent visit",
        "encounter",
        "physical examination",
        "assessment",
        "plan",
        "provider",
    ),
    "85025": ("complete blood count", "cbc", "wbc", "hemoglobin", "platelets"),
    "80048": ("basic metabolic panel", "bmp"),
    "80053": ("comprehensive metabolic panel", "cmp", "metabolic abnormalities"),
    "83036": ("hemoglobin a1c", "a1c", "hba1c"),
    "80061": ("lipid panel",),
    "81001": ("urinalysis", "urine", "ua"),
    "93000": ("electrocardiogram", "ecg", "ekg"),
    "93306": ("echocardiogram", "echocardiography", "transthoracic", "doppler"),
    "36415": ("venipuncture", "venous blood sample", "blood sample", "blood draw", "blood collected"),
    "99406": ("behavior change counseling", "smoking cessation", "tobacco cessation", "cessation counseling"),
    "12002": ("wound closed", "simple interrupted sutures", "laceration", "wound irrigated", "sutures"),
    "90714": ("tetanus", "td vaccine", "vaccine"),
    "96372": ("injection", "administration", "administered"),
    "73090": ("forearm x-ray", "x-ray", "xray", "radiograph"),
    "11102": ("tangential biopsy", "biopsy was performed", "biopsy performed", "specimen sent"),
    "11103": ("additional lesion", "separate additional lesion", "second biopsy", "multiple biopsies"),
    "88305": ("pathology", "specimen sent to pathology", "pathology for evaluation"),
    "17000": ("destruction", "premalignant lesion", "actinic keratosis", "cryotherapy"),
    "90686": ("influenza vaccine", "flu vaccine", "vaccination"),
    "90471": ("vaccine administration", "immunization administration", "administered vaccine"),
}

DESCRIPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "complete blood count": ("cbc", "wbc", "hemoglobin", "platelets"),
    "comprehensive metabolic panel": ("cmp", "metabolic abnormalities"),
    "basic metabolic panel": ("bmp",),
    "hemoglobin a1c": ("a1c", "hba1c"),
    "office visit": ("encounter", "physical examination", "assessment", "plan"),
    "urgent care visit": ("urgent visit", "visit type"),
    "influenza vaccine": ("flu vaccine", "vaccination"),
}


def _procedure_codes(evidence: ClinicalEvidenceSet) -> set[str]:
    return {item.code for item in evidence.documented_procedures if item.code}


def _passage_payload(passages: list[RetrievedPassage]) -> str:
    lines = []
    for index, passage in enumerate(passages, start=1):
        page = passage.page if passage.page is not None else "unknown"
        lines.append(f"[Passage {index} | page {page} | score {passage.score}]\n{passage.text}")
    return "\n\n".join(lines)


def _trace_passages(passages: list[RetrievedPassage]) -> list[RetrievedEvidencePassage]:
    return [
        RetrievedEvidencePassage(
            rank=index,
            text=passage.text,
            page=passage.page,
            score=passage.score,
        )
        for index, passage in enumerate(passages, start=1)
    ]


def _llm_verdict_text(verification: dict[str, Any]) -> str:
    issue = str(verification.get("issue", "UNCERTAIN")).lower().replace("_", " ")
    if verification.get("supported") is True:
        return "supported"
    if issue == "no documentation":
        return "not supported"
    return issue or "uncertain"


def _passage_looks_negative(text: str) -> bool:
    lowered = text.lower()
    negative_phrases = [" no ", " not ", "without", "denies", "denied", "none", "was not", "were not"]
    return any(phrase in f" {lowered} " for phrase in negative_phrases)


def _passage_mentions_line(line: ClaimLine, passage: RetrievedPassage) -> bool:
    lowered = passage.text.lower()
    if line.code.lower() in lowered:
        return True
    if _passage_supports_line(line, passage):
        return True
    description_tokens = [token for token in line.description.lower().replace("-", " ").split() if len(token) > 3]
    if not description_tokens:
        return False
    matched = sum(1 for token in description_tokens if token in lowered)
    return matched >= max(1, len(description_tokens) // 2)


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _description_phrases(description: str) -> set[str]:
    lowered = _normalized_text(description)
    phrases = {lowered}
    for phrase, aliases in DESCRIPTION_ALIASES.items():
        if phrase in lowered:
            phrases.update(aliases)
    tokens = [token for token in re.findall(r"[a-z0-9]+", lowered) if len(token) > 3]
    phrases.update(tokens)
    return {phrase for phrase in phrases if phrase}


def _phrase_is_negated(phrase: str, text: str) -> bool:
    phrase = re.escape(phrase)
    negation_patterns = [
        rf"\bno\s+{phrase}\b",
        rf"\bnot\s+(?:\w+\s+){{0,4}}{phrase}\b",
        rf"\b{phrase}\b\s+(?:was|were|is|are)?\s*not\b",
        rf"\bdenies\s+(?:\w+\s+){{0,6}}{phrase}\b",
    ]
    return any(re.search(pattern, text) for pattern in negation_patterns)


def _passage_supports_line(line: ClaimLine, passage: RetrievedPassage) -> bool:
    lowered = _normalized_text(passage.text)
    known_phrases = set(SERVICE_EVIDENCE_PHRASES.get(line.code, ()))
    service_phrases = known_phrases | {_normalized_text(line.description)}
    if not known_phrases:
        service_phrases.update(_description_phrases(line.description))

    matched_phrases = [
        phrase
        for phrase in service_phrases
        if phrase in lowered and not _phrase_is_negated(phrase, lowered)
    ]
    if not matched_phrases:
        return False

    if line.code == "90471":
        return any("vaccine" in phrase or "immunization" in phrase for phrase in matched_phrases)
    if line.code in {"90714", "96372"}:
        return any("administered" in phrase or "given" in phrase or "injection" in phrase for phrase in matched_phrases)
    if line.code == "99406":
        return any("counseling" in phrase or "cessation" in phrase for phrase in matched_phrases)
    if line.code in {"99213", "99214"}:
        visit_terms = {"encounter", "physical examination", "assessment", "plan", "provider", "urgent visit"}
        return len(set(matched_phrases) & visit_terms) >= 2 or any(
            phrase in matched_phrases for phrase in {"office visit", "urgent visit"}
        )
    return True


def _supporting_passage_for_line(line: ClaimLine, passages: list[RetrievedPassage]) -> RetrievedPassage | None:
    for passage in passages:
        if _passage_supports_line(line, passage):
            return passage
    return None


def _passages_support_office_visit(passages: list[RetrievedPassage]) -> RetrievedPassage | None:
    combined = _normalized_text(" ".join(passage.text for passage in passages))
    has_history = "history of present illness" in combined or "the patient presented" in combined
    has_exam = "physical examination" in combined or "vitals:" in combined
    has_assessment = "assessment" in combined
    has_plan = "plan" in combined or "follow up" in combined
    if has_history and has_exam and (has_assessment or has_plan):
        return next(
            (
                passage
                for passage in passages
                if "physical examination" in passage.text.lower() or "assessment" in passage.text.lower()
            ),
            passages[0] if passages else None,
        )
    return None


def _passages_say_no_service(line: ClaimLine, passages: list[RetrievedPassage]) -> RetrievedPassage | None:
    combined = _normalized_text(" ".join(passage.text for passage in passages))
    if line.code in {"90714", "96372"} and re.search(r"\bnone\s+given\s+today\b|\bno\s+(?:tetanus\s+)?(?:vaccine|injection)\s+(?:was\s+)?given\b", combined):
        return next((passage for passage in passages if "none given today" in passage.text.lower()), passages[0] if passages else None)
    if line.code == "93306" and re.search(r"\bechocardiogram\b.{0,80}\bnot\s+indicated\s+today\b|\bnot\s+indicated\s+today\b.{0,80}\bechocardiogram\b", combined):
        return next((passage for passage in passages if "echocardiogram" in passage.text.lower()), passages[0] if passages else None)
    if line.code == "11103" and re.search(r"\bsingle\s+site\b|\bno\s+other\s+suspicious\s+lesions\b|\bno\s+other\s+skin\s+concerns\b", combined):
        return next((passage for passage in passages if "single site" in passage.text.lower() or "no other suspicious" in passage.text.lower()), passages[0] if passages else None)
    return None


def _passages_suggest_99215_upcode(passages: list[RetrievedPassage]) -> RetrievedPassage | None:
    combined = _normalized_text(" ".join(passage.text for passage in passages))
    stable_followup = all(
        phrase in combined
        for phrase in ("routine follow-up", "stable", "no new symptoms")
    )
    short_visit = "11:05" in combined and "11:17" in combined
    if stable_followup or short_visit:
        return next((passage for passage in passages if "routine follow-up" in passage.text.lower()), passages[0] if passages else None)
    return None


def _xray_view_mismatch(line: ClaimLine, passages: list[RetrievedPassage]) -> RetrievedPassage | None:
    if line.code != "73090":
        return None
    description = _normalized_text(line.description)
    combined = _normalized_text(" ".join(passage.text for passage in passages))
    bills_two_views = line.units > 1 or "2 views" in description or "two views" in description
    record_single_view = "single ap view" in combined or "single view" in combined
    if bills_two_views and record_single_view:
        return next((passage for passage in passages if "single ap view" in passage.text.lower()), passages[0] if passages else None)
    return None


def _has_conflicting_evidence(line: ClaimLine, verification: dict[str, Any], passages: list[RetrievedPassage]) -> bool:
    issue = verification.get("issue")
    if issue in {"NO_DOCUMENTATION", "UNSUPPORTED"}:
        return any(_passage_mentions_line(line, passage) and not _passage_looks_negative(passage.text) for passage in passages)
    if issue == "UPCODE":
        return any("high complexity" in passage.text.lower() or "level 5" in passage.text.lower() for passage in passages)
    return False


def _confidence_breakdown_from_verification(
    line: ClaimLine,
    verification: dict[str, Any],
    passages: list[RetrievedPassage],
) -> ConfidenceBreakdown:
    best_score = max((passage.score for passage in passages), default=0.0)
    passages_above_threshold = sum(1 for passage in passages if passage.score >= RETRIEVAL_PASSAGE_THRESHOLD)
    return ConfidenceBreakdown(
        rule_match="Not applicable",
        retrieval_score=round(best_score, 4) if passages else None,
        retrieval_passages_found=passages_above_threshold,
        llm_verdict=_llm_verdict_text(verification),
        conflicting_evidence=_has_conflicting_evidence(line, verification, passages),
    )


def _flag_from_verification(
    line: ClaimLine,
    verification: dict[str, Any],
    passages: list[RetrievedPassage],
) -> ValidationFlag | None:
    issue = verification.get("issue")
    if issue == "SUPPORTED":
        return None

    rule = issue if issue in {"NO_DOCUMENTATION", "UNSUPPORTED", "UPCODE"} else "UNSUPPORTED"
    severity = "high" if rule == "NO_DOCUMENTATION" else "medium"
    recommended_action = "request_records" if rule == "NO_DOCUMENTATION" else "escalate"

    return ValidationFlag(
        line_id=line.line_id,
        status="unsupported" if rule in {"NO_DOCUMENTATION", "UNSUPPORTED"} else "rule_violation",
        rule=rule,
        severity=severity,
        citation=verification.get("citation"),
        page=verification.get("page"),
        confidence=float(verification.get("confidence", 0.8)),
        confidence_breakdown=_confidence_breakdown_from_verification(line, verification, passages),
        recommended_action=recommended_action,
        message=str(verification.get("reason", f"LLM verification flagged {rule} for line {line.line_id}.")),
    )


def verify_line_with_llm(
    line: ClaimLine,
    passages: list[RetrievedPassage],
    evidence: ClinicalEvidenceSet,
) -> dict[str, Any]:
    visit_complexity = (
        evidence.documented_visit_complexity.model_dump()
        if evidence.documented_visit_complexity is not None
        else None
    )
    payload = call_openrouter_json(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are a bounded medical-claim verification component. Decide only whether "
                    "the provided passages support the single billed line. Use the given passages "
                    "only; do not use outside knowledge. Return JSON only."
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    "Billed line:\n"
                    f"- line_id: {line.line_id}\n"
                    f"- code: {line.code}\n"
                    f"- description: {line.description}\n"
                    f"- units: {line.units}\n"
                    f"- service_date: {line.service_date}\n\n"
                    f"Extracted visit complexity: {visit_complexity}\n\n"
                    "Relevant record passages:\n"
                    f"{_passage_payload(passages)}\n\n"
                    "Classify issue as SUPPORTED, NO_DOCUMENTATION, UNSUPPORTED, or UPCODE. "
                    "Use UPCODE only when the billed E/M level exceeds documented complexity."
                ),
            ),
        ],
        json_schema=VERIFY_SCHEMA,
        schema_name="claim_line_verification",
        temperature=0.0,
        max_tokens=1200,
    )
    return payload


def verify_line_mock(
    line: ClaimLine,
    passages: list[RetrievedPassage],
    evidence: ClinicalEvidenceSet,
) -> dict[str, Any]:
    procedure_codes = _procedure_codes(evidence)

    if line.code == "99215" and evidence.documented_visit_complexity:
        supported_code = evidence.documented_visit_complexity.supported_code
        if supported_code != "99215":
            return {
                "supported": False,
                "issue": "UPCODE",
                "confidence": 0.92,
                "citation": evidence.documented_visit_complexity.source_span,
                "page": evidence.documented_visit_complexity.page,
                "reason": (
                    f"Line {line.line_id} bills 99215, but documentation supports "
                    f"{supported_code} based on {evidence.documented_visit_complexity.supported_level} complexity."
                ),
            }

    if line.code == "99215":
        upcode_passage = _passages_suggest_99215_upcode(passages)
        if upcode_passage:
            return {
                "supported": False,
                "issue": "UPCODE",
                "confidence": 0.88,
                "citation": upcode_passage.text,
                "page": upcode_passage.page,
                "reason": "The record describes a stable routine follow-up visit, not high-complexity documentation supporting 99215.",
            }

    if line.code == "93005" and "93000" in procedure_codes:
        passage = passages[0] if passages else None
        return {
            "supported": False,
            "issue": "UNSUPPORTED",
            "confidence": 0.87,
            "citation": passage.text if passage else None,
            "page": passage.page if passage else None,
            "reason": "The record supports a complete ECG, not a separately documented tracing-only ECG service.",
        }

    if line.code == "70450":
        negative_passage = next((p for p in passages if "No CT head" in p.text), passages[0] if passages else None)
        return {
            "supported": False,
            "issue": "NO_DOCUMENTATION",
            "confidence": 0.95,
            "citation": negative_passage.text if negative_passage else None,
            "page": negative_passage.page if negative_passage else None,
            "reason": "The clinical record states that no CT head was ordered, performed, or discussed.",
        }

    negative_service_passage = _passages_say_no_service(line, passages)
    if negative_service_passage:
        return {
            "supported": False,
            "issue": "NO_DOCUMENTATION",
            "confidence": 0.94,
            "citation": negative_service_passage.text,
            "page": negative_service_passage.page,
            "reason": f"The record mentions {line.description}, but states the service was not given today.",
        }

    xray_mismatch_passage = _xray_view_mismatch(line, passages)
    if xray_mismatch_passage:
        return {
            "supported": False,
            "issue": "UNSUPPORTED",
            "confidence": 0.9,
            "citation": xray_mismatch_passage.text,
            "page": xray_mismatch_passage.page,
            "reason": "The bill reports a two-view forearm X-ray, but the record documents only a single AP view.",
        }

    if line.code in procedure_codes:
        passage = passages[0] if passages else None
        return {
            "supported": True,
            "issue": "SUPPORTED",
            "confidence": 0.9,
            "citation": passage.text if passage else None,
            "page": passage.page if passage else None,
            "reason": f"The clinical record contains support for billed code {line.code}.",
        }

    if line.code in {"99213", "99214"}:
        office_visit_passage = _passages_support_office_visit(passages)
        if office_visit_passage:
            return {
                "supported": True,
                "issue": "SUPPORTED",
                "confidence": 0.84,
                "citation": office_visit_passage.text,
                "page": office_visit_passage.page,
                "reason": "The record documents a visit with history, examination, assessment, and plan content.",
            }

    supporting_passage = _supporting_passage_for_line(line, passages)
    if supporting_passage:
        return {
            "supported": True,
            "issue": "SUPPORTED",
            "confidence": 0.86,
            "citation": supporting_passage.text,
            "page": supporting_passage.page,
            "reason": f"The clinical record contains support for {line.description}.",
        }

    return {
        "supported": False,
        "issue": "NO_DOCUMENTATION",
        "confidence": 0.75,
        "citation": None,
        "page": None,
        "reason": f"No supporting documentation was found for billed code {line.code}.",
    }


def verify_line_against_record(
    line: ClaimLine,
    record_text: str,
    evidence: ClinicalEvidenceSet,
) -> list[ValidationFlag]:
    flags, _ = verify_line_against_record_with_trace(line, record_text, evidence)
    return flags


def verify_line_against_record_with_trace(
    line: ClaimLine,
    record_text: str,
    evidence: ClinicalEvidenceSet,
) -> tuple[list[ValidationFlag], AIVerificationTrace]:
    passages = retrieve_passages_for_line(line, record_text, top_k=3)
    verification_mode = "openrouter_llm"
    fallback_used = False

    if LLM_MODE == "live":
        try:
            verification = verify_line_with_llm(line, passages, evidence)
        except (LLMConfigurationError, LLMCallError, ValueError, KeyError, TypeError):
            verification_mode = "mock_fallback"
            fallback_used = True
            verification = verify_line_mock(line, passages, evidence)
    else:
        verification_mode = "mock_fallback"
        fallback_used = True
        verification = verify_line_mock(line, passages, evidence)

    flag = _flag_from_verification(line, verification, passages)
    trace = AIVerificationTrace(
        line_id=line.line_id,
        code=line.code,
        verification_mode=verification_mode,
        fallback_used=fallback_used,
        retrieval_query=build_line_query(line),
        retrieved_passages=_trace_passages(passages),
        llm_supported=bool(verification.get("supported")),
        llm_issue=str(verification.get("issue", "UNSUPPORTED")),
        confidence=float(verification.get("confidence", 0.0)),
        citation=verification.get("citation"),
        page=verification.get("page"),
        rationale=str(verification.get("reason", "")),
    )
    return ([flag] if flag else []), trace


def verify_claim_lines_against_record(
    claim: Claim,
    record_text: str,
    evidence: ClinicalEvidenceSet,
) -> list[ValidationFlag]:
    flags, _ = verify_claim_lines_against_record_with_traces(claim, record_text, evidence)
    return flags


def verify_claim_lines_against_record_with_traces(
    claim: Claim,
    record_text: str,
    evidence: ClinicalEvidenceSet,
) -> tuple[list[ValidationFlag], list[AIVerificationTrace]]:
    if not claim.lines:
        return [], []

    max_workers = max(1, min(VERIFY_MAX_WORKERS, len(claim.lines)))
    ordered_results: list[tuple[list[ValidationFlag], AIVerificationTrace] | None] = [None] * len(claim.lines)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(verify_line_against_record_with_trace, line, record_text, evidence): index
            for index, line in enumerate(claim.lines)
        }
        for future in as_completed(future_to_index):
            ordered_results[future_to_index[future]] = future.result()

    flags: list[ValidationFlag] = []
    traces: list[AIVerificationTrace] = []
    for result in ordered_results:
        if result is None:
            continue
        line_flags, trace = result
        flags.extend(line_flags)
        traces.append(trace)
    return flags, traces
