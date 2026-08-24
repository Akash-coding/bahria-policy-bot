from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextChunk:
    content: str
    page_number: int | None
    chunk_index: int
    section: str = ""


def split_pages(
    pages: list[tuple[int | None, str]],
    chunk_size: int = 900,
    overlap: int = 150,
) -> list[TextChunk]:
    from .topics import heading_in

    chunks: list[TextChunk] = []
    index = 0
    current_section = ""
    for page_number, text in pages:
        for piece in _split_text(text, chunk_size, overlap):
            found = heading_in(piece)
            if found:
                current_section = found
            chunks.append(
                TextChunk(
                    content=piece,
                    page_number=page_number,
                    chunk_index=index,
                    section=current_section,
                )
            )
            index += 1
    return chunks


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text]

    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            pieces.extend(_window_split(paragraph, chunk_size, overlap))
            current = ""
    if current:
        pieces.append(current)

    if overlap <= 0 or len(pieces) <= 1:
        return pieces
    return _apply_overlap(pieces, overlap)


def _window_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    step = max(chunk_size - overlap, 1)
    return [text[start : start + chunk_size].strip() for start in range(0, len(text), step) if text[start : start + chunk_size].strip()]


def _apply_overlap(pieces: list[str], overlap: int) -> list[str]:
    overlapped = [pieces[0]]
    for piece in pieces[1:]:
        previous_tail = overlapped[-1][-overlap:]
        merged = f"{previous_tail} {piece}".strip() if previous_tail else piece
        overlapped.append(merged)
    return overlapped
