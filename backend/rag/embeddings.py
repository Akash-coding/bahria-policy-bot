from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from functools import lru_cache

import requests
from django.conf import settings

logger = logging.getLogger("rag")

LEXICAL_DIM = 384
LEXICAL_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "is", "are", "was", "were", "be", "what", "how", "when", "where", "which",
    "who", "can", "does", "do", "did", "with", "from", "this", "that", "it",
    "policy", "policies", "university", "student", "students",
}


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class EmbeddingError(RuntimeError):
    pass


class EmbeddingService:
    """Embedding service. Provider is selected via EMBEDDING_PROVIDER."""

    def __init__(self) -> None:
        self.provider = settings.EMBEDDING_PROVIDER
        self.model_name = settings.EMBEDDING_MODEL
        self._st_model = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cleaned = [text if text and text.strip() else " " for text in texts]
        if self.provider == "groq":
            return self._embed_groq_many(cleaned)
        if self.provider in {"sentence-transformers", "sbert"}:
            return self._embed_sbert(cleaned)
        if self.provider in {"lexical", "hash"}:
            return [self._embed_lexical(text) for text in cleaned]
        raise EmbeddingError(
            f"Unknown EMBEDDING_PROVIDER '{self.provider}'. "
            "Use groq, sentence-transformers, or lexical."
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([self._format_query(text)])[0]

    def _format_query(self, text: str) -> str:
        name = (self.model_name or "").lower()
        if "qwen" in name:
            return (
                "Instruct: Given a university policy question, retrieve the official "
                "policy passage that answers it\n"
                f"Query: {text}"
            )
        return text

    def _embed_timeout(self) -> int:
        return max(int(getattr(settings, "GROQ_TIMEOUT", 120)), 120)

    def _embed_groq_many(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        vectors: list[list[float]] = []
        total = len(texts)
        for start in range(0, total, batch_size):
            batch = texts[start : start + batch_size]
            logger.info(
                "Embedding %s-%s of %s with %s",
                start + 1,
                start + len(batch),
                total,
                self.model_name,
            )
            vectors.extend(self._embed_groq_batch(batch))
        if len(vectors) != total:
            raise EmbeddingError("Embedding count did not match text count.")
        return vectors

    def _embed_groq_batch(self, texts: list[str]) -> list[list[float]]:
        from .groq_client import GroqError, _base_url, _headers

        timeout = self._embed_timeout()
        url = f"{_base_url()}/embeddings"
        payload = {"model": self.model_name, "input": texts, "encoding_format": "float"}
        try:
            response = requests.post(url, headers=_headers(), json=payload, timeout=timeout)
            if not response.ok:
                try:
                    data = response.json()
                    error = data.get("error")
                    detail = error.get("message") if isinstance(error, dict) else error
                except ValueError:
                    detail = response.text
                raise EmbeddingError(
                    f"Failed to generate embeddings via Groq ({self.model_name}): "
                    f"{detail or response.status_code}"
                )
            data = response.json()
        except GroqError as exc:
            raise EmbeddingError(str(exc)) from exc
        except requests.RequestException as exc:
            raise EmbeddingError(
                f"Failed to generate embeddings via Groq ({self.model_name}): {exc}"
            ) from exc

        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbeddingError("Groq embedding response did not include a vector.")
        ordered = sorted(items, key=lambda item: int(item.get("index") or 0))
        vectors = [item.get("embedding") for item in ordered]
        if any(not isinstance(vector, list) or not vector for vector in vectors):
            raise EmbeddingError("Groq embedding response did not include a vector.")
        return vectors

    def _embed_sbert(self, texts: list[str]) -> list[list[float]]:
        model = self._load_sbert()
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]

    def _load_sbert(self):
        if self._st_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed. "
                    "Install it or set EMBEDDING_PROVIDER=groq."
                ) from exc
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            logger.info("Loading sentence-transformers model %s", self.model_name)
            try:
                self._st_model = SentenceTransformer(self.model_name)
            except Exception as exc:
                raise EmbeddingError(
                    f"Failed to load embedding model {self.model_name}: {exc}"
                ) from exc
        return self._st_model

    def _embed_lexical(self, text: str) -> list[float]:
        vector = [0.0] * LEXICAL_DIM
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if token not in LEXICAL_STOPWORDS and len(token) > 2
        ]
        if not tokens:
            vector[0] = 1.0
            return vector
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % LEXICAL_DIM
            vector[index] += 1.0
        return _normalize(vector)


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
