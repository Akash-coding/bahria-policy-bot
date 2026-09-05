from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

from .ollama_client import OllamaError, generate_answer, stream_generate
from .prompts import (
    BOT_IDENTITY_ANSWER,
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

    greeting = _greeting_reply(question)
    if greeting:
        return _ready(greeting, found=True)

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
        logger.warning("Ollama unavailable; returning extractive policy excerpts")
        answer = _extractive_answer(prepared["hits"])
    answer = _prefer_excerpts_if_refused(answer, prepared["hits"])
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
    answer = ""
    last_visible = ""
    raw = ""
    try:
        for chunk in stream_generate(prepared["system_prompt"], prepared["user_prompt"]):
            raw += chunk
            visible = _partial_visible(raw)
            if visible and visible != last_visible:
                last_visible = visible
                yield {"type": "delta", "text": visible}
        answer = sanitize_answer(raw) if raw.strip() else _extractive_answer(prepared["hits"])
    except OllamaError:
        logger.warning("Ollama unavailable during stream; returning extractive policy excerpts")
        answer = sanitize_answer(raw) if raw.strip() else _extractive_answer(prepared["hits"])

    answer = _prefer_excerpts_if_refused(answer, prepared["hits"])
    found = NOT_FOUND_MESSAGE.lower() not in answer.lower()
    sources = prepared["sources"] if found else []
    if answer != last_visible:
        yield {"type": "delta", "text": answer}
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
    return False


def _partial_visible(raw: str) -> str:
    if _still_thinking(raw):
        return ""
    cleaned = sanitize_answer(raw)
    if cleaned == NOT_FOUND_MESSAGE:
        stripped = re.sub(r"</?unused\d+>", "", raw, flags=re.I)
        stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.I | re.S)
        stripped = stripped.strip()
        if not stripped:
            return ""
        first = stripped.splitlines()[0].strip()
        if _REASONING_LINE.match(first) or not re.search(r"^#|\d+\.\s+\*\*", stripped, re.M):
            return ""
    return cleaned


_REASONING_LINE = re.compile(
    r"^(the user is asking|i need to scan|identify the core question|"
    r"scan the provided|look(?:ing)? (?:through|at) the excerpts|"
    r"let me (?:think|scan|check)|step \d+|analysis:|reasoning:)",
    re.I,
)

_GREETING_START = re.compile(
    r"^\s*(hi+|hello+|hey+|salam|salaam|assalam|"
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
    text = question.strip()
    if len(text) > 80 or _POLICY_HINT.search(text) or not _GREETING_START.search(text):
        return None
    lowered = text.lower()
    if lowered.startswith(("thank", "thx")):
        return (
            "## You're welcome\n\n"
            "Happy to help. Ask whenever you need a Bahria University policy, such as attendance, "
            "examinations, fees, or student conduct."
        )
    if re.match(r"^\s*(bye+|goodbye|see you)\b", text, re.I):
        return (
            "## Goodbye\n\n"
            "Take care. Come back anytime you need a university policy explained."
        )
    return (
        "## Hello\n\n"
        "I am the Bahria University Policy Bot. I answer from official university policies "
        "on attendance, examinations, fees, leaves, and student conduct.\n\n"
        "Ask who I am if you want a full introduction, or ask a policy question."
    )


def sanitize_answer(text: str) -> str:
    """Keep only the user-facing policy answer; drop model reasoning and source lines."""
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

    lines = cleaned.splitlines()
    start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or re.match(r"^\d+\.\s+\*\*", stripped):
            start = index
            break
        if _REASONING_LINE.match(stripped):
            continue
    cleaned = "\n".join(lines[start:]).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or NOT_FOUND_MESSAGE


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
    lines = [
        "## Related policy information",
        "",
        "Based on the uploaded university documents, this is the relevant point:",
        "",
    ]
    for index, hit in enumerate(hits[:4], start=1):
        meta = hit.get("metadata") or {}
        title = meta.get("document_title") or "Untitled policy"
        page = meta.get("page_number")
        page_label = f", page {page}" if isinstance(page, int) and page > 0 else ""
        section = (meta.get("section") or "").strip()
        section_label = f", {section}" if section else ""
        excerpt = (hit.get("content") or "").strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:220].rsplit(" ", 1)[0] + "…"
        lines.append(f"{index}. **{title}{page_label}{section_label}:** {excerpt}")
    return "\n".join(lines).strip()


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
