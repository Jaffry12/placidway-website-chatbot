"""The answer pipeline: route -> retrieve -> answer -> verify.

1. Router call (small model): language, intent, a standalone English question (resolves follow-ups
   like "and recovery time?"), and a keyword search query.
2. Retrieval: BM25 over the snapshot. Skipped for booking, small talk and out-of-scope messages.
3. Answer call: sees only the retrieved excerpts (not the chat history, so earlier answers can't leak
   unsourced facts in) and must return cited source ids.
4. Verification in code: invented prices, confirmed fake prices and uncited facts trigger one
   corrected retry, then a safe "I don't know" answer.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .events import EventLog
from .guard import unsupported_amounts, unsupported_doctor_names, unsupported_user_claims
from .knowledge import Hit, KnowledgeStore
from .prompts import ANSWER_SYSTEM, INTENT_NOTES, RETRY_NOTE, ROUTER_SYSTEM

log = logging.getLogger(__name__)

INTENTS = ("question", "medical_advice", "booking", "quote", "smalltalk", "out_of_scope")
NO_RETRIEVAL = {"booking", "smalltalk", "out_of_scope"}
NEEDS_CITATIONS = {"question", "medical_advice", "quote"}
STATUSES = ("answered", "partial", "not_found", "declined")
HISTORY_MESSAGES = 8

DEFAULT_DISCLAIMER = (
    "This is general information from PlacidWay's pages, not medical advice. "
    "Please talk to your own doctor about your situation."
)
FALLBACK_ANSWER = (
    "I couldn't find a reliable answer to that in the PlacidWay pages I know, so I'd rather not guess. "
    "You can request a free, personalised quote and PlacidWay's team will answer you directly."
)
CLAIM_FALLBACK = (
    "I can't confirm that figure: it doesn't match the PlacidWay pages I have, so please don't rely on it. "
    "For an exact price for your case, request a free, personalised quote from PlacidWay."
)


class ChatModel(Protocol):
    def chat_json(self, model: str, messages: list[dict], max_tokens: int = 1500, reasoning_effort: str = "") -> dict: ...


@dataclass
class Analysis:
    language: str
    intent: str
    standalone_question: str
    search_query: str


@dataclass
class Draft:
    answer: str
    status: str
    citations: list[str]
    user_claim_supported: bool | None
    medical_topic: bool
    disclaimer: str
    offer_quote: bool

    @classmethod
    def from_json(cls, data: dict) -> "Draft":
        status = str(data.get("status", "")).strip().lower()
        claim = data.get("user_claim_supported")
        citations = data.get("citations") or []
        return cls(
            answer=str(data.get("answer", "")).strip(),
            status=status if status in STATUSES else "answered",
            citations=[str(c).strip() for c in citations] if isinstance(citations, list) else [],
            user_claim_supported=claim if isinstance(claim, bool) else None,
            medical_topic=bool(data.get("medical_topic")),
            disclaimer=str(data.get("disclaimer") or "").strip(),
            offer_quote=bool(data.get("offer_quote")),
        )


def _transcript(history: list[dict]) -> str:
    lines = []
    for turn in history[-HISTORY_MESSAGES:]:
        who = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{who}: {str(turn.get('content', ''))[:1000]}")
    return "\n".join(lines)


def _sources_block(hits: list[Hit]) -> str:
    if not hits:
        return "(no matching content was found on the PlacidWay pages)"
    return "\n\n".join(
        f"[S{i}] Page: {h.chunk.page_title} ({h.chunk.page_type})\nSection: {h.chunk.section}\n{h.chunk.text}"
        for i, h in enumerate(hits, start=1)
    )


class ChatService:
    def __init__(self, store: KnowledgeStore, llm: ChatModel, settings: Settings, events: EventLog):
        self.store = store
        self.llm = llm
        self.settings = settings
        self.events = events

    # 1. understand the message ------------------------------------------------------------
    def route(self, message: str, history: list[dict]) -> Analysis:
        conversation = _transcript(history) or "(no earlier messages)"
        data = self.llm.chat_json(
            self.settings.llm_router_model,
            [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": f"Conversation so far:\n{conversation}\n\nLatest user message:\n{message}"},
            ],
            max_tokens=600,
            reasoning_effort=self.settings.llm_router_reasoning_effort,
        )
        intent = str(data.get("intent", "question")).strip().lower()
        standalone = str(data.get("standalone_question") or message).strip()
        return Analysis(
            language=str(data.get("language") or "English").strip(),
            intent=intent if intent in INTENTS else "question",
            standalone_question=standalone,
            search_query=str(data.get("search_query") or standalone).strip(),
        )

    # 3 + 4. answer and verify --------------------------------------------------------------
    def check(self, draft: Draft, analysis: Analysis, message: str, hits: list[Hit]) -> list[str]:
        problems: list[str] = []
        by_id = {f"S{i}": h for i, h in enumerate(hits, start=1)}
        cited = [c for c in draft.citations if c in by_id]
        # A price must come from a source the answer cites, not merely from somewhere in the context:
        # "$50" exists on these pages as a consultation fee, which doesn't make "it costs $50" true.
        cited_text = "\n".join(f"{by_id[c].chunk.section}\n{by_id[c].chunk.text}" for c in cited)
        invented = unsupported_amounts(draft.answer, cited_text, message)
        if invented:
            problems.append(
                f"these amounts do not appear in the sources you cited: {', '.join(invented)} "
                "(cite the source each amount comes from, or remove it)"
            )
        names = unsupported_doctor_names(draft.answer, cited_text)
        if names:
            problems.append(
                f"these doctor names are not spelled this way in the sources you cited: {', '.join(names)} "
                "(copy names exactly from the cited source)"
            )
        claims = unsupported_user_claims(message, cited_text)
        if claims and draft.user_claim_supported is not False:
            problems.append(
                f"the user's figure(s) {', '.join(claims)} do not appear in the sources you cited; say they "
                "don't match and set user_claim_supported to false"
            )
        if (
            analysis.intent in NEEDS_CITATIONS
            and draft.status in ("answered", "partial")
            and hits
            and not cited
        ):
            problems.append("you stated facts without citing any source id")
        if not draft.answer:
            problems.append("the answer was empty")
        return problems

    def draft_answer(self, message: str, analysis: Analysis, hits: list[Hit]) -> tuple[Draft, list[str]]:
        note = INTENT_NOTES.get(analysis.intent, "")
        user_content = (
            f"SOURCES:\n{_sources_block(hits)}\n\n"
            f"QUESTION (rewritten in English): {analysis.standalone_question}\n"
            f"ORIGINAL MESSAGE: {message}\n"
            + (f"NOTE: {note}\n" if note else "")
        )
        messages = [
            {"role": "system", "content": ANSWER_SYSTEM.format(language=analysis.language)},
            {"role": "user", "content": user_content},
        ]
        effort = self.settings.llm_reasoning_effort
        data = self.llm.chat_json(self.settings.llm_model, messages, reasoning_effort=effort)
        draft = Draft.from_json(data)
        problems = self.check(draft, analysis, message, hits)
        if not problems:
            return draft, []

        log.info("answer failed checks (%s); retrying once", problems)
        messages += [
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            {"role": "user", "content": RETRY_NOTE.format(problems="; ".join(problems))},
        ]
        retry = Draft.from_json(self.llm.chat_json(self.settings.llm_model, messages, reasoning_effort=effort))
        remaining = self.check(retry, analysis, message, hits)
        if not remaining:
            return retry, problems

        log.warning("answer still failed checks (%s); using safe fallback", remaining)
        claim_problem = any("user's figure" in p for p in remaining)
        safe = Draft(
            answer=CLAIM_FALLBACK if claim_problem else FALLBACK_ANSWER,
            status="declined" if claim_problem else "not_found",
            citations=[],
            user_claim_supported=False if claim_problem else None,
            medical_topic=retry.medical_topic,
            disclaimer=retry.disclaimer,
            offer_quote=True,
        )
        return safe, problems + remaining

    # the whole turn ---------------------------------------------------------------------------
    def reply(self, message: str, history: list[dict], disclaimer_shown: bool = False) -> dict:
        started = time.perf_counter()
        analysis = self.route(message, history)
        hits: list[Hit] = []
        if analysis.intent not in NO_RETRIEVAL:
            query = f"{analysis.search_query} {analysis.standalone_question}"
            hits = self.store.kb.search(query, self.settings.retrieval_k)

        draft, checks = self.draft_answer(message, analysis, hits)

        by_id = {f"S{i}": h for i, h in enumerate(hits, start=1)}
        sources: list[dict] = []
        for cid in draft.citations:
            hit = by_id.get(cid)
            if hit is None:
                continue
            existing = next((s for s in sources if s["url"] == hit.chunk.url), None)
            if existing is None:
                sources.append({"title": hit.chunk.page_title, "url": hit.chunk.url, "sections": [hit.chunk.section]})
            elif hit.chunk.section not in existing["sections"]:
                existing["sections"].append(hit.chunk.section)

        medical = draft.medical_topic or analysis.intent == "medical_advice"
        disclaimer = (draft.disclaimer or DEFAULT_DISCLAIMER) if medical and not disclaimer_shown else None
        offer_quote = (
            draft.offer_quote
            or draft.status in ("not_found", "partial")
            or analysis.intent in ("booking", "quote", "medical_advice")
        )

        if draft.status == "not_found" and analysis.intent in ("question", "medical_advice", "quote"):
            self.events.record(
                "unanswered",
                {"question": message, "standalone_question": analysis.standalone_question, "language": analysis.language},
            )

        return {
            "answer": draft.answer,
            "status": draft.status,
            "intent": analysis.intent,
            "language": analysis.language,
            "sources": sources,
            "disclaimer": disclaimer,
            "offer_quote": offer_quote,
            "quote_url": self.settings.quote_url,
            "debug": {
                "standalone_question": analysis.standalone_question,
                "search_query": analysis.search_query,
                "retrieved": [
                    {"id": f"S{i}", "chunk": h.chunk.id, "score": round(h.score, 2), "section": h.chunk.section}
                    for i, h in enumerate(hits, start=1)
                ],
                "checks": checks,
                "ms": round((time.perf_counter() - started) * 1000),
            },
        }
