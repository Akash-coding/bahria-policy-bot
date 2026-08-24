from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

from .embeddings import get_embedding_service
from .graphstore import get_policy_graph
from .topics import extract_topics, heading_in, query_terms
from .vectorstore import get_vector_store

logger = logging.getLogger("rag")

_SPACE = re.compile(r"\s+")


def retrieve_policy_chunks(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Vector search first; Graph RAG only if confidence is too low."""
    retrieval_query = _retrieval_query(question, history or [])
    embedding = get_embedding_service().embed_query(retrieval_query)
    store = get_vector_store()
    raw_hits = store.query(embedding, top_k=max(settings.RAG_TOP_K * 2, settings.RAG_TOP_K))
    vector_hits = _dedupe_hits(raw_hits)
    relevant = [
        hit
        for hit in vector_hits
        if hit.get("relevance_score", 0) >= settings.SIMILARITY_THRESHOLD
    ]

    if _confident(relevant, question):
        return relevant[: settings.RAG_TOP_K], "vector"

    extra_ids: list[str] = []
    if getattr(settings, "GRAPH_RAG_ENABLED", True):
        graph = get_policy_graph()
        if not graph.file.exists():
            rebuild_graph_from_store()
        seed_ids = [str(hit.get("vector_id") or "") for hit in relevant]
        extra_ids = graph.expand(
            query_topics=extract_topics(question),
            seed_ids=seed_ids,
            limit=getattr(settings, "GRAPH_EXPAND_LIMIT", 6),
        )

    graph_hits = []
    if extra_ids:
        by_id = {str(hit.get("vector_id")): hit for hit in vector_hits}
        missing = [vector_id for vector_id in extra_ids if vector_id not in by_id]
        if missing:
            for item in store.get_by_ids(missing, embedding=embedding):
                by_id[str(item.get("vector_id"))] = item
        for vector_id in extra_ids:
            hit = by_id.get(vector_id)
            if hit:
                graph_hits.append(hit)

    merged = _dedupe_hits(relevant + graph_hits)
    merged.sort(key=lambda hit: float(hit.get("relevance_score") or 0), reverse=True)
    if not merged:
        logger.info("No vector or graph matches for question")
        return [], "none"

    method = "graph" if graph_hits else "vector"
    logger.info("Retrieved %s chunks via %s search", len(merged[: settings.RAG_TOP_K]), method)
    return merged[: settings.RAG_TOP_K], method


def rebuild_graph_from_store() -> None:
    items = get_vector_store().all_items()
    get_policy_graph().rebuild(items)


def _confident(hits: list[dict[str, Any]], question: str) -> bool:
    min_hits = getattr(settings, "GRAPH_MIN_VECTOR_HITS", 2)
    strong = getattr(settings, "GRAPH_STRONG_SCORE", 0.45)
    if len(hits) < min_hits:
        return False
    if float(hits[0].get("relevance_score") or 0) < strong:
        return False
    terms = query_terms(question) - {"the", "and", "for", "what", "how"}
    if not terms:
        return True
    for hit in hits[:3]:
        content_terms = query_terms(hit.get("content") or "")
        if terms & content_terms:
            return True
    return False


def _dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        meta = hit.get("metadata") or {}
        content = _SPACE.sub(" ", (hit.get("content") or "").strip().lower())
        fingerprint = content[:180]
        key = f"{meta.get('document_id')}::{meta.get('page_number')}::{fingerprint}"
        if key in seen or not content:
            continue
        seen.add(key)
        if not (meta.get("section") or "").strip():
            meta = {**meta, "section": heading_in(hit.get("content") or "")}
            hit = {**hit, "metadata": meta}
        unique.append(hit)
    return unique


def _retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    previous = [
        item["content"]
        for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    if not previous:
        return question
    return f"{previous[-1]}\n{question}"
