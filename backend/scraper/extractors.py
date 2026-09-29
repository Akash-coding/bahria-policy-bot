from __future__ import annotations

import logging
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from rag.extraction import ExtractionError, extract_pages, _clean_text

logger = logging.getLogger("scraper")

DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENT_TYPES = {"pdf", "doc", "docx", "txt"}
_PAGE_SPLIT = re.compile(r"\n*\[\[page (\d+)\]\]\n*")
_PRINTABLE = re.compile(rb"[\x09\x0a\x0d\x20-\x7e]{48,}")
_SKIP_IMAGE_HINTS = (
    "favicon",
    "sprite",
    "pixel",
    "tracking",
    "1x1",
    "spacer",
    "blank.gif",
    "/logo.",
    "logo.png",
    "logo.svg",
    "icon-",
    "/icons/",
    "apple-touch",
)


def extension_of(url: str) -> str:
    path = urlparse(url or "").path.lower()
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def file_type_of(url: str, content_type: str = "") -> str:
    ext = extension_of(url).lstrip(".")
    ctype = (content_type or "").lower()
    if "pdf" in ctype or ext == "pdf":
        return "pdf"
    if "wordprocessingml" in ctype or ext == "docx":
        return "docx"
    if "msword" in ctype or ext == "doc":
        return "doc"
    if ctype.startswith("text/plain") or ext == "txt":
        return "txt"
    if ctype.startswith("image/") or ext in {"jpg", "jpeg", "png", "gif", "webp"}:
        return "image"
    return ""


def is_document_url(url: str, content_type: str = "") -> bool:
    return file_type_of(url, content_type) in DOCUMENT_TYPES


def is_image_url(url: str, content_type: str = "") -> bool:
    return file_type_of(url, content_type) == "image"


def is_decorative_image(url: str, alt: str = "") -> bool:
    haystack = f"{url} {alt}".lower()
    if any(hint in haystack for hint in _SKIP_IMAGE_HINTS):
        return True
    alt = (alt or "").strip().lower()
    return alt in {"logo", "icon", "banner", "spacer"}


def source_type_for(file_type: str) -> str:
    return {
        "html": "website",
        "pdf": "pdf",
        "doc": "word",
        "docx": "word",
        "txt": "text",
        "image": "image",
    }.get((file_type or "").lower(), "website")


def pack_pages(pages: list[tuple[int | None, str]]) -> str:
    bodies = []
    for page_number, body in pages:
        text = (body or "").strip()
        if not text:
            continue
        if page_number is None:
            bodies.append(text)
        else:
            bodies.append(f"[[page {page_number}]]\n\n{text}")
    return "\n\n".join(bodies).strip()


def unpack_pages(content: str, file_type: str) -> list[tuple[int | None, str]]:
    text = (content or "").strip()
    if not text:
        return []
    if file_type == "pdf" and "[[page " in text:
        parts = _PAGE_SPLIT.split(text)
        pages: list[tuple[int | None, str]] = []
        index = 1
        while index + 1 < len(parts):
            body = parts[index + 1].strip()
            if body:
                pages.append((int(parts[index]), body))
            index += 2
        if pages:
            return pages
    return [(None, text)]


def pages_from_bytes(payload: bytes, file_type: str, name: str = "file") -> list[tuple[int | None, str]]:
    file_type = (file_type or "").lower()
    if not payload:
        raise ExtractionError(f"Empty file: {name}")
    if file_type == "doc":
        if payload[:2] == b"PK":
            return pages_from_bytes(payload, "docx", name)
        text = _legacy_doc_text(payload)
        if not text:
            raise ExtractionError("No extractable text was found in the Word document.")
        return [(None, text)]
    if file_type not in {"pdf", "docx", "txt"}:
        raise ExtractionError(f"Unsupported file type: {file_type}")
    suffix = f".{file_type}"
    with NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(payload)
        handle.flush()
        return extract_pages(handle.name, file_type)


def image_text(payload: bytes, mime: str = "", alt: str = "", source_url: str = "") -> str:
    alt = (alt or "").strip()
    ocr = ""
    try:
        from rag.groq_client import extract_image_text

        ocr = (extract_image_text(payload, mime or "image/jpeg", source_url=source_url) or "").strip()
    except Exception as exc:
        logger.info("Image vision skipped for %s: %s", source_url or "image", exc)
    if len(ocr) >= 40:
        return ocr
    if len(alt) >= 20:
        return alt
    return ocr or alt


def _legacy_doc_text(payload: bytes) -> str:
    chunks: list[str] = []
    for match in _PRINTABLE.findall(payload or b""):
        try:
            piece = match.decode("latin-1")
        except Exception:
            continue
        if "Microsoft Office" in piece and len(piece) < 90:
            continue
        chunks.append(piece)
    try:
        utf = payload.decode("utf-16le", errors="ignore")
        chunks.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9 ,.;:'\"/%()+-]{30,}", utf))
    except Exception:
        pass
    return _clean_text("\n".join(chunks))
