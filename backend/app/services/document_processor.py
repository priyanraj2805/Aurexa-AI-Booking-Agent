"""Multi-format document text extraction for the vectorless RAG pipeline.

Supported formats (by file extension):
    - .pdf  (PyMuPDF)
    - .docx (python-docx)
    - .xlsx (openpyxl)
    - .csv  (stdlib csv)
    - .txt / .md (plain text)

Every extractor emits the same chunk shape used elsewhere in the pipeline:
    {"content": str, "page_number": int, "source_type": str}

``page_number`` is format-specific: the PDF page, the sheet index for XLSX,
or simply 1 for flat text formats.
"""

import csv
import hashlib
import io
import logging
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}


def compute_content_hash(content: bytes) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content).hexdigest()


def chunk_text(text: str, max_chars: int = 2000) -> List[str]:
    """Split text into chunks by paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""

    for p in paragraphs:
        if len(current) + len(p) > max_chars:
            if current:
                chunks.append(current)
            current = p
        else:
            current += "\n\n" + p

    if current:
        chunks.append(current)

    return chunks


async def process_document(uploaded_file: bytes, filename: str) -> List[dict]:
    """Extract text chunks from any supported document format."""
    ext = Path(filename).suffix.lower()
    if not uploaded_file:
        raise ValueError("Empty file")

    if ext == ".pdf":
        return _extract_pdf(uploaded_file)
    if ext == ".docx":
        return _extract_docx(uploaded_file)
    if ext == ".xlsx":
        return _extract_xlsx(uploaded_file)
    if ext == ".csv":
        return _extract_csv(uploaded_file)
    if ext in (".txt", ".md"):
        return _extract_plain(uploaded_file, ext.lstrip("."))
    raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(raw: bytes) -> List[dict]:
    doc = fitz.open(stream=raw, filetype="pdf")
    chunks: List[dict] = []
    try:
        for page_num in range(len(doc)):
            text = doc[page_num].get_text()
            for chunk in chunk_text(text, max_chars=2000):
                chunks.append(
                    {
                        "content": chunk,
                        "page_number": page_num + 1,
                        "source_type": "pdf",
                    }
                )
    finally:
        doc.close()
    return chunks


def _extract_docx(raw: bytes) -> List[dict]:
    from docx import Document

    doc = Document(io.BytesIO(raw))
    parts: List[str] = []

    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text.strip())

    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    return _build_chunks("\n\n".join(parts), page_number=1, source_type="docx")


def _extract_xlsx(raw: bytes) -> List[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    chunks: List[dict] = []
    try:
        for sheet_index, sheet in enumerate(wb.worksheets, start=1):
            rows: List[str] = []
            for row in sheet.iter_rows(values_only=True):
                values = [str(v) for v in row if v is not None]
                if values:
                    rows.append(" | ".join(values))
            if rows:
                chunks.extend(
                    _build_chunks(
                        "\n".join(rows),
                        page_number=sheet_index,
                        source_type="xlsx",
                    )
                )
    finally:
        wb.close()
    return chunks


def _extract_csv(raw: bytes) -> List[dict]:
    text = raw.decode("utf-8", errors="replace")
    parsed = list(csv.reader(io.StringIO(text)))
    lines = [" | ".join(row) for row in parsed if any(row)]
    return _build_chunks("\n".join(lines), page_number=1, source_type="csv")


def _extract_plain(raw: bytes, source_type: str) -> List[dict]:
    text = raw.decode("utf-8", errors="replace")
    return _build_chunks(text, page_number=1, source_type=source_type)


def _build_chunks(
    text: str, *, page_number: int, source_type: str
) -> List[dict]:
    return [
        {
            "content": chunk,
            "page_number": page_number,
            "source_type": source_type,
        }
        for chunk in chunk_text(text, max_chars=2000)
    ]


async def process_pdf(uploaded_file: bytes, filename: str) -> List[dict]:
    """Backwards-compatible alias for callers expecting PDF processing."""
    return await process_document(uploaded_file, filename)