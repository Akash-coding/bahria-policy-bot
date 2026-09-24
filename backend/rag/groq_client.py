from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import requests
from django.conf import settings

logger = logging.getLogger("rag")


class GroqError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = (getattr(settings, "GROQ_API_KEY", "") or "").strip()
    if not key:
        raise GroqError("GROQ_API_KEY is not set.")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return (getattr(settings, "GROQ_BASE_URL", "") or "https://api.groq.com/openai/v1").rstrip("/")


def _chat_payload(system_prompt: str, user_prompt: str, stream: bool) -> dict:
    model = settings.GROQ_MODEL
    max_tokens = getattr(settings, "GROQ_MAX_TOKENS", 1024)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
        "temperature": settings.GROQ_TEMPERATURE,
        "max_completion_tokens": max_tokens,
    }
    if "qwen" in (model or "").lower():
        payload["reasoning_effort"] = "none"
        payload["reasoning_format"] = "hidden"
    return payload


def _error_message(response: requests.Response, fallback: str) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text or fallback
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error) or fallback
    if error:
        return str(error)
    return fallback


_OTPM_LIMIT = re.compile(r"Limit\s+(\d+),\s+Requested\s+(\d+)", re.I)


def _should_retry_without_reasoning(status_code: int, message: str, payload: dict) -> bool:
    if not any(key in payload for key in ("reasoning_effort", "reasoning_format")):
        return False
    lowered = (message or "").lower()
    return status_code == 400 and any(
        token in lowered
        for token in ("reasoning_effort", "reasoning_format", "reasoning", "unknown parameter", "unrecognized")
    )


def _smaller_output_tokens(message: str, payload: dict) -> int | None:
    current = int(payload.get("max_completion_tokens") or 1024)
    match = _OTPM_LIMIT.search(message or "")
    if match:
        limit = int(match.group(1))
        next_max = min(current, limit) - 1
    elif "otpm" in (message or "").lower() or "output tokens" in (message or "").lower():
        next_max = min(current, 800) - 1 if current > 800 else current // 2
    else:
        return None
    next_max = max(256, next_max)
    if next_max >= current:
        return None
    return next_max


def _post_chat(url: str, payload: dict, *, stream: bool, timeout) -> requests.Response:
    response = requests.post(
        url,
        headers=_headers(),
        json=payload,
        stream=stream,
        timeout=timeout,
    )
    if response.ok:
        return response
    message = _error_message(response, f"Groq HTTP {response.status_code}")
    if _should_retry_without_reasoning(response.status_code, message, payload):
        response.close()
        payload.pop("reasoning_effort", None)
        payload.pop("reasoning_format", None)
        logger.warning("Groq rejected reasoning options; retrying without them")
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            stream=stream,
            timeout=timeout,
        )
        if response.ok:
            return response
        message = _error_message(response, f"Groq HTTP {response.status_code}")
    smaller = _smaller_output_tokens(message, payload)
    if smaller is not None:
        response.close()
        payload["max_completion_tokens"] = smaller
        logger.warning("Groq OTPM limit hit; retrying with max_completion_tokens=%s", smaller)
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            stream=stream,
            timeout=timeout,
        )
        if response.ok:
            return response
        message = _error_message(response, f"Groq HTTP {response.status_code}")
    logger.warning("Groq chat failed: %s", message)
    raise GroqError(message)


def check_groq() -> dict:
    base = _base_url()
    model = settings.GROQ_MODEL
    key = (getattr(settings, "GROQ_API_KEY", "") or "").strip()
    if not key:
        return {
            "reachable": False,
            "base_url": base,
            "model": model,
            "model_available": False,
            "error": "GROQ_API_KEY is not set.",
            "models": [],
        }
    try:
        response = requests.get(f"{base}/models", headers=_headers(), timeout=10)
        response.raise_for_status()
        models = [item.get("id", "") for item in response.json().get("data", [])]
        return {
            "reachable": True,
            "base_url": base,
            "model": model,
            "model_available": any(
                name == model or name.startswith(f"{model}:") or model in name
                for name in models
            ),
            "models": models,
        }
    except GroqError as exc:
        return {
            "reachable": False,
            "base_url": base,
            "model": model,
            "model_available": False,
            "error": str(exc),
            "models": [],
        }
    except requests.RequestException as exc:
        return {
            "reachable": False,
            "base_url": base,
            "model": model,
            "model_available": False,
            "error": str(exc),
            "models": [],
        }


def generate_answer(system_prompt: str, user_prompt: str) -> str:
    url = f"{_base_url()}/chat/completions"
    try:
        response = _post_chat(
            url,
            _chat_payload(system_prompt, user_prompt, stream=False),
            stream=False,
            timeout=settings.GROQ_TIMEOUT,
        )
        data = response.json()
    except GroqError:
        raise
    except requests.RequestException as exc:
        logger.exception("Groq generation failed")
        raise GroqError(_groq_unreachable(exc)) from exc

    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    content = (message.get("content") or data.get("response") or "").strip()
    if not content:
        raise GroqError("Groq returned an empty response.")
    return content


def stream_generate(system_prompt: str, user_prompt: str) -> Iterator[str]:
    url = f"{_base_url()}/chat/completions"
    timeout = (15, settings.GROQ_TIMEOUT)
    try:
        with _post_chat(
            url,
            _chat_payload(system_prompt, user_prompt, stream=True),
            stream=True,
            timeout=timeout,
        ) as response:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                payload = line
                if payload.startswith("data:"):
                    payload = payload[5:].strip()
                if not payload or payload == "[DONE]":
                    if payload == "[DONE]":
                        break
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed Groq stream chunk")
                    continue
                error = data.get("error")
                if error:
                    if isinstance(error, dict):
                        raise GroqError(str(error.get("message") or error))
                    raise GroqError(str(error))
                choices = data.get("choices") or []
                delta = (choices[0].get("delta") if choices else None) or {}
                message = (choices[0].get("message") if choices else None) or {}
                content = delta.get("content") or message.get("content") or ""
                reasoning = delta.get("reasoning") or message.get("reasoning") or ""
                if reasoning and not content:
                    continue
                if content:
                    yield content
                finish = (choices[0].get("finish_reason") if choices else None) or data.get("done")
                if finish and finish is not True and finish != "null":
                    if finish in {"stop", "length", True}:
                        break
    except GroqError:
        raise
    except requests.exceptions.ChunkedEncodingError as exc:
        logger.warning("Groq stream closed early: %s", exc)
    except requests.RequestException as exc:
        logger.exception("Groq streaming failed")
        raise GroqError(_groq_unreachable(exc)) from exc


def _groq_unreachable(exc: Exception) -> str:
    return (
        f"Could not reach Groq at {_base_url()}. "
        f"Confirm that GROQ_API_KEY is valid and the model '{settings.GROQ_MODEL}' is available. "
        f"Details: {exc}"
    )
