from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import requests
from django.conf import settings

logger = logging.getLogger("rag")


class OllamaError(RuntimeError):
    pass


def _chat_payload(system_prompt: str, user_prompt: str, stream: bool) -> dict:
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
        "keep_alive": "24h",
        "options": {
            "temperature": settings.OLLAMA_TEMPERATURE,
            "num_predict": getattr(settings, "OLLAMA_NUM_PREDICT", 400),
            "num_ctx": getattr(settings, "OLLAMA_NUM_CTX", 4096),
        },
    }
    if "qwen" in settings.OLLAMA_MODEL.lower():
        payload["think"] = False
    return payload


def check_ollama() -> dict:
    base = settings.OLLAMA_BASE_URL
    try:
        tags = requests.get(f"{base}/api/tags", timeout=5)
        tags.raise_for_status()
        models = [item.get("name", "") for item in tags.json().get("models", [])]
        return {
            "reachable": True,
            "base_url": base,
            "model": settings.OLLAMA_MODEL,
            "model_available": any(
                name == settings.OLLAMA_MODEL or name.startswith(f"{settings.OLLAMA_MODEL}:")
                or settings.OLLAMA_MODEL.split(":")[0] in name
                for name in models
            ),
            "models": models,
        }
    except requests.RequestException as exc:
        return {
            "reachable": False,
            "base_url": base,
            "model": settings.OLLAMA_MODEL,
            "model_available": False,
            "error": str(exc),
            "models": [],
        }


def generate_answer(system_prompt: str, user_prompt: str) -> str:
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    try:
        response = requests.post(
            url,
            json=_chat_payload(system_prompt, user_prompt, stream=False),
            timeout=settings.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.exception("Ollama generation failed")
        raise OllamaError(_ollama_unreachable(exc)) from exc

    message = data.get("message") or {}
    content = (message.get("content") or data.get("response") or "").strip()
    if not content:
        raise OllamaError("Ollama returned an empty response.")
    return content


def stream_generate(system_prompt: str, user_prompt: str) -> Iterator[str]:
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    timeout = (15, settings.OLLAMA_TIMEOUT)
    try:
        with requests.post(
            url,
            json=_chat_payload(system_prompt, user_prompt, stream=True),
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed Ollama stream chunk")
                    continue
                error = data.get("error")
                if error:
                    raise OllamaError(str(error))
                message = data.get("message") or {}
                content = message.get("content") or data.get("response") or ""
                if content:
                    yield content
                if data.get("done"):
                    break
    except requests.exceptions.ChunkedEncodingError as exc:
        logger.warning("Ollama stream closed early: %s", exc)
    except requests.RequestException as exc:
        logger.exception("Ollama streaming failed")
        raise OllamaError(_ollama_unreachable(exc)) from exc


def _ollama_unreachable(exc: Exception) -> str:
    return (
        f"Could not reach Ollama at {settings.OLLAMA_BASE_URL}. "
        f"Confirm that Ollama is running and the model '{settings.OLLAMA_MODEL}' is installed. "
        f"Details: {exc}"
    )
