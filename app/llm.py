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


class LLMUnavailable(LLMError):
    """Overloaded, rate-limited or timed out: another model may still answer."""


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
        self._no_reasoning: set[str] = set()  # models that rejected reasoning_effort
        self._resting_until: dict[str, float] = {}  # model -> monotonic time its quota should be back
        self._client = httpx.Client(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout_s,
            transport=transport,
        )

    def chat_json(self, model: str, messages: list[dict], max_tokens: int = 1500, reasoning_effort: str = "") -> dict:
        """Call `model`; if it's overloaded (free tiers often are), fall back to the next model."""
        candidates = [model] + [m for m in self.settings.llm_fallback_models if m != model]
        now = time.monotonic()
        ready = [m for m in candidates if self._resting_until.get(m, 0) <= now]
        last_error: LLMError = LLMUnavailable("every model is out of quota right now")
        for candidate in ready or candidates:
            try:
                return self._call(candidate, messages, max_tokens, reasoning_effort)
            except LLMUnavailable as exc:
                log.warning("model %s unavailable (%s); trying the next one", candidate, exc)
                last_error = exc
        raise last_error

    def _call(self, model: str, messages: list[dict], max_tokens: int, reasoning_effort: str) -> dict:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        attempt = 0
        while attempt < 2:
            send_reasoning = bool(reasoning_effort) and model not in self._no_reasoning
            if send_reasoning:
                body["reasoning_effort"] = reasoning_effort
            else:
                body.pop("reasoning_effort", None)
            try:
                resp = self._client.post("chat/completions", json=body)
            except httpx.HTTPError as exc:
                # A timeout already used its whole budget, so move on to the next model at once.
                if attempt == 1 or isinstance(exc, httpx.TimeoutException):
                    raise LLMUnavailable(f"request failed: {exc!r}") from exc
                attempt += 1
                time.sleep(1.0)
                continue
            if resp.status_code == 400 and send_reasoning:
                # Some models reject a given reasoning_effort; drop it for this model from now on.
                log.warning("model %s rejected reasoning_effort=%s; retrying without it", model, reasoning_effort)
                self._no_reasoning.add(model)
                continue
            if resp.status_code == 429:
                # Out of quota: retrying the same model won't help. Rest it (a daily cap for longer).
                daily = "PerDay" in resp.text
                self._resting_until[model] = time.monotonic() + (1800 if daily else 60)
                raise LLMUnavailable(f"quota exceeded ({'daily' if daily else 'per-minute'})")
            if resp.status_code in (500, 502, 503, 504):
                if attempt == 1:
                    raise LLMUnavailable(f"HTTP {resp.status_code}: {resp.text[:160]}")
                attempt += 1
                time.sleep(1.0)
                continue
            if resp.status_code != 200:
                raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                content = resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"unexpected LLM response shape: {resp.text[:300]}") from exc
            return parse_json_object(content)
        raise LLMUnavailable("request failed after retries")
