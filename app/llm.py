"""Minimal client for any OpenAI-compatible chat API (Gemini, Groq, OpenAI, OpenRouter...)."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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
        """Call `model`, falling back to the next model when one fails.

        Free-tier latency is spiky (the same call takes 1.5 s or 20 s), so the request is also
        hedged: if the current model hasn't answered after `llm_hedge_after_s`, the next model is
        asked in parallel and the first valid answer wins. The whole call is capped at `llm_budget_s`.
        """
        candidates = [model] + [m for m in self.settings.llm_fallback_models if m != model]
        now = time.monotonic()
        queue = [m for m in candidates if self._resting_until.get(m, 0) <= now] or candidates
        hedge_after = self.settings.llm_hedge_after_s
        deadline = now + self.settings.llm_budget_s
        pool = ThreadPoolExecutor(max_workers=2)
        pending: dict = {}
        errors: list[LLMError] = []

        def launch() -> None:
            candidate = queue.pop(0)
            pending[pool.submit(self._call, candidate, messages, max_tokens, reasoning_effort)] = candidate

        launch()
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMUnavailable(f"no model answered within {self.settings.llm_budget_s:.0f}s")
                can_hedge = bool(queue) and len(pending) < 2 and hedge_after > 0
                timeout = min(hedge_after, remaining) if can_hedge else remaining
                done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                if not done:
                    if can_hedge and time.monotonic() < deadline:
                        log.info("model %s is slow; asking %s in parallel", next(iter(pending.values())), queue[0])
                        launch()
                    continue
                for future in done:
                    candidate = pending.pop(future)
                    try:
                        return future.result()
                    except LLMError as exc:
                        log.warning("model %s failed (%s); trying the next one", candidate, exc)
                        errors.append(exc)
                        if queue and not pending and time.monotonic() < deadline:
                            launch()
            raise errors[-1] if errors else LLMUnavailable("no model available")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

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
