from __future__ import annotations

import hashlib
import logging
import math
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
    """Local embedding service. Provider is selected via EMBEDDING_PROVIDER."""

    def __init__(self) -> None:
        self.provider = settings.EMBEDDING_PROVIDER
        self.model_name = settings.EMBEDDING_MODEL
        self._st_model = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cleaned = [text if text and text.strip() else " " for text in texts]
        if self.provider == "ollama":
            return [self._embed_ollama(text) for text in cleaned]
        if self.provider in {"sentence-transformers", "sbert"}:
            return self._embed_sbert(cleaned)
        if self.provider in {"lexical", "hash"}:
            return [self._embed_lexical(text) for text in cleaned]
        raise EmbeddingError(
            f"Unknown EMBEDDING_PROVIDER '{self.provider}'. "
            "Use ollama, sentence-transformers, or lexical."
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def _embed_ollama(self, text: str) -> list[float]:
        url = f"{settings.OLLAMA_BASE_URL}/api/embed"
        payload = {"model": self.model_name, "input": text, "keep_alive": "60m"}
        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 404:
                response = requests.post(
                    f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=60,
                )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise EmbeddingError(
                f"Failed to generate embeddings via Ollama ({self.model_name}): {exc}"
            ) from exc

        if "embeddings" in data and data["embeddings"]:
            return data["embeddings"][0]
        if "embedding" in data:
            return data["embedding"]
        raise EmbeddingError("Ollama embedding response did not include a vector.")

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
                    "Install it or set EMBEDDING_PROVIDER=ollama."
                ) from exc
            logger.info("Loading sentence-transformers model %s", self.model_name)
            self._st_model = SentenceTransformer(self.model_name)
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
