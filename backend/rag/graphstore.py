"""Lightweight policy graph used only when vector search is weak.

Nodes are chunks, documents, sections, departments, and topics.
Edges are membership (chunk→document/section/topic/department) plus
previous/next chunk in the same document. Stored as JSON — no extra DB.
"""
from __future__ import annotations

import json
import logging
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from .topics import extract_topics, heading_in

logger = logging.getLogger("rag")


class PolicyGraph:
    def __init__(self) -> None:
        self.path = Path(settings.VECTOR_DB_PATH)
        self.file = self.path / f"{settings.VECTOR_COLLECTION}.graph.json"
        self._data: dict[str, Any] | None = None

    def _empty(self) -> dict[str, Any]:
        return {
            "chunks": {},
            "topics": {},
            "sections": {},
            "departments": {},
            "documents": {},
            "neighbors": {},
        }

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.file.exists():
            self._data = self._empty()
            return self._data
        try:
            self._data = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not read policy graph; rebuilding from empty state")
            self._data = self._empty()
        return self._data

    def _save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._load())
        fd, tmp_name = tempfile.mkstemp(prefix="graph-", suffix=".json", dir=str(self.path))
        tmp_path = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            tmp_path.replace(self.file)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def rebuild(self, items: list[dict[str, Any]]) -> None:
        data = self._empty()
        by_document: dict[str, list[tuple[int, str]]] = {}

        for item in items:
            vector_id = str(item.get("id") or item.get("vector_id") or "")
            if not vector_id:
                continue
            content = item.get("document") or item.get("content") or ""
            meta = item.get("metadata") or {}
            document_id = str(meta.get("document_id") or "")
            section = (meta.get("section") or heading_in(content) or "").strip()
            department = (meta.get("department") or "").strip()
            category = (meta.get("category") or "").strip()
            title = meta.get("document_title") or ""
            topics = sorted(extract_topics(content, title, section, department, category))
            chunk_index = int(meta.get("chunk_index") or 0)

            data["chunks"][vector_id] = {
                "document_id": document_id,
                "section": section,
                "department": department,
                "topics": topics,
                "chunk_index": chunk_index,
            }
            if document_id:
                data["documents"].setdefault(document_id, []).append(vector_id)
                by_document.setdefault(document_id, []).append((chunk_index, vector_id))
            if section:
                data["sections"].setdefault(section.lower(), []).append(vector_id)
            if department:
                data["departments"].setdefault(department.lower(), []).append(vector_id)
            for topic in topics:
                data["topics"].setdefault(topic, []).append(vector_id)

        for _doc_id, pairs in by_document.items():
            ordered = [vector_id for _index, vector_id in sorted(pairs)]
            for i, vector_id in enumerate(ordered):
                neighbors = []
                if i > 0:
                    neighbors.append(ordered[i - 1])
                if i + 1 < len(ordered):
                    neighbors.append(ordered[i + 1])
                data["neighbors"][vector_id] = neighbors

        self._data = data
        self._save()
        logger.info("Rebuilt policy graph with %s chunks", len(data["chunks"]))

    def expand(
        self,
        query_topics: set[str],
        seed_ids: list[str],
        limit: int = 6,
    ) -> list[str]:
        data = self._load()
        ranked: dict[str, int] = {}

        def bump(vector_id: str, weight: int) -> None:
            if not vector_id or vector_id in seed_ids:
                return
            ranked[vector_id] = ranked.get(vector_id, 0) + weight

        for topic in query_topics:
            for vector_id in data.get("topics", {}).get(topic, []):
                bump(vector_id, 3)

        for seed in seed_ids:
            for neighbor in data.get("neighbors", {}).get(seed, []):
                bump(neighbor, 2)
            info = data.get("chunks", {}).get(seed) or {}
            section = (info.get("section") or "").lower()
            if section:
                for vector_id in data.get("sections", {}).get(section, []):
                    bump(vector_id, 1)
            department = (info.get("department") or "").lower()
            if department:
                for vector_id in data.get("departments", {}).get(department, []):
                    bump(vector_id, 1)

        ordered = sorted(ranked, key=ranked.get, reverse=True)
        return ordered[:limit]


@lru_cache(maxsize=1)
def get_policy_graph() -> PolicyGraph:
    return PolicyGraph()
