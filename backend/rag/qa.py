from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

from .embeddings import EmbeddingError
from .extraction import normalize_policy_text
from .groq_client import GroqError, generate_answer, stream_generate
from .prompts import (
    BOT_IDENTITY_ANSWER,
    CONTINUATION_USER_PROMPT,
    GREETING_SYSTEM_PROMPT,
    NOT_FOUND_MESSAGE,
    POLICY_BOT_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)
from .retriever import retrieve_policy_chunks
from .topics import extract_topics, query_terms
from .vectorstore import VectorStoreError

logger = logging.getLogger("rag")


def _clip_log(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _hit_log(hits: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hit in (hits or [])[:4]:
        meta = hit.get("metadata") or {}
        title = _clip_log(str(meta.get("document_title") or "untitled"), 50)
        score = float(hit.get("relevance_score") or 0)
        parts.append(f"{title}:{score:.2f}")
    return "; ".join(parts) or "(none)"


def prepare_answer(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return _ready("Please ask a question about Bahria University policies.", found=False)

    original = question
    question, declined = _continue_from_short_reply(question, history or [])
    if declined:
        return _ready(declined, found=True)

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

    try:
        relevant, method = retrieve_policy_chunks(question, history or [])
    except (EmbeddingError, VectorStoreError, OSError):
        logger.exception("Not found reason=embedding_failed question=%s", _clip_log(question))
        return _ready(NOT_FOUND_MESSAGE, found=False)
    if not relevant:
        logger.info("Not found reason=no_chunks question=%s", _clip_log(question))
        return _ready(NOT_FOUND_MESSAGE, found=False)

    logger.info(
        "Answering with %s retrieval (%s chunks) question=%s hits=%s",
        method,
        len(relevant),
        _clip_log(question),
        _hit_log(relevant),
    )
    context = _build_context(relevant)
    offer = _last_offered_question(history or [])
    if _is_short_continue(original) and offer:
        user_prompt = CONTINUATION_USER_PROMPT.format(
            offer=offer,
            question=question,
            history=_format_history(history or [], limit=6),
        )
    else:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            question=question,
            history=_format_history(history or []),
        )
    return {
        "mode": "generate",
        "system_prompt": POLICY_BOT_SYSTEM_PROMPT.format(context=context),
        "user_prompt": user_prompt,
        "hits": relevant,
        "sources": _unique_sources(relevant),
        "retrieval": method,
        "question": question,
    }


def answer_question(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    prepared = prepare_answer(question, history)
    follow_topic, _declined = _continue_from_short_reply(question, history or [])
    if prepared["mode"] == "ready":
        answer = _ensure_follow_up(
            follow_topic, prepared["answer"], hits=prepared.get("hits") or []
        )
        return {
            "answer": answer,
            "sources": prepared["sources"],
            "found": prepared["found"],
        }
    try:
        answer = sanitize_answer(
            generate_answer(prepared["system_prompt"], prepared["user_prompt"])
        )
    except GroqError as exc:
        logger.warning("Groq unavailable; using a local fallback: %s", exc)
        answer = ""
    answer = _ensure_follow_up(
        follow_topic,
        _finalize_answer(answer, prepared),
        hits=prepared.get("hits") or [],
    )
    found = NOT_FOUND_MESSAGE.lower() not in answer.lower()
    sources = prepared["sources"] if found else []
    _log_answer_outcome(prepared, found)
    return {
        "answer": answer,
        "sources": sources,
        "found": found,
    }


def stream_answer_events(question: str, history: list[dict[str, str]] | None = None):
    recent = (history or [])[-4:]
    prepared = prepare_answer(question, recent)
    follow_topic, _declined = _continue_from_short_reply(question, recent)
    if prepared["mode"] == "ready":
        yield {
            "type": "done",
            "answer": _ensure_follow_up(
                follow_topic, prepared["answer"], hits=prepared.get("hits") or []
            ),
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
    except GroqError as exc:
        logger.warning("Groq unavailable during stream; using a local fallback: %s", exc)
        answer = sanitize_answer(raw) if raw.strip() else ""

    answer = _ensure_follow_up(
        follow_topic,
        _finalize_answer(answer, prepared, visible=last_visible),
        hits=prepared.get("hits") or [],
    )
    found = NOT_FOUND_MESSAGE.lower() not in answer.lower()
    sources = prepared["sources"] if found else []
    _log_answer_outcome(prepared, found)
    logger.info(
        "Stream finished: raw=%s chars, answer=%s chars, found=%s think_tag=%s",
        len(raw),
        len(answer),
        found,
        "yes" if re.search(r"</think>|<think>", raw, flags=re.I) else "no",
    )
    yield {
        "type": "done",
        "answer": answer,
        "sources": sources,
        "found": found,
    }


def _log_answer_outcome(prepared: dict[str, Any], found: bool) -> None:
    question = _clip_log(str(prepared.get("question") or ""))
    retrieval = prepared.get("retrieval") or "none"
    hits = prepared.get("hits") or []
    if found:
        logger.info("Answer found retrieval=%s question=%s", retrieval, question)
        return
    if hits:
        logger.info(
            "Not found reason=model_said_missing question=%s retrieval=%s hits=%s",
            question,
            retrieval,
            _hit_log(hits),
        )
        return
    logger.info("Not found reason=empty_answer question=%s retrieval=%s", question, retrieval)


def _ready(answer: str, found: bool) -> dict[str, Any]:
    return {
        "mode": "ready",
        "answer": answer,
        "sources": [],
        "found": found,
    }


_SUGGESTED_SPLIT = re.compile(
    r"\n*\s*(?:\*\*\s*)?Suggested question\s*:?\s*(?:\*\*\s*)?",
    re.I,
)
_SHORT_YES = re.compile(
    r"^\s*(?:"
    r"yes(?:\s*,?\s*(?:please(?:\s+do)?|sure|do(?:\s+it)?|tell me|go ahead))?"
    r"|yep|yeah|yup"
    r"|sure(?:\s*,?\s*please)?"
    r"|ok(?:ay)?"
    r"|please(?:\s+do)?"
    r"|go ahead|of course|do it"
    r"|han+|haan+|ji+|haan?\s*ji|theek hai|bilkul"
    r")[\s.!,]*$",
    re.I,
)
_SHORT_NO = re.compile(
    r"^\s*(?:no|nope|nah|not now|no thanks|no thank you|"
    r"nahi+|na+|nahi chahiye|mat batao)"
    r"[\s.!,]*$",
    re.I,
)
_SHORT_MORE = re.compile(
    r"^\s*(?:tell me more|more(?:\s+detail(?:s)?)?(?:\s+please)?|"
    r"explain more|go on|continue|and then\??|what else|"
    r"please explain|please tell me)"
    r"[\s.!,]*$",
    re.I,
)
_OFFER_LEAD = re.compile(
    r"^\s*(?:"
    r"would you like(?: me)?(?: to)?"
    r"|do you (?:want|need)(?: me)?(?: to)?"
    r"|should i(?: also)?"
    r"|can i(?: also)?"
    r"|shall i"
    r"|want me to"
    r")\s+",
    re.I,
)
_NO_THANKS_REPLY = (
    "No problem — I will skip that. "
    "What else would you like to know from this policy, or name another handbook topic."
)


def _one_follow_up_question(text: str) -> str:
    line = (text or "").strip().strip("*").strip()
    line = line.splitlines()[0].strip() if line else ""
    mark = line.find("?")
    if mark >= 0:
        line = line[: mark + 1]
    line = re.sub(r"\s+", " ", line).strip()
    if line and not line.endswith("?"):
        line = line.rstrip(".") + "?"
    return line[:180]


def _last_user_question(history: list[dict[str, str]]) -> str:
    for item in reversed(history or []):
        if (item.get("role") or "") == "user" and (item.get("content") or "").strip():
            return (item.get("content") or "").strip()
    return ""


def _last_offered_question(history: list[dict[str, str]]) -> str:
    for item in reversed(history or []):
        if (item.get("role") or "") != "assistant":
            continue
        content = item.get("content") or ""
        _main, follow = _split_follow_up(content)
        if follow:
            return follow
        questions = re.findall(r"([^?\n][^?\n]{6,160}\?)", content)
        if questions:
            return _one_follow_up_question(questions[-1])
    return ""


def _is_short_yes(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 60:
        return False
    if _POLICY_HINT.search(stripped):
        return bool(_SHORT_YES.match(stripped))
    if _SHORT_YES.match(stripped):
        return True
    return bool(re.match(r"^\s*yes\b.{0,40}$", stripped, re.I))


def _is_short_no(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and len(stripped) <= 48 and bool(_SHORT_NO.match(stripped))


def _is_short_more(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and len(stripped) <= 60 and bool(_SHORT_MORE.match(stripped))


def _is_short_continue(text: str) -> bool:
    return _is_short_yes(text) or _is_short_more(text)


def _actionable_suggestion(offer: str) -> str:
    text = _one_follow_up_question(offer)
    stripped = _OFFER_LEAD.sub("", text).strip()
    if not stripped or stripped.lower() == text.lower():
        return text
    body = stripped.rstrip("?").strip()
    if not body:
        return text
    if re.match(r"^(what|how|when|where|why|which|who)\b", body, re.I):
        return body[0].upper() + body[1:] + "?"
    return body[0].upper() + body[1:] + "."


def _resolved_follow_up(history: list[dict[str, str]], extra: str = "") -> str:
    offer = _last_offered_question(history)
    last_user = _last_user_question(history)
    action = _actionable_suggestion(offer) if offer else ""
    parts = [part for part in (last_user, action, extra) if part]
    if not parts:
        return extra or last_user or offer
    seen: list[str] = []
    for part in parts:
        if part.lower() not in {item.lower() for item in seen}:
            seen.append(part)
    return " ".join(seen)


def _continue_from_short_reply(
    question: str, history: list[dict[str, str]]
) -> tuple[str, str | None]:
    if _is_short_yes(question) or _is_short_more(question):
        extra = "Tell me more, including a concrete example." if _is_short_more(question) else ""
        resolved = _resolved_follow_up(history, extra=extra)
        if resolved:
            logger.info("User accepted the previous follow-up")
            return resolved, None
        return question, (
            "Which handbook policy should I explain for you? "
            "Attendance, exams, fees, or leaves?"
        )
    if _is_short_no(question):
        return question, _NO_THANKS_REPLY
    return question, None


def _follow_up_from_query(question: str, answer: str) -> str:
    topics = extract_topics(question or "", answer or "")
    lowered_q = (question or "").lower()
    lowered_a = (answer or "").lower()
    percents = re.findall(r"(\d+(?:\.\d+)?)\s*%", answer or "")
    gpas = re.findall(r"(\d+(?:\.\d+)?)\s*(?:gpa|cgpa)", lowered_a)
    days = re.findall(r"(\d+)\s+days?", lowered_a)
    topic = sorted(topics)[0] if topics else ""

    if _IDENTITY.search(question or "") and not _POLICY_HINT.search(question or ""):
        return "Which handbook policy should I explain first?"
    if _is_small_talk(question or ""):
        return _indexed_source_follow_up()
    if NOT_FOUND_MESSAGE.lower() in lowered_a:
        if topic:
            return f"Would you like me to look up a related {topic} rule from the handbook?"
        return _indexed_source_follow_up()
    if percents and topic:
        return f"What happens if this {topic} requirement falls below {percents[0]}%?"
    if gpas:
        return f"What if my GPA drops below {gpas[0]}?"
    if days and topic:
        return f"How do I apply or report this {topic} issue within those {days[0]} days?"
    if re.search(r"\b(what is|what's|define|meaning)\b", lowered_q) and topic:
        return f"How is the {topic} rule applied if a student misses it?"
    if re.search(r"\b(how|procedure|apply|process)\b", lowered_q) and topic:
        return f"What conditions must be met before this {topic} request is approved?"
    if re.search(r"\b(if i|happen|penalty|fail|below|short)\b", lowered_q) and topic:
        return f"What can I do to stay within the {topic} policy?"
    if topic:
        return f"Would you like the next step related to this {topic} policy?"
    snippet = re.sub(r"\s+", " ", (question or "").strip()).rstrip("?.!")
    if len(snippet) > 70:
        snippet = snippet[:67].rsplit(" ", 1)[0]
    if snippet and not _is_small_talk(question or "") and not _GREETING_START.match(question or ""):
        return f"Would you like more detail about {snippet.lower()}?"
    return _indexed_source_follow_up()


def _split_follow_up(text: str) -> tuple[str, str]:
    parts = _SUGGESTED_SPLIT.split(text, maxsplit=1)
    main = parts[0].rstrip()
    labeled = _one_follow_up_question(parts[1]) if len(parts) > 1 else ""
    if labeled:
        return main, labeled
    blocks = [part.strip() for part in re.split(r"\n\s*\n", main) if part.strip()]
    if len(blocks) >= 2:
        last = _one_follow_up_question(blocks[-1])
        if last.endswith("?") and len(blocks[-1]) <= 140 and blocks[-1].count("?") == 1:
            return "\n\n".join(blocks[:-1]).rstrip(), last
    sentences = re.split(r"(?<=[.!])\s+", main)
    if len(sentences) >= 2:
        last = _one_follow_up_question(sentences[-1])
        prefix = " ".join(sentences[:-1]).rstrip()
        if last.endswith("?") and len(sentences[-1]) <= 140 and len(prefix) >= 80:
            return prefix, last
    return main, ""


def _ensure_follow_up(
    question: str, answer: str, hits: list[dict[str, Any]] | None = None
) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    if _is_short_no(question or ""):
        main, _existing = _split_follow_up(text)
        return main
    main, existing = _split_follow_up(text)
    if main.endswith("?") and not existing:
        return main
    follow = _pick_follow_up(question, main, hits or [], existing)
    if not follow:
        return main
    return f"{main}\n\n{follow}"


_FOLLOW_SKIP = {
    "the",
    "and",
    "for",
    "you",
    "your",
    "would",
    "like",
    "want",
    "check",
    "know",
    "tell",
    "more",
    "also",
    "this",
    "that",
    "from",
    "with",
    "about",
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "does",
    "happen",
    "please",
    "related",
    "next",
    "step",
    "into",
    "bringing",
    "rules",
    "rule",
    "can",
    "could",
    "should",
    "have",
    "any",
    "other",
    "there",
    "looking",
    "information",
    "specific",
}


def _source_blob(hits: list[dict[str, Any]], extra: str = "") -> str:
    parts = [extra or ""]
    for hit in hits[:6]:
        meta = hit.get("metadata") or {}
        parts.extend(
            [
                hit.get("content") or "",
                str(meta.get("document_title") or ""),
                str(meta.get("section") or ""),
                str(meta.get("category") or ""),
            ]
        )
    return " ".join(parts)


def _follow_up_is_grounded(follow: str, hits: list[dict[str, Any]], answer: str) -> bool:
    if not (follow or "").strip():
        return False
    blob = _source_blob(hits, answer).lower()
    if not blob.strip():
        return False
    follow_topics = extract_topics(follow)
    source_topics = extract_topics(blob)
    if follow_topics and follow_topics <= source_topics:
        return True
    distinctive = query_terms(follow) - _FOLLOW_SKIP
    if not distinctive:
        return bool(follow_topics & source_topics)
    return bool(distinctive & query_terms(blob))


def _follow_up_from_hits(
    hits: list[dict[str, Any]], question: str, answer: str
) -> str:
    if not hits:
        return ""
    blob = _source_blob(hits, answer)
    lowered = blob.lower()
    percents = re.findall(r"(\d+(?:\.\d+)?)\s*%", blob)
    gpas = re.findall(r"(\d+(?:\.\d+)?)\s*(?:gpa|cgpa)", lowered)
    days = re.findall(r"(\d+)\s+days?", lowered)
    topics = extract_topics(blob, question or "")
    topic = ""
    for name in (
        "attendance",
        "examination",
        "scholarship",
        "admission",
        "leave",
        "fee",
        "hostel",
        "probation",
    ):
        if name in topics:
            topic = "exam" if name == "examination" else name
            break
    if not topic and topics:
        topic = sorted(topics)[0]
    if percents and topic:
        return f"Would you like to know what happens if this {topic} requirement falls below {percents[0]}%?"
    if gpas:
        return f"Would you like to know what happens if the CGPA falls below {gpas[0]}?"
    if days and topic:
        return f"Would you like the next step for this {topic} rule within those {days[0]} days?"
    titles: list[str] = []
    seen: set[str] = set()
    for hit in hits[:4]:
        title = str((hit.get("metadata") or {}).get("document_title") or "").strip()
        key = title.lower()
        if title and len(title) > 4 and key not in seen:
            seen.add(key)
            titles.append(title)
    if titles:
        return f"Would you like more detail from {titles[0]}?"
    if topic:
        return f"Would you like the next step related to this {topic} policy?"
    return ""


def _indexed_source_follow_up() -> str:
    topics, titles = _indexed_handbook_hints()
    preferred = (
        "attendance",
        "examination",
        "scholarship",
        "admission",
        "leave",
        "fee",
        "hostel",
        "discipline",
        "semester",
    )
    labels = {"examination": "exam", "semester": "semester freeze"}
    for name in preferred:
        if name in topics:
            return f"Would you like me to explain the {labels.get(name, name)} policy from the handbook?"
    if titles:
        return f"Would you like me to explain {titles[0]}?"
    return "Would you like an attendance, exam, fee, or leave rule from the handbook?"


def _indexed_handbook_hints() -> tuple[set[str], list[str]]:
    topics: set[str] = set()
    titles: list[str] = []
    try:
        from .graphstore import get_policy_graph

        graph = get_policy_graph()
        if graph.file.exists():
            topics |= graph.topic_names()
    except Exception:
        logger.info("Could not read policy graph topics for a grounded follow-up")
    if topics:
        return topics, titles
    try:
        from .vectorstore import get_vector_store

        store = get_vector_store()
        path = getattr(store, "file", None)
        loaded = getattr(store, "_items", None)
        if path is not None and loaded is None:
            try:
                if path.exists() and path.stat().st_size > 2_000_000:
                    return set(), []
            except OSError:
                return set(), []
        items = store.all_items()[:80]
    except Exception:
        return set(), []
    seen_titles: set[str] = set()
    for item in items:
        meta = item.get("metadata") or {}
        content = item.get("document") or item.get("content") or ""
        title = str(meta.get("document_title") or "").strip()
        topics |= extract_topics(
            content,
            title,
            str(meta.get("section") or ""),
            str(meta.get("category") or ""),
        )
        key = title.lower()
        if title and len(title) > 4 and key not in seen_titles:
            seen_titles.add(key)
            titles.append(title)
    return topics, titles


def _pick_follow_up(
    question: str,
    main: str,
    hits: list[dict[str, Any]],
    existing: str,
) -> str:
    if existing and _follow_up_is_grounded(existing, hits, main):
        return existing
    from_hits = _follow_up_from_hits(hits, question, main)
    if from_hits:
        return from_hits
    if _is_small_talk(question or "") or (
        _IDENTITY.search(question or "") and not _POLICY_HINT.search(question or "")
    ):
        return _indexed_source_follow_up()
    generated = _follow_up_from_query(question, main)
    if generated and _follow_up_is_grounded(generated, hits, main):
        return generated
    if generated and not hits and _follow_up_is_grounded(generated, [], main):
        return generated
    if generated and not hits and not _is_small_talk(question or ""):
        return generated
    return from_hits or _indexed_source_follow_up()


def _usable_answer(text: str) -> str:
    if not (text or "").strip():
        return ""
    cleaned = sanitize_answer(text)
    if not cleaned:
        return ""
    if _contains_thinking(cleaned):
        return ""
    if NOT_FOUND_MESSAGE.lower() in cleaned.lower():
        return ""
    return cleaned


def _finalize_answer(answer: str, prepared: dict[str, Any], visible: str = "") -> str:
    shown = _usable_answer(visible)
    cleaned = _usable_answer(answer)
    hits = prepared.get("hits") or []
    if shown:
        # Keep the streamed wording. Only extend it if the final text is the same answer, just longer.
        if cleaned and (cleaned.startswith(shown) or shown.startswith(cleaned)):
            text = cleaned if len(cleaned) >= len(shown) else shown
        else:
            text = shown
        return _prefer_excerpts_if_refused(text, hits, prepared.get("question") or "")
    if cleaned:
        return _prefer_excerpts_if_refused(cleaned, hits, prepared.get("question") or "")
    if prepared.get("retrieval") == "chat":
        return _small_talk_fallback()
    if hits:
        logger.info("Model returned no usable answer; using a short policy summary")
        return _extractive_answer(hits, prepared.get("question") or "")
    return NOT_FOUND_MESSAGE


def _small_talk_fallback() -> str:
    return (
        "Wa alaikum assalam. I am the Bahria University Policy Bot. "
        "Ask whenever you need a university policy explained."
    )


def _prefer_excerpts_if_refused(
    answer: str, hits: list[dict[str, Any]], question: str = ""
) -> str:
    """If the model refuses but retrieval already found policy text, show those excerpts."""
    if hits and NOT_FOUND_MESSAGE.lower() in (answer or "").lower():
        logger.info("Model refused despite retrieved policy excerpts; returning excerpts")
        return _extractive_answer(hits, question)
    return answer


def _contains_thinking(text: str) -> bool:
    return bool(re.search(r"</?think>|</?unused94>|</?unused95>", text or "", flags=re.I))


def _still_thinking(raw: str) -> bool:
    text = raw or ""
    if re.search(r"<unused94>", text, flags=re.I) and not re.search(r"<unused95>", text, flags=re.I):
        return True
    if re.search(r"</think>|<unused95>", text, flags=re.I):
        return False
    if re.search(r"<think>", text, flags=re.I):
        return True
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if _THINKING_HINT.search(text) or _REASONING_LINE.match(first_line):
        return True
    kept = _drop_reasoning(_strip_think_tags(text))
    return not kept.strip()


def _partial_visible(raw: str) -> str:
    if _still_thinking(raw):
        return ""
    return _usable_answer(raw)


_REASONING_LINE = re.compile(
    r"^(okay[,.]?\s+|alright[,.]?\s+|hmm[,.]?\s+|wait[—\-,. ]|"
    r"the user\b|let me\b|looking at\b|i'?ll\b|i (?:need|should|see|will|must)\b|"
    r"we are given\b|we must\b|let's craft\b|let us craft\b|"
    r"the safest approach|perfect[,.]?\s+i'?ll|"
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
    r"won't say|will not mention|exact details from|"
    r"i'?ll say something like|the safest approach|in roman urdu style|"
    r"perfect\.?\s+i'?ll respond|no extra words)",
    re.I,
)
_THINKING_HINT = re.compile(
    r"(</think>|<think>|i'?ll say something like|the safest approach|"
    r"in roman urdu style|perfect\.?\s+i'?ll respond|no extra words|"
    r"let me think|chain of thought|hidden reasoning)",
    re.I,
)
_GREETING_START = re.compile(
    r"^\s*(hi+|hello+|hey+|salam|salaam|assalam|as-?salam|"
    r"wa\s*alaikum|walaikum|dua\b|jumma?h?\s+mubarak|"
    r"good (?:morning|afternoon|evening)|how are you|how(?:'s| is) it going|"
    r"what(?:'s| is) up|thanks|thank you|thx|bye+|goodbye|see you)\b",
    re.I,
)
_CHAT_STYLE = re.compile(
    r"\b(roman urdu|urdu me|baat karo|talk to me|chat (?:with|in)|in english)\b",
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
    if len(text) > 120 or _POLICY_HINT.search(text):
        return False
    return bool(_GREETING_START.search(text) or _CHAT_STYLE.search(text))


def _strip_think_tags(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"```(?:markdown|md)?", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("```", "")
    # Qwen often streams thinking with only the closing tag. Keep text after the last closer.
    if re.search(r"</think>", cleaned, flags=re.I):
        cleaned = re.split(r"</think>", cleaned, flags=re.I)[-1]
    if re.search(r"<unused95>", cleaned, flags=re.I):
        cleaned = re.split(r"</?unused95>", cleaned, flags=re.I)[-1]
    cleaned = re.sub(r"<unused94>.*?thought.*?(?:<unused95>|$)", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.I)
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
    cleaned = normalize_policy_text(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() or NOT_FOUND_MESSAGE


def _build_retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    previous_user = [
        item["content"] for item in history if item.get("role") == "user" and item.get("content")
    ]
    if not previous_user:
        return question
    return f"{previous_user[-1]}\n{question}"


def _format_history(history: list[dict[str, str]], limit: int = 2) -> str:
    recent = history[-limit:]
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
        title = normalize_policy_text(meta.get("document_title") or "Untitled source")
        source_type = _source_type_label(meta)
        url = (meta.get("source_url") or "").strip()
        page = meta.get("page_number")
        page_label = f"page {page}" if isinstance(page, int) and page > 0 else ""
        section = (meta.get("section") or "").strip()
        version = meta.get("version") or ""
        header = f"[{index}] {source_type}: {title}"
        if url:
            header += f" | {url}"
        elif page_label:
            header += f" | {page_label}"
        if section:
            header += f" | section: {section}"
        if version:
            header += f" | version {version}"
        body = normalize_policy_text((hit.get("content") or "").strip())
        if len(body) > 900:
            body = body[:900].rsplit(" ", 1)[0] + "…"
        block = f"{header}\n{body}"
        if used + len(block) > settings.MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts) if parts else "(no policy excerpts)"


def _extractive_answer(hits: list[dict[str, Any]], question: str = "") -> str:
    wants_gpa = bool(re.search(r"\bc?gpa\b", question or "", re.I))
    scored: list[tuple[int, str]] = []
    for hit in hits[:8]:
        text = re.sub(r"\s+", " ", normalize_policy_text(hit.get("content") or ""))
        for part in re.split(r"(?<=[.!?])\s+", text):
            if len(part) < 40:
                continue
            boost = 1 if wants_gpa and re.search(r"\bc?gpa\b", part, re.I) else 0
            scored.append((boost, part.strip()))
    scored.sort(key=lambda item: -item[0])
    sentences = [part for _boost, part in scored[:3]]
    if not sentences:
        return NOT_FOUND_MESSAGE
    return (
        "Here is the helpful point from the official policy, in simple terms. "
        + " ".join(sentences[:2])
    )


def _source_type_label(meta: dict[str, Any]) -> str:
    source_type = (meta.get("source_type") or meta.get("file_type") or "").lower()
    labels = {
        "pdf": "PDF",
        "word": "Word Document",
        "doc": "Word Document",
        "docx": "Word Document",
        "text": "Text",
        "txt": "Text",
        "image": "Image",
        "website": "Website",
        "html": "Website",
    }
    if source_type in labels:
        return labels[source_type]
    if (meta.get("image_url") or "").strip():
        return "Image"
    if (meta.get("source_url") or "").strip():
        return "Website"
    return "Policy Document"


def _unique_sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    sources: list[dict[str, Any]] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        page = meta.get("page_number")
        if page == -1:
            page = None
        url = (meta.get("source_url") or "").strip()
        key = (meta.get("document_id"), url, page, meta.get("chunk_index"))
        if key in seen:
            continue
        seen.add(key)
        source_type = _source_type_label(meta)
        title = normalize_policy_text(meta.get("document_title") or "Untitled source")
        sources.append(
            {
                "document_id": meta.get("document_id"),
                "document": title,
                "category": meta.get("category"),
                "page": page,
                "section": (meta.get("section") or "").strip() or None,
                "chunk_index": meta.get("chunk_index"),
                "relevance_score": round(float(hit.get("relevance_score") or 0), 4),
                "excerpt": normalize_policy_text(hit.get("content") or "")[:280],
                "source_type": source_type,
                "source_url": url or None,
                "image_url": (meta.get("image_url") or "").strip() or None,
                "file_type": meta.get("file_type") or None,
            }
        )
    return sources
