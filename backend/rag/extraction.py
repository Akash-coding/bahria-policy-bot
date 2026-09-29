from __future__ import annotations

import html
import logging
import re
from pathlib import Path

logger = logging.getLogger("rag")


class ExtractionError(RuntimeError):
    pass


_FANCY_CHARS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u00a0": " ",
    "\u2026": "...",
    "\ufeff": "",
}
_MOJIBAKE_MARK = ("â", "Ã", "Â", "�")


def _mojibake_score(text: str) -> int:
    return sum((text or "").count(mark) for mark in _MOJIBAKE_MARK)


def _repair_mojibake(text: str) -> str:
    current = text or ""
    for _ in range(2):
        if _mojibake_score(current) == 0:
            return current
        repaired = None
        for encoding in ("cp1252", "latin-1"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if _mojibake_score(candidate) < _mojibake_score(current):
                repaired = candidate
                break
        if not repaired:
            break
        current = repaired
    return current


def normalize_policy_text(text: str) -> str:
    """Fix UTF-8 mojibake (Universityâs) and normalize quotes for chatbot display."""
    cleaned = html.unescape(text or "")
    cleaned = _repair_mojibake(cleaned)
    cleaned = cleaned.translate(str.maketrans(_FANCY_CHARS))
    cleaned = re.sub(r"â(?=['\"])", "", cleaned)
    cleaned = cleaned.replace("Â ", " ")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\x00", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _clean_text(text: str) -> str:
    return normalize_policy_text(text)


def extract_pages(file_path: str | Path, file_type: str) -> list[tuple[int | None, str]]:
    path = Path(file_path)
    if not path.exists():
        raise ExtractionError(f"File not found: {path}")

    file_type = (file_type or path.suffix.lstrip(".")).lower()
    if file_type == "pdf":
        return _extract_pdf(path)
    if file_type == "docx":
        return _extract_docx(path)
    if file_type == "txt":
        return _extract_txt(path)
    raise ExtractionError(f"Unsupported file type: {file_type}")


def _extract_pdf(path: Path) -> list[tuple[int | None, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError("pypdf is required to process PDF files.") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ExtractionError(f"Unable to read PDF: {exc}") from exc

    pages: list[tuple[int | None, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            logger.exception("Failed to extract text from PDF page %s of %s", index, path)
            raw = ""
        cleaned = _clean_text(raw)
        if cleaned:
            pages.append((index, cleaned))
    if not pages:
        raise ExtractionError("No extractable text was found in the PDF.")
    return pages


def _extract_docx(path: Path) -> list[tuple[int | None, str]]:
    try:
        import docx
    except ImportError as exc:
        raise ExtractionError("python-docx is required to process DOCX files.") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"Unable to read DOCX: {exc}") from exc

    paragraphs = [_clean_text(p.text) for p in document.paragraphs]
    tables_text: list[str] = []
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [_clean_text(cell.text) for cell in row.cells]
            rows.append(" | ".join(cell for cell in cells if cell))
        tables_text.append("\n".join(row for row in rows if row))

    body = _clean_text("\n\n".join([p for p in paragraphs if p] + tables_text))
    if not body:
        raise ExtractionError("No extractable text was found in the Word document.")
    return [(None, body)]


def _extract_txt(path: Path) -> list[tuple[int | None, str]]:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    cleaned = _clean_text(text)
    if not cleaned:
        raise ExtractionError("The text file is empty.")
    return [(1, cleaned)]
