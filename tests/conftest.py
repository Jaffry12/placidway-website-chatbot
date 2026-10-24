from __future__ import annotations

from pathlib import Path

import pytest

from app.config import BUNDLED_SNAPSHOT, Settings
from app.events import EventLog
from app.knowledge import KnowledgeBase, KnowledgeStore

ROUTER_MODEL = "router-model"
ANSWER_MODEL = "answer-model"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        llm_api_key="test-key",
        llm_base_url="https://llm.invalid/v1/",
        llm_model=ANSWER_MODEL,
        llm_router_model=ROUTER_MODEL,
        llm_reasoning_effort="",
        llm_router_reasoning_effort="",
        llm_fallback_models=(),
        llm_hedge_after_s=0,
        llm_budget_s=10,
        llm_timeout_s=5,
        user_agent="test-agent",
        request_delay_s=0,
        runtime_dir=tmp_path / "runtime",
        admin_token="secret-token",
        lead_webhook_url="",
        quote_url="https://www.placidway.com/contact-us",
        contact_url="https://www.placidway.com/contact-us",
        rate_limit_per_minute=100,
        retrieval_k=6,
    )
    values.update(overrides)
    return Settings(**values)


class FakeLLM:
    """Scripted stand-in for the model: one router reply, then answer replies in order."""

    def __init__(self, router: dict, answers: list[dict]):
        self.router = router
        self.answers = list(answers)
        self.calls: list[tuple[str, list[dict]]] = []

    def chat_json(self, model: str, messages: list[dict], max_tokens: int = 1500, reasoning_effort: str = "") -> dict:
        self.calls.append((model, messages))
        if model == ROUTER_MODEL:
            return self.router
        reply = self.answers.pop(0)
        return reply(messages) if callable(reply) else reply

    @property
    def answer_calls(self) -> list[list[dict]]:
        return [messages for model, messages in self.calls if model == ANSWER_MODEL]


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_file(BUNDLED_SNAPSHOT)


@pytest.fixture
def store(kb) -> KnowledgeStore:
    return KnowledgeStore(kb)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def events(settings) -> EventLog:
    return EventLog(settings.runtime_dir)
