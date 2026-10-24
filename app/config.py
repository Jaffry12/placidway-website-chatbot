"""Runtime settings, read from environment variables (and a local .env file when present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_SNAPSHOT = ROOT / "data" / "knowledge.json"


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so local runs need no extra dependency. Real env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Any OpenAI-compatible chat endpoint works. The default is Google Gemini's free tier.
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_router_model: str
    llm_reasoning_effort: str
    llm_timeout_s: float

    user_agent: str
    request_delay_s: float

    # Where runtime files go (refreshed snapshot, unanswered-question log, leads).
    # Vercel's filesystem is read-only apart from /tmp.
    runtime_dir: Path

    admin_token: str
    lead_webhook_url: str
    quote_url: str
    contact_url: str
    rate_limit_per_minute: int
    retrieval_k: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    on_vercel = bool(os.getenv("VERCEL"))
    default_runtime = Path("/tmp/placidway-bot") if on_vercel else ROOT / "data" / "runtime"
    return Settings(
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        llm_model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        llm_router_model=os.getenv("LLM_ROUTER_MODEL", "gemini-2.5-flash-lite"),
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "none"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "25")),
        user_agent=os.getenv(
            "CRAWLER_USER_AGENT",
            "PlacidWayAssessmentBot/1.0 (website knowledge chatbot; contact: dawoodjaffry12@gmail.com)",
        ),
        request_delay_s=float(os.getenv("CRAWLER_DELAY_S", "1.0")),
        runtime_dir=Path(os.getenv("RUNTIME_DIR", str(default_runtime))),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        lead_webhook_url=os.getenv("LEAD_WEBHOOK_URL", ""),
        quote_url=os.getenv("QUOTE_URL", "https://www.placidway.com/contact-us"),
        contact_url=os.getenv("CONTACT_URL", "https://www.placidway.com/contact-us"),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 20),
        retrieval_k=_int("RETRIEVAL_K", 6),
    )
