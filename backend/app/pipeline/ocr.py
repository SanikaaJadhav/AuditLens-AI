from pathlib import Path

from app.config import SAMPLE_NOTE_SCANNED, SAMPLE_NOTE_TEXT


class DocumentTextExtractionError(RuntimeError):
    pass


def _is_synthetic_demo_scanned_record(path: Path) -> bool:
    name = path.name.lower()
    return path.name == SAMPLE_NOTE_SCANNED.name or ("clm-1001" in name and "scanned" in name)


def _extract_text_from_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as error:
        raise DocumentTextExtractionError("pdfplumber is required to extract text from PDF records.") from error

    text_parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_parts.append(f"--- Page {index} ---\n{page_text.strip()}")

    text = "\n\n".join(text_parts).strip()
    if not text:
        raise DocumentTextExtractionError(f"No readable text found in PDF: {path}")
    return text


def _extract_text_from_image(path: Path) -> str:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as error:
        if _is_synthetic_demo_scanned_record(path) and SAMPLE_NOTE_TEXT.exists():
            return SAMPLE_NOTE_TEXT.read_text(encoding="utf-8")
        raise DocumentTextExtractionError(
            "pytesseract and Pillow are required to OCR image records. "
            "Install pytesseract plus the system Tesseract binary, or use the sample fallback."
        ) from error

    try:
        text = pytesseract.image_to_string(Image.open(path)).strip()
    except Exception as error:
        if _is_synthetic_demo_scanned_record(path) and SAMPLE_NOTE_TEXT.exists():
            return SAMPLE_NOTE_TEXT.read_text(encoding="utf-8")
        raise DocumentTextExtractionError(
            "Image OCR failed. Ensure the Tesseract system binary is installed and available on PATH."
        ) from error

    if not text:
        if _is_synthetic_demo_scanned_record(path) and SAMPLE_NOTE_TEXT.exists():
            return SAMPLE_NOTE_TEXT.read_text(encoding="utf-8")
        raise DocumentTextExtractionError(f"OCR produced no text for image: {path}")
    return text


def extract_text_from_document(path: Path) -> str:
    """Extract TXT/PDF/image records with a demo fallback for the sample scan."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return _extract_text_from_image(path)
    raise DocumentTextExtractionError(f"Unsupported record file type: {path.suffix}")
