from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from functools import lru_cache
from pathlib import Path

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


def _vector_to_list(vector) -> list[float]:
    if hasattr(vector, "tolist"):
        return list(vector.tolist())
    return [float(value) for value in vector]


class EmbeddingError(RuntimeError):
    pass


def _project_root() -> Path:
    return Path(getattr(settings, "PROJECT_ROOT", Path.cwd()))


def _hf_cache_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in (
        getattr(settings, "EMBEDDING_MODEL_PATH", "") or "",
        os.environ.get("HF_HOME", ""),
        os.environ.get("HUGGINGFACE_HUB_CACHE", ""),
        os.environ.get("TRANSFORMERS_CACHE", ""),
        str(_project_root() / ".cache" / "huggingface"),
        str(Path.home() / ".cache" / "huggingface"),
        "/srv/data/huggingface",
        "/opt/huggingface",
    ):
        text = (raw or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if path.name == "hub":
            path = path.parent
        if path not in roots:
            roots.append(path)
    return roots


def _looks_like_sbert_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "modules.json").exists():
        return True
    has_config = (path / "config.json").exists()
    has_weights = any(
        (path / name).exists()
        for name in ("model.safetensors", "pytorch_model.bin", "model.onnx")
    )
    return has_config and has_weights


def _repo_ids(model_name: str) -> list[str]:
    name = (model_name or "").strip()
    if not name:
        return ["sentence-transformers/all-MiniLM-L6-v2"]
    ids = [name]
    if "/" not in name.replace("\\", "/"):
        ids.append(f"sentence-transformers/{name}")
    return list(dict.fromkeys(ids))


def _snapshot_from_hub_dir(hub_dir: Path) -> Path | None:
    refs_main = hub_dir / "refs" / "main"
    if refs_main.is_file():
        revision = refs_main.read_text(encoding="utf-8").strip()
        snap = hub_dir / "snapshots" / revision
        if _looks_like_sbert_dir(snap):
            return snap
    snapshots = hub_dir / "snapshots"
    if snapshots.is_dir():
        for snap in sorted(snapshots.iterdir(), reverse=True):
            if _looks_like_sbert_dir(snap):
                return snap
    if _looks_like_sbert_dir(hub_dir):
        return hub_dir
    return None


def find_local_embedding_model(model_name: str | None = None) -> Path | None:
    """Return a local MiniLM (or other SBERT) directory if the files are already on disk."""
    configured = (getattr(settings, "EMBEDDING_MODEL_PATH", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    name = (model_name or getattr(settings, "EMBEDDING_MODEL", "") or "").strip()
    if name:
        as_path = Path(name).expanduser()
        if as_path.exists():
            candidates.append(as_path)
        candidates.append(_project_root() / "models" / name)
        candidates.append(_project_root() / "models" / Path(name).name)

    for path in candidates:
        if _looks_like_sbert_dir(path):
            return path
        nested = _snapshot_from_hub_dir(path)
        if nested:
            return nested

    for root in _hf_cache_roots():
        hub = root / "hub" if (root / "hub").is_dir() else root
        for repo_id in _repo_ids(name or "all-MiniLM-L6-v2"):
            folder = "models--" + repo_id.replace("/", "--")
            found = _snapshot_from_hub_dir(hub / folder)
            if found:
                return found
            direct = hub / Path(repo_id).name
            if _looks_like_sbert_dir(direct):
                return direct
    return None


def _offline_requested() -> bool:
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "EMBEDDING_OFFLINE"):
        if os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _download_allowed() -> bool:
    if _offline_requested():
        return False
    return bool(getattr(settings, "EMBEDDING_ALLOW_DOWNLOAD", True))


def _load_sentence_transformer(source: str, local_files_only: bool):
    from sentence_transformers import SentenceTransformer

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return SentenceTransformer(source, local_files_only=local_files_only)


def embedding_status() -> dict[str, str | bool | None]:
    service = get_embedding_service()
    local = find_local_embedding_model(settings.EMBEDDING_MODEL)
    return {
        "configured_provider": settings.EMBEDDING_PROVIDER,
        "configured_model": settings.EMBEDDING_MODEL,
        "active_provider": service.active_provider or settings.EMBEDDING_PROVIDER,
        "active_model": service.active_model or settings.EMBEDDING_MODEL,
        "source": service.active_source or ("local-cache" if local else "not-loaded"),
        "local_model_path": str(local) if local else "",
        "local_model_found": bool(local),
    }


def log_embedding_startup() -> None:
    local = find_local_embedding_model(getattr(settings, "EMBEDDING_MODEL", ""))
    provider = getattr(settings, "EMBEDDING_PROVIDER", "")
    model = getattr(settings, "EMBEDDING_MODEL", "")
    if provider in {"lexical", "hash"}:
        logger.info(
            "Embeddings: builtin lexical hashed bag-of-words (%sd). Vector search is enabled.",
            LEXICAL_DIM,
        )
        return
    if provider == "groq":
        logger.info("Embeddings: Groq API model %s", model)
        return
    if local:
        logger.info(
            "Embeddings: sentence-transformers model %s found locally at %s (no Hugging Face download needed)",
            model,
            local,
        )
        return
    if not _download_allowed():
        logger.warning(
            "Embeddings: %s is not in the local cache and Hugging Face download is disabled. "
            "Will use builtin lexical embeddings so vector search keeps working.",
            model,
        )
        return
    logger.warning(
        "Embeddings: %s is not in the local Hugging Face cache. "
        "Will try a download once; if huggingface.co is blocked, builtin lexical embeddings will keep vector search working.",
        model,
    )


class EmbeddingService:
    """Embedding service. Provider is selected via EMBEDDING_PROVIDER."""

    def __init__(self) -> None:
        self.provider = settings.EMBEDDING_PROVIDER
        self.model_name = settings.EMBEDDING_MODEL
        self.active_provider = ""
        self.active_model = ""
        self.active_source = ""
        self._st_model = None
        self._backend = ""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cleaned = [text if text and text.strip() else " " for text in texts]
        backend = self._ensure_backend()
        if backend == "groq":
            return self._embed_groq_many(cleaned)
        if backend == "sbert":
            return self._embed_sbert(cleaned)
        return [self._embed_lexical(text) for text in cleaned]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([self._format_query(text)])[0]

    def _format_query(self, text: str) -> str:
        name = (self.active_model or self.model_name or "").lower()
        if "qwen" in name:
            return (
                "Instruct: Given a university policy question, retrieve the official "
                "policy passage that answers it\n"
                f"Query: {text}"
            )
        return text

    def _ensure_backend(self) -> str:
        if self._backend:
            return self._backend
        provider = (self.provider or "").lower()
        if provider == "groq":
            self._activate("groq", self.model_name, "groq-api")
            return self._backend
        if provider in {"lexical", "hash"}:
            self._activate_lexical("configured")
            return self._backend
        if provider in {"sentence-transformers", "sbert", ""}:
            self._activate_sbert_or_lexical()
            return self._backend
        raise EmbeddingError(
            f"Unknown EMBEDDING_PROVIDER '{self.provider}'. "
            "Use groq, sentence-transformers, or lexical."
        )

    def _activate(self, backend: str, model: str, source: str) -> None:
        self._backend = backend
        self.active_provider = {
            "sbert": "sentence-transformers",
            "lexical": "lexical",
            "groq": "groq",
        }.get(backend, backend)
        self.active_model = model
        self.active_source = source
        logger.info(
            "Using embedding provider=%s model=%s source=%s",
            self.active_provider,
            self.active_model,
            self.active_source,
        )

    def _activate_lexical(self, reason: str) -> None:
        self._st_model = None
        self._activate("lexical", f"lexical-{LEXICAL_DIM}", f"builtin-lexical ({reason})")
        if reason == "configured":
            logger.info(
                "Embeddings: builtin lexical hashed bag-of-words (%s-d). Vector search is enabled.",
                LEXICAL_DIM,
            )
            return
        logger.warning(
            "Vector search remains enabled with builtin lexical embeddings (%s-d hashed bag-of-words). "
            "Re-index documents and scraped websites after MiniLM is available locally for best quality.",
            LEXICAL_DIM,
        )

    def _activate_sbert_or_lexical(self) -> None:
        local = find_local_embedding_model(self.model_name)
        errors: list[str] = []

        if local:
            try:
                logger.info("Loading sentence-transformers from local path %s", local)
                self._st_model = _load_sentence_transformer(str(local), local_files_only=True)
                self._activate("sbert", self.model_name, f"local:{local}")
                return
            except ImportError as exc:
                logger.warning("sentence-transformers is not installed (%s)", exc)
                self._activate_lexical("sentence-transformers-missing")
                return
            except Exception as exc:
                errors.append(f"local path {local}: {exc}")
                logger.warning("Could not load local embedding model at %s: %s", local, exc)

        hub_name = self.model_name or "all-MiniLM-L6-v2"
        try:
            logger.info("Loading sentence-transformers %s from local Hugging Face cache only", hub_name)
            self._st_model = _load_sentence_transformer(hub_name, local_files_only=True)
            self._activate("sbert", hub_name, "huggingface-cache")
            return
        except Exception as exc:
            errors.append(f"local cache: {exc}")
            logger.info("Embedding model %s is not in the local Hugging Face cache: %s", hub_name, exc)

        if _download_allowed():
            try:
                logger.info("Attempting Hugging Face download of %s", hub_name)
                self._st_model = _load_sentence_transformer(hub_name, local_files_only=False)
                self._activate("sbert", hub_name, "huggingface-hub")
                return
            except Exception as exc:
                errors.append(f"huggingface.co: {exc}")
                logger.warning(
                    "Failed to load embedding model %s from Hugging Face: %s",
                    hub_name,
                    exc,
                )
        else:
            logger.info("Skipping Hugging Face download (offline mode or EMBEDDING_ALLOW_DOWNLOAD=false)")

        logger.warning(
            "Could not load %s (%s). Falling back to builtin lexical embeddings.",
            hub_name,
            " | ".join(errors) or "unavailable",
        )
        self._activate_lexical("minilm-unavailable")

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
        try:
            model = self._st_model
            if model is None:
                raise EmbeddingError("Sentence-transformers model is not loaded.")
            vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return [_vector_to_list(vector) for vector in vectors]
        except Exception as exc:
            logger.warning("sentence-transformers encode failed (%s); using lexical embeddings for this request", exc)
            self._activate_lexical("encode-failed")
            return [self._embed_lexical(text) for text in texts]

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
