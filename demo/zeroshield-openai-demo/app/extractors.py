"""Extract plain text from uploaded demo files."""
from __future__ import annotations

import csv
import io
from pathlib import Path


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".txt":
        return data.decode("utf-8", errors="replace")
    if ext == ".csv":
        return _csv_text(data)
    if ext == ".pdf":
        return _pdf_text(data)
    if ext in (".docx", ".doc"):
        return _docx_text(data)
    raise ValueError(f"Unsupported file type: {ext or '(none)'}")


def _csv_text(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return "\n".join(", ".join(row) for row in rows[:500])


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:50]:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts).strip()


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
