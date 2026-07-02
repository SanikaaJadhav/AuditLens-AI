from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
NOTE_PATH = ROOT / "data" / "samples" / "clinical_note_CLM-1001.txt"
PDF_PATH = ROOT / "data" / "samples" / "clinical_note_CLM-1001.pdf"
PNG_PATH = ROOT / "data" / "samples" / "clinical_note_CLM-1001_scanned.png"


def make_pdf(text: str) -> None:
    page_width, page_height = letter
    margin = 0.72 * inch
    line_height = 12
    max_chars = 98

    pdf = canvas.Canvas(str(PDF_PATH), pagesize=letter)
    pdf.setTitle("AuditLens AI Synthetic Clinical Note CLM-1001")
    pdf.setAuthor("AuditLens AI synthetic dataset")

    y = page_height - margin
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin, y, "AuditLens AI Synthetic Clinical Record - CLM-1001")
    y -= line_height * 1.7
    pdf.setFont("Helvetica", 9)

    for raw_line in text.splitlines():
        lines = [""] if not raw_line else wrap(raw_line, width=max_chars, replace_whitespace=False)
        for line in lines:
            if y < margin:
                pdf.showPage()
                y = page_height - margin
                pdf.setFont("Helvetica", 9)
            if raw_line.startswith("--- Page"):
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(margin, y, line)
                pdf.setFont("Helvetica", 9)
            else:
                pdf.drawString(margin, y, line)
            y -= line_height
    pdf.save()


def make_scanned_image(text: str) -> None:
    width, height = 1700, 2500
    margin_x, margin_y = 120, 110
    line_height = 31
    max_chars = 88

    image = Image.new("RGB", (width, height), "#f7f4ec")
    draw = ImageDraw.Draw(image)
    try:
        regular = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
        bold = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 26)
    except OSError:
        regular = ImageFont.load_default()
        bold = regular

    y = margin_y
    draw.text((margin_x, y), "AuditLens AI Synthetic Clinical Record - CLM-1001", fill="#222222", font=bold)
    y += line_height * 2

    for raw_line in text.splitlines():
        if y > height - margin_y:
            break
        lines = [""] if not raw_line else wrap(raw_line, width=max_chars, replace_whitespace=False)
        for line in lines:
            if y > height - margin_y:
                break
            font = bold if raw_line.startswith("--- Page") else regular
            draw.text((margin_x, y), line, fill="#292929", font=font)
            y += line_height

    image = image.rotate(-0.35, expand=False, fillcolor="#f7f4ec")
    image = image.filter(ImageFilter.GaussianBlur(radius=0.25))
    image.save(PNG_PATH)


if __name__ == "__main__":
    note_text = NOTE_PATH.read_text(encoding="utf-8")
    make_pdf(note_text)
    make_scanned_image(note_text)
    print(PDF_PATH)
    print(PNG_PATH)
