import csv, io
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

SUPPORTED = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}

def extract_text(data: bytes, extension: str) -> str:
    extension = extension.lower()
    if extension == ".docx":
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    if extension == ".xlsx":
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"[ชีต: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                vals = [str(v) for v in row if v is not None]
                if vals:
                    parts.append(" | ".join(vals))
        return "\n".join(parts)
    if extension == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if extension == ".txt":
        return data.decode("utf-8", errors="replace")
    if extension == ".csv":
        text = data.decode("utf-8-sig", errors="replace")
        return "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(text)))
    raise ValueError(f"ยังไม่รองรับไฟล์ {extension}")
