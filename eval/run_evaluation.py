from __future__ import annotations

import json
import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
import pandas as pd

from app.pipeline import verify as verify_module
from app.main import app


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    title: str
    claim: dict[str, Any]
    evidence: dict[str, Any]
    expected_flags: set[tuple[str, str]]
    record_format: str = "txt"
    bill_format: str = "json"


def patient(case_id: str) -> dict[str, Any]:
    return {
        "patient_id": f"PAT-{case_id[-3:]}",
        "name": f"Synthetic Patient {case_id[-3:]}",
        "date_of_birth": "1977-09-18",
    }


def provider() -> dict[str, Any]:
    return {
        "provider_id": "PRV-EVAL",
        "name": "AuditLens Evaluation Clinic",
        "specialty": "Family Medicine",
    }


def line(line_id: str, code: str, description: str, charge: float, units: int = 1) -> dict[str, Any]:
    return {
        "line_id": line_id,
        "code": code,
        "code_system": "CPT",
        "description": description,
        "units": units,
        "charge": charge,
        "modifiers": [],
        "place_of_service": "11",
    }


def claim(case_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "claim_id": case_id,
        "patient": patient(case_id),
        "provider": provider(),
        "service_date": "2025-03-04",
        "lines": lines,
    }


DX_DESCRIPTIONS = {
    "J20.9": "Acute bronchitis, unspecified",
    "R05.9": "Cough, unspecified",
    "R07.89": "Other chest pain",
    "R00.2": "Palpitations",
    "I10": "Essential hypertension",
    "R11.0": "Nausea without vomiting",
    "R10.9": "Unspecified abdominal pain",
    "R51.9": "Headache, unspecified",
    "K80.20": "Calculus of gallbladder without cholecystitis",
    "R94.5": "Abnormal results of liver function studies",
}

PX_DESCRIPTIONS = {
    "71046": "Chest X-ray, two views",
    "71045": "Chest X-ray, single view",
    "93000": "Electrocardiogram complete",
    "93005": "Electrocardiogram tracing only",
    "36415": "Venipuncture",
    "76700": "Complete abdominal ultrasound",
}


def dx(code: str, index: int) -> dict[str, Any]:
    description = DX_DESCRIPTIONS[code]
    return {
        "evidence_id": f"DX{index}",
        "type": "diagnosis",
        "code": code,
        "description": description,
        "date": "2025-03-04",
        "source_span": f"{description} ({code}).",
        "page": 1,
    }


def px(code: str, index: int) -> dict[str, Any]:
    description = PX_DESCRIPTIONS[code]
    return {
        "evidence_id": f"PX{index}",
        "type": "procedure",
        "code": code,
        "description": description,
        "date": "2025-03-04",
        "source_span": f"{description} was documented as performed on 2025-03-04.",
        "page": 2,
    }


def evidence(
    case_id: str,
    diagnosis_codes: list[str],
    procedure_codes: list[str],
    supported_level: str | None = None,
    supported_code: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "claim_id": case_id,
        "patient_id": f"PAT-{case_id[-3:]}",
        "documented_diagnoses": [dx(code, index) for index, code in enumerate(diagnosis_codes, start=1)],
        "documented_procedures": [px(code, index) for index, code in enumerate(procedure_codes, start=1)],
        "documented_visit_complexity": None,
    }
    if supported_level and supported_code:
        payload["documented_visit_complexity"] = {
            "supported_level": supported_level,
            "supported_code": supported_code,
            "source_span": f"The visit involved {supported_level} complexity.",
            "page": 2,
        }
    return payload


def record_text(case: EvalCase) -> str:
    diagnoses = "\n".join(
        f"{index}. {item['description']} ({item['code']})."
        for index, item in enumerate(case.evidence["documented_diagnoses"], start=1)
    )
    procedure_phrases = {
        "71046": (
            "Chest X-ray, two views, was ordered and completed on 2025-03-04 "
            "to evaluate cough, fever, and chest discomfort. Result: no acute infiltrate and no pleural effusion."
        ),
        "93000": (
            "Electrocardiogram was performed and interpreted in clinic because the patient reported chest discomfort. "
            "Result: normal sinus rhythm, no acute ST-T changes."
        ),
        "36415": "One venous blood sample was collected for CBC and comprehensive metabolic panel on 2025-03-04.",
        "76700": (
            "Complete abdominal ultrasound was performed on 2025-03-04 after patient concern about nausea. "
            "The note documents nausea only and does not document abdominal pain, abnormal liver enzymes, "
            "suspected gallbladder disease, jaundice, trauma, mass, or other coverage-supporting indication."
        ),
    }
    procedures = "\n\n".join(
        procedure_phrases.get(item["code"], item["source_span"])
        for item in case.evidence["documented_procedures"]
    )
    complexity = case.evidence.get("documented_visit_complexity") or {}
    complexity_text = complexity.get("source_span", "The visit involved low complexity.")
    ct_text = "No CT head was ordered, performed, or discussed."
    return (
        "AUDITLENS AI SYNTHETIC EVALUATION CLINICAL RECORD\n"
        "This fictional record contains no real patient information.\n\n"
        f"Patient: {case.claim['patient']['name']}\n"
        f"Patient ID: {case.claim['patient']['patient_id']}\n"
        "Date of Service: 2025-03-04\n"
        f"Provider: {case.claim['provider']['name']}\n\n"
        "--- Page 1 ---\n"
        "Assessment\n"
        f"{diagnoses}\n\n"
        "--- Page 2 ---\n"
        f"Orders and Procedures\n{procedures}\n\n"
        f"Medical Decision Making\n{complexity_text} {ct_text}\n"
    )


def base_sample_case() -> EvalCase:
    case_id = "CLM-EVAL-001"
    return EvalCase(
        case_id=case_id,
        title="Original planted mixed-error claim",
        claim=claim(
            case_id,
            [
                line("L1", "99215", "Established patient office visit level 5", 310),
                line("L2", "71046", "Chest X-ray 2 views", 180),
                line("L3", "93000", "Electrocardiogram complete", 75),
                line("L4", "93005", "Electrocardiogram tracing only", 40),
                line("L5", "36415", "Venipuncture", 120, units=4),
                line("L6", "71046", "Chest X-ray 2 views", 180),
                line("L7", "70450", "CT head without contrast", 950),
                line("L8", "76700", "Complete abdominal ultrasound", 420),
            ],
        ),
        evidence=evidence(
            case_id,
            ["J20.9", "R05.9", "R07.89", "R11.0", "I10"],
            ["71046", "93000", "36415", "76700"],
            supported_level="moderate",
            supported_code="99214",
        ),
        expected_flags={
            ("L1", "UPCODE"),
            ("L4", "UNSUPPORTED"),
            ("L4", "NCCI_BUNDLE"),
            ("L5", "MUE_UNITS"),
            ("L6", "DUPLICATE"),
            ("L7", "NO_DOCUMENTATION"),
            ("L7", "MEDICAL_NECESSITY"),
            ("L8", "MEDICAL_NECESSITY"),
        },
    )


def evaluation_cases() -> list[EvalCase]:
    cases = [base_sample_case()]
    cases.extend(
        [
            EvalCase(
                case_id="CLM-EVAL-002",
                title="Clean supported claim",
                claim=claim(
                    "CLM-EVAL-002",
                    [
                        line("L1", "71046", "Chest X-ray 2 views", 180),
                        line("L2", "93000", "Electrocardiogram complete", 75),
                        line("L3", "36415", "Venipuncture", 30),
                        line("L4", "76700", "Complete abdominal ultrasound", 420),
                    ],
                ),
                evidence=evidence("CLM-EVAL-002", ["J20.9", "R07.89", "R10.9"], ["71046", "93000", "36415", "76700"]),
                expected_flags=set(),
                bill_format="csv",
            ),
            EvalCase(
                case_id="CLM-EVAL-003",
                title="Duplicate chest X-ray",
                claim=claim(
                    "CLM-EVAL-003",
                    [
                        line("L1", "71046", "Chest X-ray 2 views", 180),
                        line("L2", "71046", "Chest X-ray 2 views", 180),
                    ],
                ),
                evidence=evidence("CLM-EVAL-003", ["J20.9"], ["71046"]),
                expected_flags={("L2", "DUPLICATE")},
            ),
            EvalCase(
                case_id="CLM-EVAL-004",
                title="Excessive venipuncture units",
                claim=claim("CLM-EVAL-004", [line("L1", "36415", "Venipuncture", 90, units=3)]),
                evidence=evidence("CLM-EVAL-004", ["J20.9"], ["36415"]),
                expected_flags={("L1", "MUE_UNITS")},
            ),
            EvalCase(
                case_id="CLM-EVAL-005",
                title="Bundled ECG tracing",
                claim=claim(
                    "CLM-EVAL-005",
                    [
                        line("L1", "93000", "Electrocardiogram complete", 75),
                        line("L2", "93005", "Electrocardiogram tracing only", 40),
                    ],
                ),
                evidence=evidence("CLM-EVAL-005", ["R07.89"], ["93000"]),
                expected_flags={("L2", "UNSUPPORTED"), ("L2", "NCCI_BUNDLE")},
                bill_format="xlsx",
            ),
            EvalCase(
                case_id="CLM-EVAL-006",
                title="CT head absent from record",
                claim=claim("CLM-EVAL-006", [line("L1", "70450", "CT head without contrast", 950)]),
                evidence=evidence("CLM-EVAL-006", ["J20.9"], []),
                expected_flags={("L1", "NO_DOCUMENTATION"), ("L1", "MEDICAL_NECESSITY")},
                bill_format="pdf",
            ),
            EvalCase(
                case_id="CLM-EVAL-007",
                title="Ultrasound lacks medical necessity diagnosis",
                claim=claim("CLM-EVAL-007", [line("L1", "76700", "Complete abdominal ultrasound", 420)]),
                evidence=evidence("CLM-EVAL-007", ["R11.0"], ["76700"]),
                expected_flags={("L1", "MEDICAL_NECESSITY")},
            ),
            EvalCase(
                case_id="CLM-EVAL-008",
                title="E/M upcoding",
                claim=claim("CLM-EVAL-008", [line("L1", "99215", "Established patient office visit level 5", 310)]),
                evidence=evidence("CLM-EVAL-008", ["J20.9"], [], supported_level="moderate", supported_code="99214"),
                expected_flags={("L1", "UPCODE")},
            ),
            EvalCase(
                case_id="CLM-EVAL-009",
                title="Mixed MUE and NCCI findings",
                claim=claim(
                    "CLM-EVAL-009",
                    [
                        line("L1", "71046", "Chest X-ray 2 views", 180),
                        line("L2", "93000", "Electrocardiogram complete", 75),
                        line("L3", "93005", "Electrocardiogram tracing only", 40),
                        line("L4", "36415", "Venipuncture", 60, units=2),
                    ],
                ),
                evidence=evidence("CLM-EVAL-009", ["J20.9", "R07.89"], ["71046", "93000", "36415"]),
                expected_flags={("L3", "UNSUPPORTED"), ("L3", "NCCI_BUNDLE"), ("L4", "MUE_UNITS")},
                bill_format="csv",
            ),
            EvalCase(
                case_id="CLM-EVAL-010",
                title="Standalone unsupported ECG tracing line",
                claim=claim("CLM-EVAL-010", [line("L1", "93005", "Electrocardiogram tracing only", 40)]),
                evidence=evidence("CLM-EVAL-010", ["R07.89"], []),
                expected_flags={("L1", "NO_DOCUMENTATION")},
            ),
            EvalCase(
                case_id="CLM-EVAL-011",
                title="Chest X-ray single view bundled into two view",
                claim=claim(
                    "CLM-EVAL-011",
                    [
                        line("L1", "71046", "Chest X-ray 2 views", 180),
                        line("L2", "71045", "Chest X-ray single view", 120),
                    ],
                ),
                evidence=evidence("CLM-EVAL-011", ["J20.9", "R05.9"], ["71046"]),
                expected_flags={("L2", "NO_DOCUMENTATION"), ("L2", "NCCI_BUNDLE")},
            ),
            EvalCase(
                case_id="CLM-EVAL-012",
                title="Scanned clean claim ingestion",
                claim=claim(
                    "CLM-EVAL-012",
                    [
                        line("L1", "71046", "Chest X-ray 2 views", 180),
                        line("L2", "93000", "Electrocardiogram complete", 75),
                    ],
                ),
                evidence=evidence("CLM-EVAL-012", ["J20.9", "R07.89"], ["71046", "93000"]),
                expected_flags=set(),
                record_format="png",
                bill_format="png",
            ),
        ]
    )
    return cases


def reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def render_record_image(text: str, output_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError("Pillow is required to generate scanned evaluation records.") from error

    font_path = Path("/System/Library/Fonts/SFNSMono.ttf")
    font = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default()
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=96))

    width = 2200
    line_height = 40
    margin = 72
    height = max(900, margin * 2 + line_height * (len(lines) + 1))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = margin
    for line_text in lines:
        draw.text((margin, y), line_text, fill=(0, 0, 0), font=font)
        y += line_height

    image.save(output_path)


def render_bill_image(csv_text: str, output_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError("Pillow is required to generate scanned evaluation bills.") from error

    font_path = Path("/System/Library/Fonts/SFNSMono.ttf")
    font = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default()
    lines = ["AUDITLENS BILL EXPORT", *csv_text.splitlines(), "END_BILL_EXPORT"]
    width = 3000
    line_height = 42
    margin = 80
    height = max(900, margin * 2 + line_height * (len(lines) + 1))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = margin
    for line_text in lines:
        draw.text((margin, y), line_text, fill=(0, 0, 0), font=font)
        y += line_height

    image.save(output_path)


def render_bill_pdf(csv_text: str, output_path: Path) -> None:
    try:
        from reportlab.pdfgen import canvas
    except ImportError as error:
        raise RuntimeError("reportlab is required to generate evaluation bill PDFs.") from error

    width, height = 1800, 900
    pdf = canvas.Canvas(str(output_path), pagesize=(width, height))
    margin = 36
    y = height - margin
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, y, "AUDITLENS BILL EXPORT")
    y -= 22
    pdf.setFont("Courier", 10)
    for line_text in csv_text.splitlines():
        if y < margin:
            pdf.showPage()
            y = height - margin
            pdf.setFont("Courier", 10)
        pdf.drawString(margin, y, line_text)
        y -= 12
    pdf.setFont("Helvetica", 8)
    pdf.drawString(margin, margin / 2, "END_BILL_EXPORT")
    pdf.save()


def write_case_files(case: EvalCase, case_dir: Path) -> tuple[Path, Path]:
    case_dir.mkdir(parents=True, exist_ok=True)
    bill_path = write_bill_file(case, case_dir)

    note_text = record_text(case)
    if case.record_format == "png":
        record_path = case_dir / f"{case.case_id}_record_scanned.png"
        render_record_image(note_text, record_path)
        (case_dir / f"{case.case_id}_record_source.txt").write_text(note_text, encoding="utf-8")
    else:
        record_path = case_dir / f"{case.case_id}_record.txt"
        record_path.write_text(note_text, encoding="utf-8")

    (case_dir / "expected_flags.json").write_text(
        json.dumps(
            sorted(
                [{"line_id": line_id, "rule": rule} for line_id, rule in case.expected_flags],
                key=lambda item: (item["line_id"], item["rule"]),
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return bill_path, record_path


def bill_export_rows(case: EvalCase) -> list[dict[str, Any]]:
    rows = []
    claim_payload = case.claim
    for line_payload in claim_payload["lines"]:
        rows.append(
            {
                "claim_id": claim_payload["claim_id"],
                "patient_id": claim_payload["patient"]["patient_id"],
                "patient_name": claim_payload["patient"]["name"],
                "date_of_birth": claim_payload["patient"].get("date_of_birth"),
                "provider_id": claim_payload["provider"]["provider_id"],
                "provider_name": claim_payload["provider"]["name"],
                "provider_specialty": claim_payload["provider"].get("specialty"),
                "service_date": claim_payload["service_date"],
                "line_id": line_payload["line_id"],
                "code": line_payload["code"],
                "code_system": line_payload.get("code_system", "CPT"),
                "description": line_payload["description"],
                "units": line_payload.get("units", 1),
                "charge": line_payload["charge"],
                "modifiers": "",
                "place_of_service": line_payload.get("place_of_service", "11"),
            }
        )
    return rows


def write_bill_file(case: EvalCase, case_dir: Path) -> Path:
    if case.bill_format == "json":
        bill_path = case_dir / f"{case.case_id}_bill.json"
        bill_path.write_text(json.dumps(case.claim, indent=2), encoding="utf-8")
        return bill_path

    rows = bill_export_rows(case)
    frame = pd.DataFrame(rows)
    if case.bill_format == "csv":
        bill_path = case_dir / f"{case.case_id}_bill.csv"
        frame.to_csv(bill_path, index=False)
        return bill_path
    if case.bill_format == "xlsx":
        bill_path = case_dir / f"{case.case_id}_bill.xlsx"
        frame.to_excel(bill_path, index=False, sheet_name="Bill Lines")
        return bill_path
    if case.bill_format == "pdf":
        bill_path = case_dir / f"{case.case_id}_bill.pdf"
        render_bill_pdf(frame.to_csv(index=False), bill_path)
        return bill_path
    if case.bill_format == "png":
        bill_path = case_dir / f"{case.case_id}_bill_scanned.png"
        render_bill_image(frame.to_csv(index=False), bill_path)
        return bill_path

    raise RuntimeError(f"Unsupported bill format: {case.bill_format}")


def analyze_case_upload(client: TestClient, case: EvalCase, bill_path: Path, record_path: Path) -> dict[str, Any]:
    bill_content_type = {
        ".json": "application/json",
        ".csv": "text/csv",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(bill_path.suffix.lower(), "application/octet-stream")
    content_type = "image/png" if record_path.suffix.lower() == ".png" else "text/plain"
    with bill_path.open("rb") as bill_file, record_path.open("rb") as record_file:
        response = client.post(
            "/analyze/upload",
            files={
                "bill": (bill_path.name, bill_file, bill_content_type),
                "record": (record_path.name, record_file, content_type),
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"{case.case_id} failed with {response.status_code}: {response.text}")
    return response.json()


def predicted_flags_from_report(report: dict[str, Any]) -> set[tuple[str, str]]:
    return {(flag["line_id"], str(flag["rule"])) for flag in report.get("flags", []) if flag.get("rule")}


def metrics(expected: set[tuple[str, str]], predicted: set[tuple[str, str]]) -> dict[str, float | int]:
    true_positive = len(expected & predicted)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main() -> None:
    cases = evaluation_cases()
    results = []
    aggregate_expected: set[tuple[str, str, str]] = set()
    aggregate_predicted: set[tuple[str, str, str]] = set()
    generated_dir = PROJECT_ROOT / "eval" / "generated_cases"
    reports_dir = PROJECT_ROOT / "eval" / "results" / "reports"
    results_dir = PROJECT_ROOT / "eval" / "results"
    reset_directory(generated_dir)
    reset_directory(reports_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    previous_mode = verify_module.LLM_MODE
    verify_module.LLM_MODE = "mock"
    client = TestClient(app)

    try:
        for case in cases:
            case_dir = generated_dir / case.case_id
            bill_path, record_path = write_case_files(case, case_dir)
            report = analyze_case_upload(client, case, bill_path, record_path)
            predicted = predicted_flags_from_report(report)
            case_expected = {(case.case_id, line_id, rule) for line_id, rule in case.expected_flags}
            case_predicted = {(case.case_id, line_id, rule) for line_id, rule in predicted}
            aggregate_expected |= case_expected
            aggregate_predicted |= case_predicted
            case_metrics = metrics(case.expected_flags, predicted)
            report_path = reports_dir / f"{case.case_id}_report.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            metadata = report.get("processing_metadata") or {}
            results.append(
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "bill_format": case.bill_format,
                    "record_format": case.record_format,
                    "bill_path": str(bill_path.relative_to(PROJECT_ROOT)),
                    "record_path": str(record_path.relative_to(PROJECT_ROOT)),
                    "report_path": str(report_path.relative_to(PROJECT_ROOT)),
                    "expected_flags": sorted(
                        [{"line_id": line_id, "rule": rule} for line_id, rule in case.expected_flags],
                        key=lambda item: (item["line_id"], item["rule"]),
                    ),
                    "predicted_flags": sorted(
                        [{"line_id": line_id, "rule": rule} for line_id, rule in predicted],
                        key=lambda item: (item["line_id"], item["rule"]),
                    ),
                    "missing_flags": sorted(
                        [{"line_id": line_id, "rule": rule} for line_id, rule in case.expected_flags - predicted],
                        key=lambda item: (item["line_id"], item["rule"]),
                    ),
                    "extra_flags": sorted(
                        [{"line_id": line_id, "rule": rule} for line_id, rule in predicted - case.expected_flags],
                        key=lambda item: (item["line_id"], item["rule"]),
                    ),
                    "metrics": case_metrics,
                    "risk_score": report["risk_score"],
                    "dollars_at_risk": report["dollars_at_risk"],
                    "ai_trace_count": len(report.get("ai_traces", [])),
                    "record_input_type": metadata.get("record_input_type"),
                    "ocr_used": metadata.get("ocr_used"),
                }
            )
    finally:
        verify_module.LLM_MODE = previous_mode

    aggregate_metrics = metrics(aggregate_expected, aggregate_predicted)
    payload = {
        "dataset": "AuditLens AI end-to-end synthetic evaluation",
        "case_count": len(cases),
        "scoring_unit": "line_id + rule pair",
        "mode": "FastAPI upload endpoint with deterministic mock verifier plus rule guardrails",
        "generated_cases_dir": str(generated_dir.relative_to(PROJECT_ROOT)),
        "reports_dir": str(reports_dir.relative_to(PROJECT_ROOT)),
        "aggregate_metrics": aggregate_metrics,
        "cases": results,
    }
    ground_truth = {
        "dataset": payload["dataset"],
        "scoring_unit": payload["scoring_unit"],
        "cases": [
            {
                "case_id": case.case_id,
                "title": case.title,
                "bill_format": case.bill_format,
                "record_format": case.record_format,
                "expected_flags": sorted(
                    [{"line_id": line_id, "rule": rule} for line_id, rule in case.expected_flags],
                    key=lambda item: (item["line_id"], item["rule"]),
                ),
            }
            for case in cases
        ],
    }

    output_path = results_dir / "e2e_metrics.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (results_dir / "phase9_metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (PROJECT_ROOT / "eval" / "ground_truth_labels.json").write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    summary_path = PROJECT_ROOT / "eval" / "e2e_summary.md"
    summary_lines = [
        "# AuditLens AI End-to-End Evaluation",
        "",
        f"- Cases: {len(cases)}",
        "- Path tested: generated bill/record files -> FastAPI /analyze/upload -> extraction/OCR -> verification -> rules -> report JSON",
        "- Scoring unit: `(claim_id, line_id, rule)`",
        f"- Precision: {aggregate_metrics['precision']}",
        f"- Recall: {aggregate_metrics['recall']}",
        f"- F1: {aggregate_metrics['f1']}",
        f"- True positives: {aggregate_metrics['true_positive']}",
        f"- False positives: {aggregate_metrics['false_positive']}",
        f"- False negatives: {aggregate_metrics['false_negative']}",
        "",
        "## Cases",
        "",
    ]
    for item in results:
        summary_lines.append(
            f"- {item['case_id']} - {item['title']}: "
            f"F1 {item['metrics']['f1']}, risk {item['risk_score']}, "
            f"AI traces {item['ai_trace_count']}, bill {item['bill_format']}, record {item['record_input_type']}"
        )
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(json.dumps(payload["aggregate_metrics"], indent=2))
    print(f"Wrote {output_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
