from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

from .ollama_client import OllamaError, generate_answer, stream_generate
from .prompts import (
    BOT_IDENTITY_ANSWER,
    GREETING_SYSTEM_PROMPT,
    NOT_FOUND_MESSAGE,
    POLICY_BOT_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)
from .retriever import retrieve_policy_chunks

logger = logging.getLogger("rag")


def prepare_answer(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return _ready("Please ask a question about Bahria University policies.", found=False)

    identity = _identity_reply(question)
    if identity:
        return _ready(identity, found=True)

    if _is_small_talk(question):
        return {
            "mode": "generate",
            "system_prompt": GREETING_SYSTEM_PROMPT,
            "user_prompt": question,
            "hits": [],
            "sources": [],
            "retrieval": "chat",
        }

    relevant, method = retrieve_policy_chunks(question, history or [])
    if not relevant:
        logger.info("No relevant policy chunks found for question")
        return _ready(NOT_FOUND_MESSAGE, found=False)

    logger.info("Answering with %s retrieval (%s chunks)", method, len(relevant))
    context = _build_context(relevant)
    return {
        "mode": "generate",
        "system_prompt": POLICY_BOT_SYSTEM_PROMPT.format(context=context),
        "user_prompt": USER_PROMPT_TEMPLATE.format(
            question=question,
            history=_format_history(history or []),
        ),
        "hits": relevant,
        "sources": _unique_sources(relevant),
        "retrieval": method,
    }


def answer_question(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    prepared = prepare_answer(question, history)
    if prepared["mode"] == "ready":
        return {
            "answer": prepared["answer"],
            "sources": prepared["sources"],
            "found": prepared["found"],
        }
    try:
        answer = sanitize_answer(
            generate_answer(prepared["system_prompt"], prepared["user_prompt"])
        )
    except OllamaError:
        logger.warning("Ollama unavailable; using a local fallback")
        answer = ""
    answer = _finalize_answer(answer, prepared)
    found = NOT_FOUND_MESSAGE.lower() not in answer.lower()
    sources = prepared["sources"] if found else []
    return {
        "answer": answer,
        "sources": sources,
        "found": found,
    }


def stream_answer_events(question: str, history: list[dict[str, str]] | None = None):
    prepared = prepare_answer(question, (history or [])[-4:])
    if prepared["mode"] == "ready":
        yield {
            "type": "done",
            "answer": prepared["answer"],
            "sources": prepared["sources"],
            "found": prepared["found"],
        }
        return

    yield {"type": "status", "status": "generating"}
    last_visible = ""
    raw = ""
    try:
        for chunk in stream_generate(prepared["system_prompt"], prepared["user_prompt"]):
            raw += chunk
            visible = _partial_visible(raw)
            if visible and visible != last_visible:
                last_visible = visible
                yield {"type": "delta", "text": visible}
        answer = sanitize_answer(raw) if raw.strip() else ""
    except OllamaError:
        logger.warning("Ollama unavailable during stream; using a local fallback")
        answer = sanitize_answer(raw) if raw.strip() else ""

    answer = _finalize_answer(answer, prepared, visible=last_visible)
    found = NOT_FOUND_MESSAGE.lower() not in answer.lower()
    sources = prepared["sources"] if found else []
    yield {
        "type": "done",
        "answer": answer,
        "sources": sources,
        "found": found,
    }


def _ready(answer: str, found: bool) -> dict[str, Any]:
    return {
        "mode": "ready",
        "answer": answer,
        "sources": [],
        "found": found,
    }


def _finalize_answer(answer: str, prepared: dict[str, Any], visible: str = "") -> str:
    shown = (visible or "").strip()
    cleaned = (answer or "").strip()
    shown_ok = bool(shown) and NOT_FOUND_MESSAGE.lower() not in shown.lower()
    cleaned_ok = bool(cleaned) and NOT_FOUND_MESSAGE.lower() not in cleaned.lower()
    if shown_ok:
        # Keep the streamed wording. Only extend it if the final text is the same answer, just longer.
        if cleaned_ok and (cleaned.startswith(shown) or shown.startswith(cleaned)):
            return cleaned if len(cleaned) >= len(shown) else shown
        return shown
    if cleaned_ok:
        return cleaned
    if prepared.get("retrieval") == "chat":
        return _small_talk_fallback()
    hits = prepared.get("hits") or []
    if hits:
        logger.info("Model returned no usable answer; using a short policy summary")
        return _extractive_answer(hits)
    return NOT_FOUND_MESSAGE


def _small_talk_fallback() -> str:
    return (
        "Wa alaikum assalam. I am the Bahria University Policy Bot. "
        "Ask whenever you need a university policy explained."
    )


def _prefer_excerpts_if_refused(answer: str, hits: list[dict[str, Any]]) -> str:
    """If the model refuses but retrieval already found policy text, show those excerpts."""
    if hits and NOT_FOUND_MESSAGE.lower() in (answer or "").lower():
        logger.info("Model refused despite retrieved policy excerpts; returning excerpts")
        return _extractive_answer(hits)
    return answer


def _still_thinking(raw: str) -> bool:
    if re.search(r"<unused94>", raw, flags=re.I) and not re.search(r"<unused95>", raw, flags=re.I):
        return True
    if re.search(r"<think>", raw, flags=re.I) and not re.search(r"</think>", raw, flags=re.I):
        return True
    kept = _drop_reasoning(_strip_think_tags(raw))
    return not kept.strip()


def _partial_visible(raw: str) -> str:
    if _still_thinking(raw):
        return ""
    cleaned = sanitize_answer(raw)
    if cleaned == NOT_FOUND_MESSAGE:
        return ""
    return cleaned


_REASONING_LINE = re.compile(
    r"^(okay[,.]?\s+|alright[,.]?\s+|hmm[,.]?\s+|wait[—\-,. ]|"
    r"the user\b|let me\b|looking at\b|i (?:need|should|see|will|must)\b|"
    r"we are given\b|we must\b|let's craft\b|let us craft\b|"
    r"identify the core question|scan the provided|"
    r"look(?:ing)? (?:through|at) the excerpts|"
    r"let me (?:think|scan|check|tackle)|step \d+|analysis:|reasoning:|"
    r"example:|important:|"
    r"\*?(?:double-checking|trimming|avoiding pitfalls))",
    re.I,
)
_REASONING_BLOB = re.compile(
    r"(/no_think|we are given a user message|as the bahria university policy bot|"
    r"if the user greets|reply in kind in two short|let's craft|let us craft|"
    r"the user said|since the user used|matching the user|"
    r"do not invent policy|do not write analysis|"
    r"let me tackle|the user (?:is asking|asked|wants|specifically)|"
    r"looking at the provided|retrieved policy context|double-checking|"
    r"avoiding pitfalls|i should prioritize|first line:|second line:|"
    r"won't say|will not mention|exact details from)",
    re.I,
)
_GREETING_START = re.compile(
    r"^\s*(hi+|hello+|hey+|salam|salaam|assalam|as-?salam|"
    r"wa\s*alaikum|walaikum|dua\b|jumma?h?\s+mubarak|"
    r"good (?:morning|afternoon|evening)|how are you|how(?:'s| is) it going|"
    r"what(?:'s| is) up|thanks|thank you|thx|bye+|goodbye|see you)\b",
    re.I,
)
_POLICY_HINT = re.compile(
    r"\b(policy|policies|attendance|exam|examination|fee|leave|hostel|"
    r"semester|grade|admission|harassment|probation|freeze|refund)\b",
    re.I,
)
_IDENTITY = re.compile(
    r"\b(who are you|what are you|what do you do|what can you do|your tasks?|"
    r"your purpose|why (?:were you|are you|do you exist|were you (?:created|made|built))|"
    r"about (?:you|yourself|this bot|the bot)|introduce yourself|"
    r"tell me about (?:you|yourself|this bot)|what is this bot|"
    r"what is your (?:job|role|function|task)|how do you work|"
    r"your (?:capabilities|features|duties|responsibilities))\b",
    re.I,
)


def _identity_reply(question: str) -> str | None:
    text = question.strip()
    if not _IDENTITY.search(text) or _POLICY_HINT.search(text):
        return None
    return BOT_IDENTITY_ANSWER


def _greeting_reply(question: str) -> str | None:
    return None if not _is_small_talk(question) else _small_talk_fallback()


def _is_small_talk(question: str) -> bool:
    text = question.strip()
    if len(text) > 80 or _POLICY_HINT.search(text):
        return False
    return bool(_GREETING_START.search(text))


def _strip_think_tags(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"```(?:markdown|md)?", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("```", "")
    if re.search(r"<unused95>", cleaned, flags=re.I):
        cleaned = re.split(r"</?unused95>", cleaned, flags=re.I)[-1]
    cleaned = re.sub(r"<unused94>.*?thought.*?(?:<unused95>|$)", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"</?unused\d+>", "", cleaned)
    cleaned = re.sub(r"^\s*thought\b.*?(?=\n[A-Z#])", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"^source:.*$", "", cleaned, flags=re.I | re.M)
    return cleaned


def _drop_reasoning(text: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    kept = [part for part in paragraphs if not _REASONING_BLOB.search(part) and not _REASONING_LINE.match(part.splitlines()[0].strip())]
    if kept:
        return "\n\n".join(kept)
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _REASONING_LINE.match(stripped) or _REASONING_BLOB.search(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def sanitize_answer(text: str) -> str:
    """Keep only the user-facing policy answer; drop model reasoning and source lines."""
    cleaned = _drop_reasoning(_strip_think_tags(text))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() or NOT_FOUND_MESSAGE


def _build_retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    previous_user = [
        item["content"] for item in history if item.get("role") == "user" and item.get("content")
    ]
    if not previous_user:
        return question
    return f"{previous_user[-1]}\n{question}"


def _format_history(history: list[dict[str, str]]) -> str:
    recent = history[-2:]
    if not recent:
        return "(none)"
    lines = []
    for item in recent:
        role = "User" if item.get("role") == "user" else "Bot"
        lines.append(f"{role}: {item.get('content', '')}")
    return "\n".join(lines)


def _build_context(hits: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        meta = hit.get("metadata") or {}
        title = meta.get("document_title") or "Untitled policy"
        page = meta.get("page_number")
        page_label = f"page {page}" if isinstance(page, int) and page > 0 else "page unknown"
        section = (meta.get("section") or "").strip()
        version = meta.get("version") or ""
        header = f"[{index}] Document: {title} | {page_label}"
        if section:
            header += f" | section: {section}"
        if version:
            header += f" | version {version}"
        body = (hit.get("content") or "").strip()
        if len(body) > 900:
            body = body[:900].rsplit(" ", 1)[0] + "…"
        block = f"{header}\n{body}"
        if used + len(block) > settings.MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts) if parts else "(no policy excerpts)"


def _extractive_answer(hits: list[dict[str, Any]]) -> str:
    sentences: list[str] = []
    for hit in hits[:4]:
        text = re.sub(r"\s+", " ", (hit.get("content") or "").strip())
        for part in re.split(r"(?<=[.!?])\s+", text):
            if len(part) < 40:
                continue
            sentences.append(part.strip())
            if len(sentences) >= 3:
                break
        if len(sentences) >= 3:
            break
    if not sentences:
        return NOT_FOUND_MESSAGE
    return (
        "Here is the helpful point from the official policy, in simple terms. "
        + " ".join(sentences[:2])
    )


def _unique_sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    sources: list[dict[str, Any]] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        page = meta.get("page_number")
        if page == -1:
            page = None
        key = (meta.get("document_id"), page, meta.get("chunk_index"))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "document_id": meta.get("document_id"),
                "document": meta.get("document_title") or "Untitled policy",
                "category": meta.get("category"),
                "page": page,
                "section": (meta.get("section") or "").strip() or None,
                "chunk_index": meta.get("chunk_index"),
                "relevance_score": round(float(hit.get("relevance_score") or 0), 4),
                "excerpt": (hit.get("content") or "")[:280],
            }
        )
    return sources
