"""HTTP API (FastAPI). Locally it also serves the chat page from ./public."""

from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .chat import ChatModel, ChatService
from .config import BUNDLED_SNAPSHOT, ROOT, Settings, get_settings
from .events import EventLog
from .ingest.fetch import PoliteFetcher
from .ingest.pipeline import build_snapshot, load_snapshot, save_snapshot
from .knowledge import KnowledgeBase, KnowledgeStore
from .llm import LLMClient, LLMError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("placidway")

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=800)
    history: list[HistoryItem] = Field(default_factory=list, max_length=30)
    disclaimer_shown: bool = False

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message is empty")
        return value.strip()


class LeadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=200)
    question: str = Field(default="", max_length=1000)
    consent: bool

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL.match(value):
            raise ValueError("enter a valid email address")
        return value


class RateLimiter:
    """Sliding one-minute window per client, to protect the free-tier API key."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def load_knowledge(settings: Settings) -> KnowledgeBase:
    """Prefer a snapshot refreshed at runtime if it is newer than the one shipped with the code."""
    bundled = load_snapshot(BUNDLED_SNAPSHOT)
    refreshed = load_snapshot(settings.runtime_dir / "knowledge.json")
    if refreshed and (bundled is None or refreshed.get("built_at", "") > bundled.get("built_at", "")):
        return KnowledgeBase(refreshed)
    if bundled is None:
        raise FileNotFoundError("no knowledge snapshot found; run `python -m scripts.ingest`")
    return KnowledgeBase(bundled)


def refresh_knowledge(settings: Settings, store: KnowledgeStore) -> dict:
    """Re-crawl the source pages, save the new snapshot and swap it in without a restart."""
    with PoliteFetcher(settings.user_agent, delay_s=settings.request_delay_s) as fetcher:
        snapshot, report = build_snapshot(fetcher, previous=store.kb.snapshot)
    save_snapshot(snapshot, settings.runtime_dir / "knowledge.json")
    store.swap(KnowledgeBase(snapshot))
    return {**report, "built_at": snapshot["built_at"], "chunks": len(snapshot["chunks"])}


def create_app(
    settings: Settings | None = None,
    store: KnowledgeStore | None = None,
    llm: ChatModel | None = None,
    events: EventLog | None = None,
    refresher: Callable[[Settings, KnowledgeStore], dict] = refresh_knowledge,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or KnowledgeStore(load_knowledge(settings))
    events = events or EventLog(settings.runtime_dir)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    if llm is None and settings.llm_api_key:
        llm = LLMClient(settings)
    service = ChatService(store, llm, settings, events) if llm is not None else None

    app = FastAPI(title="PlacidWay Website Knowledge Chatbot", version="1.0.0")

    def require_admin(token: str | None) -> None:
        if not settings.admin_token:
            raise HTTPException(403, "admin endpoints are disabled (ADMIN_TOKEN is not set)")
        if not token or not hmac.compare_digest(token, settings.admin_token):
            raise HTTPException(401, "invalid admin token")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "llm_configured": service is not None,
            "model": settings.llm_model,
            "pages": len(store.kb.pages),
            "chunks": len(store.kb.chunks),
            "knowledge_built_at": store.kb.built_at,
        }

    @app.get("/api/sources")
    def sources() -> dict:
        return {
            "built_at": store.kb.built_at,
            "pages": [
                {k: p[k] for k in ("id", "title", "url", "page_type", "fetched_at", "words", "chunks")}
                for p in store.kb.pages.values()
            ],
        }

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request) -> dict:
        if service is None:
            raise HTTPException(503, "The assistant isn't configured yet (LLM_API_KEY is missing).")
        if not limiter.allow(_client_key(request)):
            raise HTTPException(429, "Too many messages in a minute. Please wait a moment and try again.")
        try:
            return service.reply(body.message, [h.model_dump() for h in body.history], body.disclaimer_shown)
        except LLMError as exc:
            log.error("LLM failure: %s", exc)
            raise HTTPException(
                503, "The assistant is temporarily unavailable. Please try again in a moment."
            ) from exc

    @app.post("/api/lead")
    def lead(body: LeadRequest, request: Request) -> dict:
        if not body.consent:
            raise HTTPException(422, "Please agree to be contacted about your quote.")
        if not limiter.allow(f"lead:{_client_key(request)}"):
            raise HTTPException(429, "Too many requests. Please try again in a minute.")
        event = events.record("leads", {"name": body.name.strip(), "email": body.email, "question": body.question.strip()})
        if settings.lead_webhook_url:
            try:
                httpx.post(settings.lead_webhook_url, json=event, timeout=10)
            except httpx.HTTPError as exc:  # the lead is already logged; don't fail the visitor
                log.warning("lead webhook failed: %s", exc)
        return {"ok": True}

    @app.post("/api/admin/refresh")
    def admin_refresh(x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        return refresher(settings, store)

    @app.get("/api/admin/unanswered")
    def admin_unanswered(x_admin_token: str | None = Header(default=None), limit: int = 50) -> dict:
        require_admin(x_admin_token)
        return {"unanswered": events.recent("unanswered", min(max(limit, 1), 500))}

    public = ROOT / "public"
    if public.exists():
        app.mount("/", StaticFiles(directory=public, html=True), name="static")
    return app


app = create_app()
