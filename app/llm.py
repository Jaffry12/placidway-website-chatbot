"""Minimal client for any OpenAI-compatible chat API (Gemini, Groq, OpenAI, OpenRouter...)."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def parse_json_object(text: str) -> dict:
    """Models sometimes wrap JSON in ``` fences or add a sentence; take the outermost object."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"model did not return JSON: {text[:200]!r}") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("model returned JSON that is not an object")
    return value


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not set")
        self.settings = settings
        self._send_reasoning = bool(settings.llm_reasoning_effort)
        self._client = httpx.Client(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout_s,
            transport=transport,
        )

    def chat_json(self, model: str, messages: list[dict], max_tokens: int = 900) -> dict:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(3):
            if self._send_reasoning:
                body["reasoning_effort"] = self.settings.llm_reasoning_effort
            else:
                body.pop("reasoning_effort", None)
            try:
                resp = self._client.post("chat/completions", json=body)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                time.sleep(1.0)
                continue
            if resp.status_code == 400 and self._send_reasoning and "reasoning" in resp.text.lower():
                # Some models don't accept reasoning_effort; drop it for the rest of this process.
                log.warning("model %s rejected reasoning_effort; retrying without it", model)
                self._send_reasoning = False
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                content = resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"unexpected LLM response shape: {resp.text[:300]}") from exc
            return parse_json_object(content)
        raise LLMError("LLM request failed after retries")
