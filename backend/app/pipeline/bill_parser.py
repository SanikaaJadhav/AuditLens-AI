from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
import re
from typing import Any

import pandas as pd
from pydantic import ValidationError

from app.schemas import Claim


class BillParseError(ValueError):
    pass


COLUMN_ALIASES = {
    "claim_id": {"claim_id", "claim", "claim_number", "claim_no"},
    "patient_id": {"patient_id", "patient", "member_id", "patient_number", "patient_no"},
    "patient_name": {"patient_name", "patient_full_name", "member_name", "name"},
    "date_of_birth": {"date_of_birth", "dob", "patient_dob"},
    "provider_id": {"provider_id", "provider", "billing_provider_id", "npi"},
    "provider_name": {"provider_name", "billing_provider", "provider_full_name"},
    "provider_specialty": {"provider_specialty", "specialty"},
    "service_date": {"service_date", "date_of_service", "dos"},
    "line_id": {"line_id", "line", "line_number", "line_no"},
    "code": {"code", "cpt", "cpt_code", "procedure_code", "service_code"},
    "code_system": {"code_system", "coding_system"},
    "description": {"description", "service_description", "procedure_description", "item_description"},
    "units": {"units", "unit_count", "qty", "quantity"},
    "charge": {"charge", "charges", "amount", "billed_amount", "line_charge"},
    "modifiers": {"modifiers", "modifier", "cpt_modifier"},
    "place_of_service": {"place_of_service", "pos"},
}


REQUIRED_TABULAR_FIELDS = {
    "claim_id",
    "patient_id",
    "patient_name",
    "provider_id",
    "provider_name",
    "service_date",
    "code",
    "description",
    "charge",
}


def parse_bill_file(content: bytes, filename: str | None) -> Claim:
    suffix = _suffix(filename)
    if suffix == ".json":
        return parse_bill_json(content)
    if suffix == ".csv":
        return parse_bill_csv(content)
    if suffix == ".xlsx":
        return parse_bill_xlsx(content)
    if suffix == ".pdf":
        return parse_bill_pdf(content)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return parse_bill_image(content)
    raise BillParseError("Upload the bill as JSON, CSV, XLSX, PDF, or image.")


def parse_bill_json(content: bytes) -> Claim:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BillParseError(f"Could not parse uploaded bill JSON: {error}") from error

    try:
        claim = Claim.model_validate(payload)
    except ValidationError as strict_error:
        try:
            claim = _claim_from_patient_bill_json(payload)
        except (TypeError, KeyError, ValueError, ValidationError) as flexible_error:
            raise BillParseError(
                "The bill JSON could not be parsed or does not match the required claim schema. "
                f"Strict schema: {strict_error}. Flexible patient-bill parser: {flexible_error}"
            ) from flexible_error
    _validate_claim(claim)
    return claim


def _claim_from_patient_bill_json(payload: Any) -> Claim:
    if not isinstance(payload, dict):
        raise BillParseError("Uploaded JSON root must be an object.")

    line_items = payload.get("line_items") or payload.get("lines") or payload.get("services")
    if not isinstance(line_items, list) or not line_items:
        raise BillParseError("Uploaded JSON bill does not contain line_items, lines, or services.")

    provider = payload.get("provider") or {}
    patient = payload.get("patient") or {}
    claim = payload.get("claim") or {}
    invoice = payload.get("invoice") or {}
    rendering_provider = payload.get("rendering_provider") or {}

    service_date = claim.get("date_of_service") or payload.get("service_date") or invoice.get("service_date")
    claim_payload: dict[str, Any] = {
        "claim_id": claim.get("claim_number") or invoice.get("invoice_number") or payload.get("claim_id") or "JSON-UPLOADED",
        "patient": {
            "patient_id": patient.get("member_id") or patient.get("patient_id") or patient.get("id") or "PAT-UPLOADED",
            "name": patient.get("name") or patient.get("patient_name") or "Uploaded Patient",
            "date_of_birth": _clean_optional_date(patient.get("dob") or patient.get("date_of_birth"), "date_of_birth"),
        },
        "provider": {
            "provider_id": (
                rendering_provider.get("npi")
                or provider.get("tax_id_npi")
                or provider.get("npi")
                or provider.get("provider_id")
                or "PROV-UPLOADED"
            ),
            "name": provider.get("name") or provider.get("provider_name") or "Uploaded Provider",
            "specialty": provider.get("specialty"),
        },
        "service_date": _clean_date(str(service_date or ""), "service_date"),
        "lines": [],
    }

    for index, item in enumerate(line_items, start=1):
        if not isinstance(item, dict):
            raise BillParseError(f"JSON bill line {index} must be an object.")
        line_service_date = item.get("service_date") or item.get("date_of_service") or service_date
        claim_payload["lines"].append(
            {
                "line_id": item.get("line_id") or item.get("line") or f"L{index}",
                "code": _normalize_ocr_service_code(str(item.get("cpt_code") or item.get("code") or item.get("hcpcs") or "")),
                "code_system": item.get("code_system") or "CPT",
                "description": item.get("description") or item.get("service_description") or f"Procedure {index}",
                "units": _clean_int(item.get("units") or item.get("qty") or 1, "units", index),
                "charge": _clean_float(item.get("amount") or item.get("charge") or item.get("unit_charge") or 0, "charge", index),
                "service_date": _clean_optional_date(str(line_service_date or ""), "service_date"),
                "modifiers": _clean_modifiers(item.get("modifiers")),
                "place_of_service": item.get("place_of_service") or "11",
            }
        )

    return Claim.model_validate(claim_payload)


def parse_bill_csv(content: bytes) -> Claim:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BillParseError("Could not decode uploaded CSV bill as UTF-8.") from error

    try:
        frame = pd.read_csv(StringIO(text), dtype=object)
        return _claim_from_frame(frame, source="CSV")
    except Exception as strict_error:
        try:
            return _claim_from_mixed_patient_bill_csv(text)
        except (csv.Error, TypeError, KeyError, ValueError, ValidationError, BillParseError) as flexible_error:
            raise BillParseError(
                "Could not parse uploaded CSV bill. "
                f"Rectangular table parser: {strict_error}. Mixed document parser: {flexible_error}"
            ) from flexible_error


def _claim_from_mixed_patient_bill_csv(text: str) -> Claim:
    rows = [row for row in csv.reader(StringIO(text)) if any(cell.strip() for cell in row)]
    metadata, line_items, totals = _parse_mixed_bill_rows(rows)
    if not line_items:
        raise BillParseError("Mixed CSV bill does not contain a recognizable CPT line-item table.")
    return _claim_from_mixed_bill_parts(metadata, line_items, totals)


def _parse_mixed_bill_rows(rows: list[list[Any]]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, float]]:
    metadata: dict[str, str] = {}
    line_items: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    table_headers: list[str] = []
    in_line_table = False

    for raw_row in rows:
        row = [_clean_cell(cell) for cell in raw_row]
        if not any(row):
            continue
        normalized_first = _normalize_column(row[0]) if row else ""

        if normalized_first in {"cpt_code", "code", "procedure_code"}:
            table_headers = [_normalize_column(cell) for cell in row]
            in_line_table = True
            continue

        if in_line_table and table_headers and row[0]:
            if _looks_like_code(row[0]):
                padded = row + [""] * max(0, len(table_headers) - len(row))
                line_items.append({header: padded[index] for index, header in enumerate(table_headers) if header})
                continue
            in_line_table = False

        if len(row) >= 2 and row[0]:
            key = _normalize_column(row[0])
            value = row[1]
            if key:
                metadata[key] = value
        if len(row) >= 6 and row[-2]:
            total_key = _normalize_column(row[-2])
            if total_key:
                try:
                    totals[total_key] = _clean_float(row[-1], total_key, 1)
                except BillParseError:
                    pass

    return metadata, line_items, totals


def _claim_from_mixed_bill_parts(
    metadata: dict[str, str],
    line_items: list[dict[str, Any]],
    totals: dict[str, float],
) -> Claim:
    payload = {
        "provider": {
            "name": metadata.get("provider_name"),
            "tax_id_npi": metadata.get("provider_tax_id_npi"),
            "phone": metadata.get("provider_phone"),
            "address": metadata.get("provider_address"),
        },
        "invoice": {
            "invoice_number": metadata.get("invoice_number"),
            "statement_date": metadata.get("statement_date"),
            "status": metadata.get("status"),
        },
        "patient": {
            "name": metadata.get("patient_name"),
            "dob": metadata.get("patient_dob"),
            "member_id": metadata.get("patient_member_id"),
            "address": metadata.get("patient_address"),
        },
        "claim": {
            "claim_number": metadata.get("claim_number"),
            "group_number": metadata.get("group_number"),
            "date_of_service": metadata.get("date_of_service"),
            "insurer": metadata.get("insurer"),
        },
        "rendering_provider": {
            "name": metadata.get("rendering_provider_name"),
            "npi": metadata.get("rendering_provider_npi"),
        },
        "line_items": [
            {
                "cpt_code": item.get("cpt_code") or item.get("code") or item.get("procedure_code"),
                "description": item.get("description"),
                "icd10": item.get("icd10"),
                "units": item.get("units") or 1,
                "unit_charge": item.get("unit_charge"),
                "amount": item.get("amount") or item.get("charge"),
            }
            for item in line_items
        ],
        "totals": totals,
    }
    return _claim_from_patient_bill_json(payload)


def _looks_like_code(value: str) -> bool:
    cleaned = value.strip()
    return bool(re.search(r"\d", cleaned)) and bool(
        re.fullmatch(r"(?:[A-Z]\d{4}|\d{5}|[A-Z]{2,4}[A-Z0-9]{2,4})[A-Z]?", cleaned, flags=re.IGNORECASE)
    )


def parse_bill_xlsx(content: bytes) -> Claim:
    try:
        frame = pd.read_excel(BytesIO(content), dtype=object)
    except ImportError as error:
        raise BillParseError("XLSX bill parsing requires openpyxl. Install backend requirements and retry.") from error
    except Exception as error:
        raise BillParseError(f"Could not parse uploaded XLSX bill: {error}") from error

    try:
        return _claim_from_frame(frame, source="XLSX")
    except BillParseError as strict_error:
        try:
            sheets = pd.read_excel(BytesIO(content), sheet_name=None, header=None, dtype=object)
            return _claim_from_mixed_patient_bill_workbook(sheets)
        except (TypeError, KeyError, ValueError, ValidationError, BillParseError) as flexible_error:
            raise BillParseError(
                "Uploaded XLSX bill is missing required columns and could not be normalized. "
                f"Strict parser: {strict_error}. Mixed workbook parser: {flexible_error}"
            ) from flexible_error


def _claim_from_mixed_patient_bill_workbook(sheets: dict[str, pd.DataFrame]) -> Claim:
    metadata: dict[str, str] = {}
    line_items: list[dict[str, Any]] = []
    totals: dict[str, float] = {}

    for frame in sheets.values():
        frame = frame.where(pd.notna(frame), "")
        rows = [[_clean_cell(cell) for cell in row] for row in frame.to_numpy().tolist()]
        sheet_metadata, sheet_lines, sheet_totals = _parse_mixed_bill_rows(rows)
        metadata.update({key: value for key, value in sheet_metadata.items() if value})
        line_items.extend(sheet_lines)
        totals.update(sheet_totals)

    if not line_items:
        raise BillParseError("Mixed XLSX bill does not contain a recognizable CPT line-item table.")
    return _claim_from_mixed_bill_parts(metadata, line_items, totals)


def parse_bill_pdf(content: bytes) -> Claim:
    try:
        import pdfplumber
    except ImportError as error:
        raise BillParseError("PDF bill parsing requires pdfplumber. Install backend requirements and retry.") from error

    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as error:
        raise BillParseError(f"Could not extract text from uploaded PDF bill: {error}") from error

    return parse_bill_text_export(text, source="PDF")


def parse_bill_image(content: bytes) -> Claim:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as error:
        raise BillParseError("Image bill OCR requires Pillow and pytesseract. Install backend requirements and retry.") from error

    try:
        image = Image.open(BytesIO(content))
        text = pytesseract.image_to_string(image)
    except Exception as error:
        raise BillParseError(f"OCR failed for uploaded bill image: {error}") from error

    return parse_bill_text_export(text, source="image OCR")


def parse_bill_text_export(text: str, source: str) -> Claim:
    try:
        csv_text = _extract_csv_export(text)
        try:
            frame = pd.read_csv(StringIO(csv_text), dtype=object)
        except Exception as error:
            raise BillParseError(f"Could not parse extracted {source} bill table: {error}") from error
        return _claim_from_frame(frame, source=source)
    except BillParseError as structured_error:
        try:
            return _claim_from_generic_statement(text, source=source)
        except BillParseError as generic_error:
            raise BillParseError(
                "AuditLens could not automatically extract claim lines from this bill. "
                "Try a clearer image/PDF, or upload CSV/XLSX. "
                f"Structured parser: {structured_error}. Generic parser: {generic_error}"
            ) from generic_error


def _claim_from_frame(frame: pd.DataFrame, source: str) -> Claim:
    frame = frame.where(pd.notna(frame), "")
    if frame.empty:
        raise BillParseError(f"Uploaded {source} bill has no rows.")

    rows = [_normalized_row(row) for row in frame.to_dict(orient="records")]
    missing = sorted(field for field in REQUIRED_TABULAR_FIELDS if not _first(rows, field))
    if missing:
        raise BillParseError(f"Uploaded {source} bill is missing required column(s): {', '.join(missing)}.")

    service_date = _clean_date(_first(rows, "service_date"), "service_date")
    claim_payload: dict[str, Any] = {
        "claim_id": _first(rows, "claim_id"),
        "patient": {
            "patient_id": _first(rows, "patient_id"),
            "name": _first(rows, "patient_name"),
            "date_of_birth": _clean_optional_date(_first(rows, "date_of_birth"), "date_of_birth"),
        },
        "provider": {
            "provider_id": _first(rows, "provider_id"),
            "name": _first(rows, "provider_name"),
            "specialty": _first(rows, "provider_specialty") or None,
        },
        "service_date": service_date,
        "lines": [],
    }

    for index, row in enumerate(rows, start=1):
        if not _has_line_content(row):
            continue
        line_service_date = _clean_optional_date(row.get("service_date"), "service_date")
        claim_payload["lines"].append(
            {
                "line_id": row.get("line_id") or f"L{index}",
                "code": _required(row, "code", index),
                "code_system": row.get("code_system") or "CPT",
                "description": _required(row, "description", index),
                "units": _clean_int(row.get("units") or 1, "units", index),
                "charge": _clean_float(_required(row, "charge", index), "charge", index),
                "service_date": line_service_date,
                "modifiers": _clean_modifiers(row.get("modifiers")),
                "place_of_service": row.get("place_of_service") or "11",
            }
        )

    try:
        claim = Claim.model_validate(claim_payload)
    except ValidationError as error:
        raise BillParseError(f"Uploaded {source} bill does not match the AuditLens claim schema: {error}") from error
    _validate_claim(claim)
    return claim


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    output = {field: "" for field in COLUMN_ALIASES}
    for raw_key, raw_value in row.items():
        normalized_key = _normalize_column(raw_key)
        matched_field = next(
            (field for field, aliases in COLUMN_ALIASES.items() if normalized_key in aliases),
            None,
        )
        if matched_field:
            output[matched_field] = _clean_cell(raw_value)
    return output


def _normalize_column(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _extract_csv_export(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    header_index = next((index for index, line in enumerate(lines) if _looks_like_bill_header(line)), None)
    if header_index is None:
        raise BillParseError(
            "Could not find a readable bill table in the extracted PDF/image text. "
            "Use the AuditLens demo export layout or upload JSON, CSV, XLSX, or a cleaner PDF/image."
        )

    csv_lines = [lines[header_index]]
    for line in lines[header_index + 1 :]:
        if _looks_like_bill_footer(line):
            break
        if _line_has_claim_values(line):
            csv_lines.append(line)

    if len(csv_lines) < 2:
        raise BillParseError("Extracted bill table did not contain any claim lines.")
    return "\n".join(csv_lines)


def _looks_like_bill_header(line: str) -> bool:
    tokens = {_normalize_column(part) for part in re.split(r"[,|\t]", line)}
    return {"claim_id", "patient_id", "code", "charge"}.issubset(tokens)


def _line_has_claim_values(line: str) -> bool:
    return line.count(",") >= 8 and bool(re.search(r"\b[A-Z]?\d+\b", line))


def _looks_like_bill_footer(line: str) -> bool:
    normalized = _normalize_column(line)
    return normalized in {"end_bill_export", "clinical_record", "expected_result"}


def _claim_from_generic_statement(text: str, source: str) -> Claim:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    service_rows = [_parse_generic_service_row(line) for line in lines]
    service_rows = [row for row in service_rows if row]
    if not service_rows:
        service_rows = _parse_code_first_service_rows(lines, text)
    if not service_rows:
        raise BillParseError("No service rows with date, CPT/HCPCS code, and charge were found.")

    claim_id = _extract_first_match(
        [
            r"\bClaim\s*#:\s*([A-Za-z0-9-]+)",
            r"\bInvoice\s*#:\s*([A-Za-z0-9-]+)",
            r"\bStatement\s*#:\s*([A-Za-z0-9-]+)",
            r"\bAccount\s*#:\s*([A-Za-z0-9-]+)",
            r"\bInvoice\s+Number:\s*([A-Za-z0-9-]+)",
            r"\bAccount\s+Number:\s*([A-Za-z0-9-]+)",
        ],
        text,
        default=f"OCR-{abs(hash(text)) % 1_000_000:06d}",
    )
    patient_name = _extract_patient_name(text)
    provider_name = _extract_provider_name(lines)
    service_rows = _repair_outlier_service_dates(service_rows)
    service_date = service_rows[0]["service_date"]

    claim_payload: dict[str, Any] = {
        "claim_id": claim_id,
        "patient": {
            "patient_id": _extract_first_match(
                [
                    r"\bMember\s+ID:\s*([A-Za-z0-9-]+)",
                    r"\bAccount\s*#:\s*([A-Za-z0-9-]+)",
                    r"\bAccount\s+Number:\s*([A-Za-z0-9-]+)",
                ],
                text,
                default="PAT-UPLOADED",
            ),
            "name": patient_name,
            "date_of_birth": _clean_optional_date(
                _extract_first_match([r"\bDOB:\s*(\d{1,2}/\d{1,2}/\d{4})"], text, default=""),
                "date_of_birth",
            ),
        },
        "provider": {
            "provider_id": _extract_first_match([r"\bNPI\s*#?:\s*([A-Za-z0-9-]+)"], text, default="PROV-UPLOADED"),
            "name": provider_name,
            "specialty": None,
        },
        "service_date": service_date,
        "lines": [],
    }

    for index, row in enumerate(service_rows, start=1):
        claim_payload["lines"].append(
            {
                "line_id": f"L{index}",
                "code": row["code"],
                "code_system": "CPT",
                "description": row["description"],
                "units": 1,
                "charge": row["charge"],
                "service_date": row["service_date"],
                "modifiers": [],
                "place_of_service": "11",
            }
        )

    try:
        claim = Claim.model_validate(claim_payload)
    except ValidationError as error:
        raise BillParseError(f"Generic {source} statement does not match the claim schema: {error}") from error
    _validate_claim(claim)
    return claim


def _parse_code_first_service_rows(lines: list[str], text: str) -> list[dict[str, Any]]:
    service_date = _extract_first_match(
        [
            r"\bDate\s+of\s+Service:\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
            r"\bservices\s+rendered\s+on\s+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
        ],
        text,
        default="",
    )
    if not service_date:
        return []
    service_date = _clean_date(service_date, "service_date")

    rows: list[dict[str, Any]] = []
    previous_row: dict[str, Any] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if re.search(r"\b(total|subtotal|balance due|insurance|statement|patient information|claim &)\b", line, flags=re.IGNORECASE):
            continue

        row = _parse_code_first_service_row(line, service_date)
        if row:
            rows.append(row)
            previous_row = row
            continue

        if previous_row and len(line.split()) <= 4 and not re.search(r"\$|\d{4,}", line):
            previous_row["description"] = _normalize_ocr_description(f"{previous_row['description']} {line}")

    return rows


def _parse_code_first_service_row(line: str, service_date: str) -> dict[str, Any] | None:
    line = re.sub(r"^[|:\s]+", "", line)
    match = re.match(
        r"^(?P<code>(?:[A-Z]\d{4}|\d{5}|[A-Z]{2,4}[A-Z0-9]{2,4})[A-Z]?)\s+(?P<body>.+)$",
        line,
    )
    if not match:
        return None

    body = match.group("body")
    money_matches = list(re.finditer(r"-?\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?", body))
    money_values = [match for match in money_matches if _looks_like_money(match.group(0))]
    if not money_values:
        return None

    charge_match = next((item for item in money_values if not item.group(0).strip().startswith("-")), money_values[0])
    description = body[: charge_match.start()].strip(" -:|")
    description = re.sub(r"\b[A-Z]\d{2}(?:\.\d+)?\b\s+\d+\s*$", "", description).strip()
    description = re.sub(r"\b\d{1,3}(?:\.\d+)?\s+\d+\s*$", "", description).strip()

    if not description:
        return None

    return {
        "service_date": service_date,
        "description": _normalize_ocr_description(description),
        "code": _normalize_ocr_service_code(match.group("code")),
        "charge": _clean_float(charge_match.group(0), "charge", 1),
    }


def _looks_like_money(value: str) -> bool:
    stripped = value.strip()
    if "$" in stripped:
        return True
    return bool(re.match(r"^-?\d{2,4}\.\d{2}$", stripped))


def _parse_generic_service_row(line: str) -> dict[str, Any] | None:
    line = re.sub(r"^[^\d]+(?=\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", "", line.strip())
    if re.search(r"\b(total|amount due|payment|insurance provider|statement)\b", line, flags=re.IGNORECASE):
        return None

    date_match = re.match(r"^(?P<date>\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\s+(?P<body>.+)$", line)
    if not date_match:
        return None

    body = date_match.group("body")
    code_match = re.search(r"\b(?P<code>(?:[A-Z]\d{4}|\d{5}|[A-Z]{2,4}[A-Z0-9]{2,4})[A-Z]?)\b", body)
    if not code_match:
        return None

    money_matches = list(re.finditer(r"-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})", body))
    if not money_matches:
        return None

    charge_match = next((match for match in money_matches if not match.group(0).strip().startswith("-")), money_matches[0])
    description = body[: code_match.start()].strip(" -:|")
    if not description:
        description = f"Procedure {code_match.group('code')}"

    return {
        "service_date": _clean_date(date_match.group("date"), "service_date"),
        "description": _normalize_ocr_description(description),
        "code": _normalize_ocr_service_code(code_match.group("code")),
        "charge": _clean_float(charge_match.group(0), "charge", 1),
    }


def _normalize_ocr_service_code(code: str) -> str:
    normalized = code.upper()
    if normalized.startswith("FAC"):
        normalized = normalized.replace("O", "0")
    return normalized


def _repair_outlier_service_dates(service_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    date_counts: dict[str, int] = {}
    for row in service_rows:
        date_counts[row["service_date"]] = date_counts.get(row["service_date"], 0) + 1
    if not date_counts:
        return service_rows

    majority_date, majority_count = max(date_counts.items(), key=lambda item: item[1])
    if majority_count < max(2, len(service_rows) - 1):
        return service_rows

    majority_year, majority_month, majority_day = majority_date.split("-")
    repaired_rows = []
    for row in service_rows:
        row_date = row["service_date"]
        try:
            row_year, _row_month, row_day = row_date.split("-")
        except ValueError:
            repaired_rows.append(row)
            continue
        if row_date != majority_date and row_year == majority_year and row_day == majority_day:
            repaired_rows.append({**row, "service_date": majority_date})
        else:
            repaired_rows.append(row)
    return repaired_rows


def _extract_first_match(patterns: list[str], text: str, default: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return default


def _extract_patient_name(text: str) -> str:
    patient_match = re.search(r"\bPatient\s+Name:\s*([A-Za-z][A-Za-z .'-]+)", text, flags=re.IGNORECASE)
    if patient_match:
        return patient_match.group(1).strip()
    name_match = re.search(r"\bName:\s*([A-Z][A-Za-z .'-]+?)(?=\s+(?:Insurer|DOB|Claim|Member|Group|Address|Date)\b|\n|$)", text, flags=re.IGNORECASE)
    if name_match:
        return name_match.group(1).strip()
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.search(r"\bBILL\s+TO\b", line, flags=re.IGNORECASE):
            for candidate in lines[index + 1 : index + 6]:
                cleaned = re.sub(r"^[^A-Za-z]+", "", candidate).strip()
                if not cleaned:
                    continue
                cleaned = re.split(
                    r"\b(total\s+charges|insurance\s+payments?|patient\s+payments?|amount\s+you\s+owe|statement\s+summary)\b",
                    cleaned,
                    flags=re.IGNORECASE,
                )[0].strip()
                if re.search(r"\b(statement|summary|total|charges|payments?|amount|address|account|invoice|date|insurance)\b", cleaned, flags=re.IGNORECASE):
                    continue
                name_match = re.match(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", cleaned)
                if name_match:
                    return name_match.group(1)
    return "Uploaded Patient"


def _extract_provider_name(lines: list[str]) -> str:
    top_candidates: list[str] = []
    for line in lines[:4]:
        candidate = _clean_provider_candidate(line)
        if candidate:
            top_candidates.append(candidate)
    if top_candidates:
        if len(top_candidates) > 1 and re.fullmatch(r"(?:[A-Z]\s+)?Group", top_candidates[1], flags=re.IGNORECASE):
            return f"{top_candidates[0]} Group"
        if re.search(r"\b(medical|clinic|hospital|health|center|care|group)\b", top_candidates[0], flags=re.IGNORECASE):
            return top_candidates[0]

    for index, line in enumerate(lines[:8]):
        if re.search(r"\b(medical|clinic|hospital|health|center|care)\b", line, flags=re.IGNORECASE):
            previous = lines[index - 1].strip() if index > 0 else ""
            if previous and not re.search(r"\d|www\.|@|\(|\bstreet\b|\bway\b|\bsuite\b|\bpo box\b", previous, flags=re.IGNORECASE):
                combined = f"{previous} {line}".strip()
                if len(combined.split()) <= 6:
                    return combined
            return line.strip()
    return "Uploaded Provider"


def _clean_provider_candidate(line: str) -> str:
    cleaned = re.sub(r"\bPATIENT\s+BILL\b", "", line, flags=re.IGNORECASE)
    cleaned = re.split(r"\bThank\s+you\b|\bStatement\s+Date\b|\bAccount\s+Number\b", cleaned, flags=re.IGNORECASE)[0]
    cleaned = re.sub(r"^[^A-Za-z]+", "", cleaned)
    cleaned = re.sub(r"[^A-Za-z&' .-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or re.search(r"\b(your health|priority|www|suite|drive|street)\b", cleaned, flags=re.IGNORECASE):
        return ""
    return cleaned


def _normalize_ocr_description(description: str) -> str:
    cleaned = re.sub(r"\s+", " ", description).strip()
    cleaned = re.sub(r"\bOffice\s*:\s*it\b", "Office visit", cleaned, flags=re.IGNORECASE)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Uploaded service"


def _first(rows: list[dict[str, str]], field: str) -> str:
    return next((row[field] for row in rows if row.get(field)), "")


def _required(row: dict[str, str], field: str, index: int) -> str:
    value = row.get(field, "")
    if not value:
        raise BillParseError(f"Bill row {index} is missing {field}.")
    return value


def _has_line_content(row: dict[str, str]) -> bool:
    return any(row.get(field) for field in ("code", "description", "charge"))


def _clean_int(value: Any, field: str, index: int) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError as error:
        raise BillParseError(f"Bill row {index} has invalid {field}: {value}.") from error


def _clean_float(value: Any, field: str, index: int) -> float:
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError as error:
        raise BillParseError(f"Bill row {index} has invalid {field}: {value}.") from error


def _clean_modifiers(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[;,|]", value) if part.strip()]


def _clean_date(value: str, field: str) -> str:
    if not value:
        raise BillParseError(f"Uploaded bill is missing {field}.")
    cleaned = value.strip()
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", cleaned)
    if date_match:
        return date_match.group(1)
    alt_match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", cleaned)
    if alt_match:
        month, day, year = alt_match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return cleaned


def _clean_optional_date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    return _clean_date(value, field)


def _validate_claim(claim: Claim) -> None:
    if not claim.lines:
        raise BillParseError("Uploaded bill must contain at least one claim line.")
    line_ids = [line.line_id for line in claim.lines]
    if len(line_ids) != len(set(line_ids)):
        raise BillParseError("Uploaded bill contains duplicate line_id values.")


def _suffix(filename: str | None) -> str:
    return "." + (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
