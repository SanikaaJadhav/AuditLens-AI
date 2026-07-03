from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.config import ENABLE_LLM_NARRATIVE, LLM_MODE
from app.pipeline.llm_client import LLMCallError, LLMConfigurationError, LLMMessage, call_openrouter_json
from app.pipeline.reference_data import load_medical_necessity_rules
from app.schemas import (
    AIVerificationTrace,
    Claim,
    ClaimReport,
    ClinicalEvidenceSet,
    DocumentationGap,
    LineValidationResult,
    ProcessingMetadata,
    RecommendedAction,
    ReportMetrics,
    ValidationFlag,
)


NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_narrative": {
            "type": "string",
            "description": "A 3-5 sentence professional clinical audit summary.",
        }
    },
    "required": ["claim_narrative"],
}


RULE_PLAIN_LANGUAGE = {
    "NO_DOCUMENTATION": "there is no clinical documentation supporting the billed service",
    "UNSUPPORTED": "the record supports a different service than the one billed",
    "NCCI_BUNDLE": "a service appears separately billed even though it is already included in another billed procedure",
    "UPCODE": "the visit level appears higher than the documented clinical complexity supports",
    "MEDICAL_NECESSITY": "the required diagnosis support is not documented for the procedure",
    "MUE_UNITS": "the billed units exceed the allowed quantity for the service",
    "DUPLICATE": "the same service appears billed more than once",
}


ACTION_PLAIN_LANGUAGE = {
    "pay": "pay the claim",
    "request_records": "request additional records",
    "deny_line": "deny the affected line",
    "escalate": "escalate the claim for senior review",
}


DOCUMENTATION_GAP_RULES = {"NO_DOCUMENTATION", "UNSUPPORTED", "MEDICAL_NECESSITY", "UPCODE"}
GAP_PASSAGE_THRESHOLD = 0.05


def recommended_action_for_flags(flags: list[ValidationFlag]) -> RecommendedAction:
    if any(flag.severity == "high" for flag in flags):
        return "escalate"
    if any(flag.recommended_action == "deny_line" for flag in flags):
        return "deny_line"
    if any(flag.severity == "medium" for flag in flags):
        return "request_records"
    if flags:
        return "request_records"
    return "pay"


def overall_recommended_action(flags: list[ValidationFlag], risk_score: int) -> RecommendedAction:
    if risk_score >= 70 or any(flag.severity == "high" for flag in flags):
        return "escalate"
    if any(flag.recommended_action == "deny_line" for flag in flags):
        return "deny_line"
    if flags:
        return "request_records"
    return "pay"


def calculate_risk_score(flags: list[ValidationFlag], total_charge: float, dollars_at_risk: float) -> int:
    severity_points = {"high": 18, "medium": 9, "low": 4, "none": 0}
    raw_score = sum(severity_points[flag.severity] for flag in flags)
    unique_rules = {flag.rule for flag in flags if flag.rule}
    diversity_component = min(18, len(unique_rules) * 3)
    dollar_component = int((dollars_at_risk / total_charge) * 30) if total_charge else 0
    return min(100, raw_score + diversity_component + dollar_component)


def status_for_line(flags: list[ValidationFlag]) -> str:
    if not flags:
        return "supported"
    statuses = {flag.status for flag in flags}
    if len(flags) > 1:
        return "multiple_issues"
    if "unsupported" in statuses:
        return "unsupported"
    if "rule_violation" in statuses:
        return "rule_violation"
    return "needs_review"


def _highest_priority_flag(flags: list[ValidationFlag]) -> ValidationFlag | None:
    if not flags:
        return None
    severity_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    return max(flags, key=lambda flag: (severity_rank.get(flag.severity, 0), flag.confidence))


def key_finding_summary_for_line(flags: list[ValidationFlag]) -> str:
    if not flags:
        return "No issues found — documentation supports this service."

    priority_flag = _highest_priority_flag(flags)
    if priority_flag is None:
        return "Review needed before payment."

    summaries = {
        "NO_DOCUMENTATION": "No record support was found for this billed service.",
        "UNSUPPORTED": "Record supports a different service than what was billed.",
        "MEDICAL_NECESSITY": "Required diagnosis support is missing from the record.",
        "UPCODE": "Visit complexity does not support the billed level.",
        "NCCI_BUNDLE": "Service appears included in another billed procedure.",
        "MUE_UNITS": "Billed units exceed the allowed daily quantity.",
        "DUPLICATE": "This service appears billed more than once.",
    }
    return summaries.get(priority_flag.rule or "", "Review needed before payment.")


def build_metrics(
    claim: Claim,
    flags: list[ValidationFlag],
    dollars_at_risk_by_rule: dict[str, float],
) -> ReportMetrics:
    flags_by_line: dict[str, list[ValidationFlag]] = defaultdict(list)
    for flag in flags:
        flags_by_line[flag.line_id].append(flag)

    rule_counts = Counter(flag.rule or "OTHER" for flag in flags)
    severity_counts = Counter(flag.severity for flag in flags)
    top_risk_lines = [
        line_id
        for line_id, line_flags in sorted(
            flags_by_line.items(),
            key=lambda item: (
                sum({"high": 3, "medium": 2, "low": 1, "none": 0}[flag.severity] for flag in item[1]),
                len(item[1]),
            ),
            reverse=True,
        )
    ][:5]

    flagged_line_ids = set(flags_by_line)
    return ReportMetrics(
        total_lines=len(claim.lines),
        supported_lines=len(claim.lines) - len(flagged_line_ids),
        flagged_lines=len(flagged_line_ids),
        high_severity_flags=severity_counts.get("high", 0),
        medium_severity_flags=severity_counts.get("medium", 0),
        low_severity_flags=severity_counts.get("low", 0),
        flag_counts_by_rule=dict(sorted(rule_counts.items())),
        flag_counts_by_severity=dict(sorted(severity_counts.items())),
        dollars_at_risk_by_rule={rule: round(amount, 2) for rule, amount in sorted(dollars_at_risk_by_rule.items())},
        top_risk_lines=top_risk_lines,
    )


def build_summary(
    flags: list[ValidationFlag],
    metrics: ReportMetrics,
    dollars_at_risk: float,
    risk_score: int,
    action: RecommendedAction,
) -> str:
    if not flags:
        return "AuditLens found no review findings in this claim. Recommended action: pay."

    priority_rules = [
        "NO_DOCUMENTATION",
        "UNSUPPORTED",
        "NCCI_BUNDLE",
        "UPCODE",
        "MEDICAL_NECESSITY",
        "MUE_UNITS",
        "DUPLICATE",
    ]
    present_rules = [rule for rule in priority_rules if metrics.flag_counts_by_rule.get(rule)]
    issue_text = ", ".join(rule.replace("_", " ").lower() for rule in present_rules[:3])

    return (
        f"AuditLens found {len(flags)} review finding(s) across {metrics.flagged_lines} of "
        f"{metrics.total_lines} billed line(s). Priority issues include {issue_text}. "
        f"Estimated dollars at risk: ${dollars_at_risk:,.2f}. Risk score: {risk_score}/100. "
        f"Recommended next action: {action.replace('_', ' ')}."
    )


def _currency(value: float) -> str:
    return f"${value:,.2f}"


def _flag_priority(flag: ValidationFlag, line_charge: dict[str, float]) -> tuple[int, float, float]:
    severity_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    return (
        severity_rank.get(flag.severity, 0),
        line_charge.get(flag.line_id, 0.0),
        flag.confidence,
    )


def _line_lookup(claim: Claim) -> dict[str, dict[str, Any]]:
    return {
        line.line_id: {
            "code": line.code,
            "description": line.description,
            "charge": line.charge,
            "units": line.units,
        }
        for line in claim.lines
    }


def _finding_payload(claim: Claim, flags: list[ValidationFlag]) -> list[dict[str, Any]]:
    lines = _line_lookup(claim)
    return [
        {
            "line_id": flag.line_id,
            "code": lines.get(flag.line_id, {}).get("code", "unknown"),
            "description": lines.get(flag.line_id, {}).get("description", "unknown service"),
            "charge": lines.get(flag.line_id, {}).get("charge", 0.0),
            "units": lines.get(flag.line_id, {}).get("units", 1),
            "rule": flag.rule,
            "plain_issue": RULE_PLAIN_LANGUAGE.get(flag.rule or "", flag.message),
            "severity": flag.severity,
            "confidence": flag.confidence,
            "recommended_action": flag.recommended_action,
            "auditor_reason": flag.message,
        }
        for flag in flags
    ]


def _fallback_claim_narrative(
    claim: Claim,
    flags: list[ValidationFlag],
    dollars_at_risk: float,
    risk_score: int,
    action: RecommendedAction,
) -> str:
    if not flags:
        return (
            f"Claim {claim.claim_id} does not show material documentation or billing-rule concerns across "
            f"{len(claim.lines)} billed line(s). The submitted services total {_currency(sum(line.charge for line in claim.lines))}, "
            "and the available record support is consistent with payment. Recommended next action is to pay the claim because "
            "there is no identified dollar exposure requiring additional review."
        )

    line_charge = {line.line_id: line.charge for line in claim.lines}
    line_detail = _line_lookup(claim)
    priority_flag = max(flags, key=lambda flag: _flag_priority(flag, line_charge))
    priority_line = line_detail.get(priority_flag.line_id, {})
    priority_code = priority_line.get("code", "unknown")
    priority_charge = float(priority_line.get("charge", 0.0))
    priority_name = priority_line.get("description", "the billed service")
    priority_line_flags = [flag for flag in flags if flag.line_id == priority_flag.line_id]
    priority_issues = []
    for flag in priority_line_flags:
        issue = RULE_PLAIN_LANGUAGE.get(flag.rule or "", flag.message)
        if issue not in priority_issues:
            priority_issues.append(issue)
    priority_issue_text = " and ".join(priority_issues[:2])
    action_text = ACTION_PLAIN_LANGUAGE.get(action, action.replace("_", " "))

    other_flagged_lines = len({flag.line_id for flag in flags})
    return (
        f"The priority concern is {priority_name} code {priority_code} for {_currency(priority_charge)}, because {priority_issue_text}. "
        f"The broader review shows payment risk on {other_flagged_lines} of {len(claim.lines)} billed line(s), with estimated exposure of "
        f"{_currency(dollars_at_risk)} against a {_currency(sum(line.charge for line in claim.lines))} claim and a risk score of {risk_score}/100. "
        "In plain terms, the record does not give the reviewer enough support to release every billed service as submitted. "
        f"Recommended next action is to {action_text} because the current documentation creates material payment exposure."
    )


def build_claim_narrative(
    claim: Claim,
    flags: list[ValidationFlag],
    metrics: ReportMetrics,
    dollars_at_risk: float,
    risk_score: int,
    action: RecommendedAction,
) -> str:
    fallback = _fallback_claim_narrative(claim, flags, dollars_at_risk, risk_score, action)
    if LLM_MODE != "live" or not ENABLE_LLM_NARRATIVE:
        return fallback

    findings = _finding_payload(claim, flags)
    line_charge = {line.line_id: line.charge for line in claim.lines}
    priority_flag = max(flags, key=lambda flag: _flag_priority(flag, line_charge)) if flags else None
    priority_line = _line_lookup(claim).get(priority_flag.line_id, {}) if priority_flag else {}
    priority_finding = None
    if priority_flag:
        related_priority_issues = [
            RULE_PLAIN_LANGUAGE.get(flag.rule or "", flag.message)
            for flag in flags
            if flag.line_id == priority_flag.line_id
        ]
        priority_finding = {
            "line_id": priority_flag.line_id,
            "code": priority_line.get("code"),
            "description": priority_line.get("description"),
            "charge": priority_line.get("charge"),
            "rule": priority_flag.rule,
            "plain_issue": RULE_PLAIN_LANGUAGE.get(priority_flag.rule or "", priority_flag.message),
            "related_plain_issues_for_same_line": related_priority_issues,
            "severity": priority_flag.severity,
            "recommended_action": priority_flag.recommended_action,
        }

    try:
        payload = call_openrouter_json(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "You write clinical audit summaries for healthcare payment integrity reviewers. "
                        "Return JSON only."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        "Write a 3-5 sentence clinical audit summary in the voice of an experienced "
                        "healthcare payment integrity auditor. The narrative must open by identifying "
                        "the single highest-dollar or most severe finding by name and explain why it is "
                        "the priority concern. Reference specific procedure codes and dollar amounts inline. "
                        "Explain what is wrong in plain English without using jargon acronyms like NCCI or "
                        "MUE; spell out what the rule violation actually means. End with a clear recommended "
                        "next action and the business justification for it. Do not list findings. Do not use "
                        "bullet points. Write in flowing professional prose as if briefing a senior auditor "
                        "who has 30 seconds to read this before making a payment decision.\n\n"
                        f"Claim ID: {claim.claim_id}\n"
                        f"Patient: {claim.patient.name}\n"
                        f"Total charge: {_currency(sum(line.charge for line in claim.lines))}\n"
                        f"Dollars at risk: {_currency(dollars_at_risk)}\n"
                        f"Risk score: {risk_score}/100\n"
                        f"Recommended overall action: {ACTION_PLAIN_LANGUAGE.get(action, action)}\n"
                        f"Metrics: {metrics.model_dump()}\n"
                        f"Priority finding: {priority_finding}\n"
                        f"All findings: {findings}\n"
                    ),
                ),
            ],
            json_schema=NARRATIVE_SCHEMA,
            schema_name="claim_audit_narrative",
            temperature=0.2,
            max_tokens=900,
        )
        narrative = str(payload.get("claim_narrative", "")).strip()
        return narrative or fallback
    except (LLMConfigurationError, LLMCallError, ValueError, KeyError, TypeError):
        return fallback


def _allowed_diagnosis_text(code: str) -> str:
    medical_rules = load_medical_necessity_rules()
    rule = medical_rules.get(code)
    if not rule:
        return ""
    allowed_codes = ", ".join(sorted(rule.allowed_diagnosis_codes))
    return (
        f"Documentation should show that code {code} was ordered or performed and tied to a supporting "
        f"diagnosis such as {allowed_codes}. {rule.rule_description}"
    )


def _required_documentation(line_description: str, code: str, line_flags: list[ValidationFlag]) -> str:
    rules = {flag.rule for flag in line_flags}
    if "UPCODE" in rules:
        return (
            f"Documentation should support the billed visit complexity for {line_description} code {code}, "
            "including the clinical decision making, problem severity, data reviewed, and risk level needed "
            "for the submitted service level."
        )

    necessity_text = _allowed_diagnosis_text(code)
    if necessity_text:
        return necessity_text

    return (
        f"Documentation should clearly show that {line_description} code {code} was ordered, performed, "
        "clinically relevant to the visit, and attributable to this service date."
    )


def _gap_type(line_flags: list[ValidationFlag], relevant_passages: list[str]) -> str:
    rules = {flag.rule for flag in line_flags}
    if "UPCODE" in rules:
        return "Complexity gap"
    if "NO_DOCUMENTATION" in rules or not relevant_passages:
        return "Complete gap"
    return "Partial gap"


def _present_evidence(line_flags: list[ValidationFlag], trace: AIVerificationTrace | None) -> str:
    evidence_snippets: list[str] = []
    for flag in line_flags:
        if flag.citation and flag.citation not in evidence_snippets:
            evidence_snippets.append(flag.citation)

    if trace is not None:
        for passage in trace.retrieved_passages:
            if passage.score >= GAP_PASSAGE_THRESHOLD and passage.text not in evidence_snippets:
                evidence_snippets.append(passage.text)

    if not evidence_snippets:
        return "Nothing found"

    first = evidence_snippets[0]
    if len(first) > 260:
        first = f"{first[:257].rstrip()}..."
    return first


def generate_documentation_gaps(
    claim: Claim,
    flags: list[ValidationFlag],
    ai_traces: list[AIVerificationTrace] | None = None,
) -> list[DocumentationGap]:
    traces_by_line = {trace.line_id: trace for trace in ai_traces or []}
    flags_by_line: dict[str, list[ValidationFlag]] = defaultdict(list)
    for flag in flags:
        if flag.rule in DOCUMENTATION_GAP_RULES:
            flags_by_line[flag.line_id].append(flag)

    gaps: list[DocumentationGap] = []
    for line in claim.lines:
        line_flags = flags_by_line.get(line.line_id, [])
        if not line_flags:
            continue

        trace = traces_by_line.get(line.line_id)
        relevant_passages = [
            passage.text
            for passage in (trace.retrieved_passages if trace else [])
            if passage.score >= GAP_PASSAGE_THRESHOLD
        ]
        gaps.append(
            DocumentationGap(
                service_name=line.description,
                cpt_code=line.code,
                required_documentation=_required_documentation(line.description, line.code, line_flags),
                what_is_present=_present_evidence(line_flags, trace),
                gap_type=_gap_type(line_flags, relevant_passages),
                dollar_amount=round(line.charge, 2),
            )
        )

    return gaps


def build_claim_report(
    claim: Claim,
    evidence: ClinicalEvidenceSet | None,
    flags: list[ValidationFlag],
    processing_metadata: ProcessingMetadata | None = None,
    ai_traces: list[AIVerificationTrace] | None = None,
) -> ClaimReport:
    """Build the API report envelope and keep unflagged lines in review."""
    flags_by_line: dict[str, list[ValidationFlag]] = {line.line_id: [] for line in claim.lines}
    for flag in flags:
        flags_by_line.setdefault(flag.line_id, []).append(flag)

    line_results = []
    for line in claim.lines:
        line_flags = flags_by_line.get(line.line_id, [])
        line_results.append(
            LineValidationResult(
                line_id=line.line_id,
                code=line.code,
                status=status_for_line(line_flags),
                flags=line_flags,
                recommended_action=recommended_action_for_flags(line_flags),
                key_finding_summary=key_finding_summary_for_line(line_flags),
            )
        )

    total_charge = sum(line.charge for line in claim.lines)
    flagged_line_ids = {flag.line_id for flag in flags}
    dollars_at_risk = sum(line.charge for line in claim.lines if line.line_id in flagged_line_ids)
    line_charge = {line.line_id: line.charge for line in claim.lines}
    dollars_at_risk_by_rule: dict[str, float] = defaultdict(float)
    for flag in flags:
        dollars_at_risk_by_rule[flag.rule or "OTHER"] += line_charge.get(flag.line_id, 0.0)

    risk_score = calculate_risk_score(flags, total_charge, dollars_at_risk)
    metrics = build_metrics(claim, flags, dollars_at_risk_by_rule)
    recommended_action = overall_recommended_action(flags, risk_score)
    summary = build_summary(flags, metrics, round(dollars_at_risk, 2), risk_score, recommended_action)
    claim_narrative = build_claim_narrative(
        claim,
        flags,
        metrics,
        round(dollars_at_risk, 2),
        risk_score,
        recommended_action,
    )
    line_action = {result.line_id: result.recommended_action for result in line_results}
    recommended_payment = sum(line.charge for line in claim.lines if line_action.get(line.line_id) == "pay")
    potential_savings = total_charge - recommended_payment
    savings_percentage = round((potential_savings / total_charge) * 100, 1) if total_charge else 0.0
    documentation_gaps = generate_documentation_gaps(claim, flags, ai_traces)

    return ClaimReport(
        claim_id=claim.claim_id,
        generated_at=datetime.utcnow(),
        claim=claim,
        evidence=evidence,
        line_results=line_results,
        flags=flags,
        total_charge=round(total_charge, 2),
        dollars_at_risk=round(dollars_at_risk, 2),
        total_billed=round(total_charge, 2),
        recommended_payment=round(recommended_payment, 2),
        potential_savings=round(potential_savings, 2),
        savings_percentage=savings_percentage,
        risk_score=risk_score,
        recommended_action=recommended_action,
        metrics=metrics,
        summary=summary,
        claim_narrative=claim_narrative,
        documentation_gaps=documentation_gaps,
        processing_metadata=processing_metadata,
        ai_traces=ai_traces or [],
    )
