from __future__ import annotations

import json
import logging
import math
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger("rag")


class VectorStoreError(RuntimeError):
    pass


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0:
        return 0.0
    return max(0.0, min(1.0, dot / denom))


class LocalVectorStore:
    """File-backed cosine index. No native compiler or cloud service required."""

    def __init__(self) -> None:
        self.path = Path(settings.VECTOR_DB_PATH)
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / f"{settings.VECTOR_COLLECTION}.json"
        self._items: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._items is not None:
            return self._items
        if not self.file.exists():
            self._items = {}
            return self._items
        try:
            payload = json.loads(self.file.read_text(encoding="utf-8"))
            items = payload.get("items", payload)
            if isinstance(items, list):
                self._items = {item["id"]: item for item in items if "id" in item}
            else:
                self._items = items
        except Exception as exc:
            raise VectorStoreError(f"Could not read vector index: {exc}") from exc
        return self._items

    def _save(self) -> None:
        items = self._load()
        self.path.mkdir(parents=True, exist_ok=True)
        data = {"items": list(items.values())}
        fd, tmp_name = tempfile.mkstemp(prefix="vectors-", suffix=".json", dir=str(self.path))
        tmp_path = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            tmp_path.replace(self.file)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def upsert_chunks(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        items = self._load()
        for vector_id, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            items[vector_id] = {
                "id": vector_id,
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }
        self._save()
        logger.info("Upserted %s vectors into %s", len(ids), self.file)

    def delete_document(self, document_id: int) -> None:
        items = self._load()
        keep = {
            key: value
            for key, value in items.items()
            if str((value.get("metadata") or {}).get("document_id")) != str(document_id)
        }
        self._items = keep
        self._save()

    def query(self, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        items = list(self._load().values())
        if not items:
            return []
        scored: list[dict[str, Any]] = []
        for item in items:
            score = _cosine(embedding, item.get("embedding") or [])
            scored.append(
                {
                    "vector_id": item.get("id"),
                    "content": item.get("document") or "",
                    "metadata": item.get("metadata") or {},
                    "distance": 1.0 - score,
                    "relevance_score": score,
                }
            )
        scored.sort(key=lambda row: row["relevance_score"], reverse=True)
        return scored[: max(top_k, 0)]

    def get_by_ids(
        self,
        ids: list[str],
        embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        items = self._load()
        results: list[dict[str, Any]] = []
        for vector_id in ids:
            item = items.get(vector_id)
            if not item:
                continue
            score = _cosine(embedding, item.get("embedding") or []) if embedding else 0.0
            results.append(
                {
                    "vector_id": vector_id,
                    "content": item.get("document") or "",
                    "metadata": item.get("metadata") or {},
                    "distance": 1.0 - score,
                    "relevance_score": score,
                }
            )
        return results

    def all_items(self) -> list[dict[str, Any]]:
        return list(self._load().values())

    def count(self) -> int:
        return len(self._load())


@lru_cache(maxsize=1)
def get_vector_store() -> LocalVectorStore:
    return LocalVectorStore()
