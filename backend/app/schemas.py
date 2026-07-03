from datetime import date as DateType
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


RuleName = Literal[
    "NCCI_BUNDLE",
    "MUE_UNITS",
    "DUPLICATE",
    "NO_DOCUMENTATION",
    "UNSUPPORTED",
    "MEDICAL_NECESSITY",
    "UPCODE",
]
LineStatus = Literal["supported", "unsupported", "rule_violation", "needs_review", "multiple_issues"]
Severity = Literal["none", "low", "medium", "high"]
RecommendedAction = Literal["pay", "request_records", "deny_line", "escalate"]
EvidenceType = Literal["diagnosis", "procedure", "visit_complexity", "note_span"]
DocumentInputType = Literal["text", "pdf", "image", "unknown"]
VerificationMode = Literal["openrouter_llm", "openrouter_batch", "mock_fallback"]
DocumentationGapType = Literal["Complete gap", "Partial gap", "Complexity gap"]


class Patient(BaseModel):
    patient_id: str
    name: str
    date_of_birth: DateType | None = None


class Provider(BaseModel):
    provider_id: str
    name: str
    specialty: str | None = None


class ClaimLine(BaseModel):
    line_id: str
    code: str
    code_system: str
    description: str
    units: int = Field(ge=1)
    charge: float = Field(ge=0)
    service_date: DateType | None = None
    modifiers: list[str] = Field(default_factory=list)
    place_of_service: str | None = None


class Claim(BaseModel):
    claim_id: str
    patient: Patient
    provider: Provider
    service_date: DateType
    lines: list[ClaimLine]


class DocumentedEvidence(BaseModel):
    evidence_id: str
    type: EvidenceType
    code: str | None = None
    description: str
    date: DateType | None = None
    source_span: str
    page: int | None = Field(default=None, ge=1)


class VisitComplexityEvidence(BaseModel):
    supported_level: Literal["minimal", "low", "moderate", "high"]
    supported_code: str
    source_span: str
    page: int | None = Field(default=None, ge=1)


class ClinicalEvidenceSet(BaseModel):
    claim_id: str
    patient_id: str
    documented_diagnoses: list[DocumentedEvidence] = Field(default_factory=list)
    documented_procedures: list[DocumentedEvidence] = Field(default_factory=list)
    documented_visit_complexity: VisitComplexityEvidence | None = None


class ConfidenceBreakdown(BaseModel):
    rule_match: str
    retrieval_score: float | None = None
    retrieval_passages_found: int = Field(default=0, ge=0)
    llm_verdict: str
    conflicting_evidence: bool = False


class ValidationFlag(BaseModel):
    line_id: str
    status: LineStatus
    rule: RuleName | None = None
    severity: Severity
    citation: str | None = None
    page: int | None = Field(default=None, ge=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence_breakdown: ConfidenceBreakdown | None = None
    recommended_action: RecommendedAction
    message: str


class LineValidationResult(BaseModel):
    line_id: str
    code: str
    status: LineStatus
    flags: list[ValidationFlag] = Field(default_factory=list)
    recommended_action: RecommendedAction
    key_finding_summary: str


class RetrievedEvidencePassage(BaseModel):
    rank: int = Field(ge=1)
    text: str
    page: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0.0)


class AIVerificationTrace(BaseModel):
    line_id: str
    code: str
    verification_mode: VerificationMode
    fallback_used: bool = False
    retrieval_query: str
    retrieved_passages: list[RetrievedEvidencePassage] = Field(default_factory=list)
    llm_supported: bool
    llm_issue: str
    confidence: float = Field(ge=0.0, le=1.0)
    citation: str | None = None
    page: int | None = Field(default=None, ge=1)
    rationale: str
    guardrail_rules: list[str] = Field(default_factory=list)


class ProcessingMetadata(BaseModel):
    bill_filename: str | None = None
    record_filename: str | None = None
    record_input_type: DocumentInputType = "unknown"
    ocr_used: bool = False
    ocr_engine: str | None = None
    extraction_method: str
    verification_method: str
    rule_guardrails_applied: list[str] = Field(default_factory=list)


class ReportMetrics(BaseModel):
    total_lines: int
    supported_lines: int
    flagged_lines: int
    high_severity_flags: int
    medium_severity_flags: int
    low_severity_flags: int
    flag_counts_by_rule: dict[str, int] = Field(default_factory=dict)
    flag_counts_by_severity: dict[str, int] = Field(default_factory=dict)
    dollars_at_risk_by_rule: dict[str, float] = Field(default_factory=dict)
    top_risk_lines: list[str] = Field(default_factory=list)


class DocumentationGap(BaseModel):
    service_name: str
    cpt_code: str
    required_documentation: str
    what_is_present: str
    gap_type: DocumentationGapType
    dollar_amount: float


class ClaimReport(BaseModel):
    claim_id: str
    generated_at: datetime
    claim: Claim
    evidence: ClinicalEvidenceSet | None = None
    line_results: list[LineValidationResult]
    flags: list[ValidationFlag]
    total_charge: float
    dollars_at_risk: float
    total_billed: float = 0.0
    recommended_payment: float = 0.0
    potential_savings: float = 0.0
    savings_percentage: float = 0.0
    risk_score: int = Field(ge=0, le=100)
    recommended_action: RecommendedAction
    metrics: ReportMetrics
    summary: str
    claim_narrative: str | None = None
    documentation_gaps: list[DocumentationGap] = Field(default_factory=list)
    processing_metadata: ProcessingMetadata | None = None
    ai_traces: list[AIVerificationTrace] = Field(default_factory=list)


class ReviewerAction(BaseModel):
    claim_id: str
    line_id: str
    action: RecommendedAction
    reviewer_note: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UploadResponse(BaseModel):
    claim_id: str
    status: Literal["accepted", "analyzed", "failed"]
    message: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


class ChatResponse(BaseModel):
    claim_id: str
    answer: str
    citation: str | None = None
    page: int | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EvaluationResults(BaseModel):
    total_cases: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int = 0
    precision: float
    recall: float
    f1_score: float
    false_alarm_rate: float = 0.0
    baseline_precision: float
    baseline_recall: float
    baseline_f1: float
    baseline_false_alarm_rate: float = 0.0
    false_alarm_reduction_percentage: float


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
