import json
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    APP_NAME,
    APP_VERSION,
    EVAL_DIR,
    MAX_UPLOAD_BYTES,
    SAMPLE_CLAIM_ID,
    SAMPLE_NOTE_PDF,
    SAMPLE_NOTE_SCANNED,
    SAMPLE_NOTE_TEXT,
)
from app.db import initialize_database, load_claim_artifacts, save_claim_artifacts, save_reviewer_action
from app.pipeline.aggregate import build_claim_report
from app.pipeline.bill_parser import BillParseError, parse_bill_file
from app.pipeline.chat import answer_question_from_record
from app.pipeline.extraction import extract_clinical_evidence, load_claim_from_json, load_clinical_evidence_from_json
from app.pipeline.ocr import DocumentTextExtractionError, extract_text_from_document
from app.pipeline.rules import run_medical_necessity_checks, run_rulebook_checks
from app.pipeline.verify import verify_claim_lines_against_record_with_traces
from app.schemas import (
    AIVerificationTrace,
    ChatRequest,
    ChatResponse,
    Claim,
    ClaimReport,
    ClinicalEvidenceSet,
    EvaluationResults,
    ProcessingMetadata,
    HealthResponse,
    ReviewerAction,
    UploadResponse,
)

SAMPLE_RECORD_SOURCES = {
    "text": SAMPLE_NOTE_TEXT,
    "pdf": SAMPLE_NOTE_PDF,
    "scanned": SAMPLE_NOTE_SCANNED,
}
ALLOWED_BILL_SUFFIXES = {".json", ".csv", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
ALLOWED_RECORD_SUFFIXES = {".txt", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

RECORD_TEXT_CACHE: dict[str, str] = {}
EVIDENCE_CACHE: dict[str, ClinicalEvidenceSet] = {}
REPORT_CACHE: dict[str, ClaimReport] = {}

RULE_GUARDRAILS = ["DUPLICATE", "MUE_UNITS", "NCCI_BUNDLE", "MEDICAL_NECESSITY"]
EVALUATION_RESULTS_PATH = EVAL_DIR / "results" / "app_evaluation_metrics.json"


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Clinical claim validation workbench for the AuditLens AI prototype.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=f"{APP_NAME} backend", version=APP_VERSION)


@app.get("/evaluation/results", response_model=EvaluationResults)
def get_evaluation_results() -> EvaluationResults:
    if not EVALUATION_RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="Evaluation metrics fixture was not found.")
    return EvaluationResults.model_validate(json.loads(EVALUATION_RESULTS_PATH.read_text(encoding="utf-8")))


@app.get("/sample/claim", response_model=Claim, include_in_schema=False)
def get_sample_claim() -> Claim:
    return load_claim_from_json()


@app.get("/sample/evidence", response_model=ClinicalEvidenceSet, include_in_schema=False)
def get_sample_evidence() -> ClinicalEvidenceSet:
    return extract_clinical_evidence(SAMPLE_NOTE_TEXT)


@app.get("/sample/evidence/from-pdf", response_model=ClinicalEvidenceSet, include_in_schema=False)
def get_sample_evidence_from_pdf() -> ClinicalEvidenceSet:
    return extract_clinical_evidence(SAMPLE_NOTE_PDF)


@app.get("/sample/evidence/from-scanned", response_model=ClinicalEvidenceSet, include_in_schema=False)
def get_sample_evidence_from_scanned() -> ClinicalEvidenceSet:
    return extract_clinical_evidence(SAMPLE_NOTE_SCANNED)


@app.get("/sample/evidence/expected", response_model=ClinicalEvidenceSet, include_in_schema=False)
def get_expected_sample_evidence() -> ClinicalEvidenceSet:
    return load_clinical_evidence_from_json()


@app.get("/sample/record/text", include_in_schema=False)
def get_sample_record_text() -> dict[str, str]:
    return {"source": SAMPLE_NOTE_TEXT.name, "text": extract_text_from_document(SAMPLE_NOTE_TEXT)}


@app.get("/sample/record/ocr", include_in_schema=False)
def get_sample_record_ocr_text() -> dict[str, str]:
    return {"source": SAMPLE_NOTE_SCANNED.name, "text": extract_text_from_document(SAMPLE_NOTE_SCANNED)}


def _analyze_sample_claim_from_source(record_source: str = "text") -> ClaimReport:
    record_path = SAMPLE_RECORD_SOURCES.get(record_source)
    if record_path is None:
        supported_sources = ", ".join(sorted(SAMPLE_RECORD_SOURCES))
        raise HTTPException(status_code=400, detail=f"Unsupported sample source. Use one of: {supported_sources}.")

    claim = load_claim_from_json()
    record_text = extract_text_from_document(record_path)
    evidence = extract_clinical_evidence(record_path)
    verification_flags, ai_traces = verify_claim_lines_against_record_with_traces(claim, record_text, evidence)
    flags = (
        verification_flags
        + run_rulebook_checks(claim)
        + run_medical_necessity_checks(claim, evidence)
    )
    _attach_guardrail_rules(ai_traces, flags)
    report = build_claim_report(
        claim=claim,
        evidence=evidence,
        flags=flags,
        processing_metadata=_processing_metadata_for_files(
            bill_filename="sample claim",
            record_path=record_path,
            ai_traces=ai_traces,
        ),
        ai_traces=ai_traces,
    )
    _cache_and_persist_claim_artifacts(report, record_text, evidence)
    return report

def _build_report_from_claim_and_record(
    claim: Claim,
    record_path: Path,
    bill_filename: str | None = None,
    record_filename: str | None = None,
) -> ClaimReport:
    try:
        record_text = extract_text_from_document(record_path)
        evidence = extract_clinical_evidence(record_path)
    except DocumentTextExtractionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    verification_flags, ai_traces = verify_claim_lines_against_record_with_traces(claim, record_text, evidence)
    flags = (
        verification_flags
        + run_rulebook_checks(claim)
        + run_medical_necessity_checks(claim, evidence)
    )
    _attach_guardrail_rules(ai_traces, flags)
    report = build_claim_report(
        claim=claim,
        evidence=evidence,
        flags=flags,
        processing_metadata=_processing_metadata_for_files(
            bill_filename=bill_filename,
            record_path=record_path,
            record_filename=record_filename,
            ai_traces=ai_traces,
        ),
        ai_traces=ai_traces,
    )
    _cache_and_persist_claim_artifacts(report, record_text, evidence)
    return report


def _cache_and_persist_claim_artifacts(
    report: ClaimReport,
    record_text: str,
    evidence: ClinicalEvidenceSet | None,
) -> None:
    RECORD_TEXT_CACHE[report.claim_id] = record_text
    if evidence is not None:
        EVIDENCE_CACHE[report.claim_id] = evidence
    REPORT_CACHE[report.claim_id] = report
    save_claim_artifacts(report, record_text, evidence)


def _record_input_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return "text"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return "image"
    return "unknown"


def _processing_metadata_for_files(
    bill_filename: str | None,
    record_path: Path,
    ai_traces: list[AIVerificationTrace],
    record_filename: str | None = None,
) -> ProcessingMetadata:
    input_type = _record_input_type(record_path)
    ocr_used = input_type == "image"
    verification_method = "OpenRouter LLM with deterministic rule guardrails"
    if any(trace.fallback_used for trace in ai_traces):
        verification_method = "Fallback verifier with deterministic rule guardrails"
    return ProcessingMetadata(
        bill_filename=bill_filename,
        record_filename=record_filename or record_path.name,
        record_input_type=input_type,
        ocr_used=ocr_used,
        ocr_engine="Tesseract" if ocr_used and shutil.which("tesseract") else ("sample text fallback" if ocr_used else None),
        extraction_method="LLM schema extraction with deterministic fallback",
        verification_method=verification_method,
        rule_guardrails_applied=RULE_GUARDRAILS,
    )


def _attach_guardrail_rules(ai_traces: list[AIVerificationTrace], flags: list) -> None:
    guardrails_by_line: dict[str, list[str]] = {}
    for flag in flags:
        if flag.rule in RULE_GUARDRAILS:
            guardrails_by_line.setdefault(flag.line_id, []).append(flag.rule)
    for trace in ai_traces:
        trace.guardrail_rules = sorted(set(guardrails_by_line.get(trace.line_id, [])))


def _claim_from_uploaded_bill(content: bytes, filename: str | None) -> Claim:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_BILL_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Upload the bill as JSON, CSV, XLSX, PDF, PNG, JPG, TIF, or TIFF. "
                "Tabular/OCR exports should include claim, patient, provider, service date, code, "
                "description, units, and charge columns."
            ),
        )

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Uploaded bill exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    try:
        return parse_bill_file(content, filename)
    except BillParseError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



async def _uploaded_record_to_temp_file(record: UploadFile) -> Path:
    suffix = Path(record.filename or "").suffix.lower()
    if suffix not in ALLOWED_RECORD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Current prototype accepts record files as TXT, PDF, PNG, JPG, TIF, or TIFF.",
        )

    content = await record.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded clinical record is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded clinical record exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(record.filename or "record").stem)[:80] or "record"
    with tempfile.NamedTemporaryFile(delete=False, prefix=f"{safe_stem}_", suffix=suffix) as temp_file:
        temp_file.write(content)
        return Path(temp_file.name)


def _report_or_404(claim_id: str) -> ClaimReport:
    report = REPORT_CACHE.get(claim_id)
    if report is not None:
        return report

    stored = load_claim_artifacts(claim_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="No report found for this claim. Run claim review first.")

    RECORD_TEXT_CACHE[claim_id] = stored.record_text
    if stored.evidence is not None:
        EVIDENCE_CACHE[claim_id] = stored.evidence
    REPORT_CACHE[claim_id] = stored.report
    return stored.report


def _record_text_or_404(claim_id: str) -> str:
    record_text = RECORD_TEXT_CACHE.get(claim_id)
    if record_text:
        return record_text

    stored = load_claim_artifacts(claim_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="No uploaded clinical record is saved for this claim. Run analysis first.")

    RECORD_TEXT_CACHE[claim_id] = stored.record_text
    if stored.evidence is not None:
        EVIDENCE_CACHE[claim_id] = stored.evidence
    REPORT_CACHE[claim_id] = stored.report
    return stored.record_text


@app.post("/upload", response_model=UploadResponse)
async def upload_claim_documents(
    bill: UploadFile = File(...),
    record: UploadFile = File(...),
) -> UploadResponse:
    bill_content = await bill.read()
    if not bill_content:
        raise HTTPException(status_code=400, detail="Uploaded bill file is empty.")
    claim = _claim_from_uploaded_bill(bill_content, bill.filename)

    record_path = await _uploaded_record_to_temp_file(record)
    record_path.unlink(missing_ok=True)
    return UploadResponse(
        claim_id=claim.claim_id,
        status="accepted",
        message="Files accepted. Use /analyze/upload to process uploaded bill and record files.",
    )


@app.post("/preview/bill", response_model=Claim)
async def preview_uploaded_bill(bill: UploadFile = File(...)) -> Claim:
    bill_content = await bill.read()
    if not bill_content:
        raise HTTPException(status_code=400, detail="Uploaded bill file is empty.")
    return _claim_from_uploaded_bill(bill_content, bill.filename)


@app.post("/analyze/upload", response_model=ClaimReport)
async def analyze_uploaded_claim(
    bill: UploadFile = File(...),
    record: UploadFile = File(...),
) -> ClaimReport:
    bill_content = await bill.read()
    if not bill_content:
        raise HTTPException(status_code=400, detail="Uploaded bill file is empty.")

    claim = _claim_from_uploaded_bill(bill_content, bill.filename)
    record_path = await _uploaded_record_to_temp_file(record)
    try:
        return _build_report_from_claim_and_record(
            claim,
            record_path,
            bill_filename=bill.filename,
            record_filename=record.filename,
        )
    finally:
        record_path.unlink(missing_ok=True)


@app.post("/analyze/sample", response_model=ClaimReport, include_in_schema=False)
def analyze_sample_claim() -> ClaimReport:
    return _analyze_sample_claim_from_source("text")


@app.post("/analyze/sample/{record_source}", response_model=ClaimReport, include_in_schema=False)
def analyze_sample_claim_by_source(record_source: str) -> ClaimReport:
    return _analyze_sample_claim_from_source(record_source)


@app.get("/report/{claim_id}", response_model=ClaimReport)
def get_report(claim_id: str) -> ClaimReport:
    return _report_or_404(claim_id)


@app.post("/action")
def record_reviewer_action(action: ReviewerAction) -> dict[str, str]:
    report = _report_or_404(action.claim_id)
    valid_line_ids = {line.line_id for line in report.claim.lines}
    if action.line_id not in valid_line_ids:
        raise HTTPException(status_code=404, detail="No claim line found for this reviewer action.")
    save_reviewer_action(action)
    return {"status": "recorded", "claim_id": action.claim_id, "line_id": action.line_id}


@app.post("/chat/{claim_id}", response_model=ChatResponse)
def chat_with_record(claim_id: str, request: ChatRequest) -> ChatResponse:
    record_text = _record_text_or_404(claim_id)
    return answer_question_from_record(
        claim_id=claim_id,
        question=request.question,
        record_text=record_text,
        evidence=EVIDENCE_CACHE.get(claim_id),
    )
