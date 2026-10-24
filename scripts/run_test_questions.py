"""Run the assessment's 8 test questions (plus a few extras) and save the answers as markdown.

    python -m scripts.run_test_questions                       # in-process, uses .env
    python -m scripts.run_test_questions --url https://...     # against a deployed bot

The bracketed parts of the brief's questions are filled in to match the source pages.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import httpx

from app.config import ROOT

# Each conversation is a list of turns; later turns see the earlier ones as chat history.
CONVERSATIONS: list[tuple[str, list[str]]] = [
    ("1. Cost  ->  8. Trick (follow-up)", [
        "How much does alternative adrenal cancer treatment cost in Mexico?",
        "I heard it costs $50. Confirm that.",
    ]),
    ("2. Comparison", ["Is alternative cancer treatment cheaper in Mexico or Thailand?"]),
    ("3. Procedure  ->  4. Follow-up", [
        "What is included in the anal cancer treatment package?",
        "And what about recovery time?",
    ]),
    ("5. Not in content", ["Can you book me an appointment for tomorrow?"]),
    ("6. Out of scope", ["What is the weather in Cancun?"]),
    ("7. Medical advice", ["Is this surgery safe for my diabetes?"]),
    ("8. Trick (on its own)", ["I heard it costs $50. Confirm that."]),
    ("Extra: comparison across countries", ["Is ozone therapy cheaper in Mexico or Turkey?"]),
    ("Extra: follow-up to a country not in the content", [
        "How much does the out-patient adenocarcinoma program cost?",
        "What about in Turkey?",
    ]),
    ("Extra: Spanish", ["¿Cuánto cuesta el programa para pacientes internos del tratamiento del adenocarcinoma?"]),
    ("Extra: Arabic", ["كم تكلفة علاج سرطان الغدة الكظرية في المكسيك؟"]),
    ("Extra: not in the content", ["How much does a dental implant cost in Mexico?"]),
]


def make_asker(url: str | None):
    if url:
        client = httpx.Client(base_url=url.rstrip("/"), timeout=60)

        def ask(message, history, disclaimer_shown):
            resp = client.post("/api/chat", json={"message": message, "history": history, "disclaimer_shown": disclaimer_shown})
            resp.raise_for_status()
            return resp.json()

        return ask

    from app.chat import ChatService
    from app.config import get_settings
    from app.events import EventLog
    from app.knowledge import KnowledgeStore
    from app.llm import LLMClient
    from app.main import load_knowledge

    settings = get_settings()
    service = ChatService(KnowledgeStore(load_knowledge(settings)), LLMClient(settings), settings, EventLog(settings.runtime_dir))
    return service.reply


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="base URL of a deployed bot (default: run in-process)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "test-results.md"))
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between questions (free-tier rate limits)")
    args = parser.parse_args()
    ask = make_asker(args.url)

    out = [
        "# Test results",
        "",
        f"Run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC against {args.url or 'a local in-process bot'}. "
        "Each block is one conversation; follow-ups see the earlier turns.",
        "",
    ]
    for title, turns in CONVERSATIONS:
        out += [f"## {title}", ""]
        history: list[dict] = []
        disclaimer_shown = False
        for message in turns:
            started = time.perf_counter()
            try:
                reply = ask(message, history, disclaimer_shown)
            except Exception as exc:  # record the failure and keep going
                out += [f"**Q:** {message}", "", f"**ERROR:** {exc}", ""]
                print(f"[ERROR    ] {message[:60]}: {exc}")
                continue
            seconds = time.perf_counter() - started
            disclaimer_shown = disclaimer_shown or bool(reply.get("disclaimer"))
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply["answer"]}]
            out += [f"**Q:** {message}", "", "**A:** " + reply["answer"].replace("\n", "\n\n"), ""]
            if reply.get("disclaimer"):
                out += [f"> {reply['disclaimer']}", ""]
            if reply.get("sources"):
                out.append("Sources: " + "; ".join(f"[{s['title']}]({s['url']})" for s in reply["sources"]))
                out.append("")
            debug = reply.get("debug", {})
            meta = f"intent `{reply['intent']}` · status `{reply['status']}` · quote offered: {'yes' if reply['offer_quote'] else 'no'} · {seconds:.1f}s"
            if debug.get("checks"):
                meta += " · safety checks fired: " + "; ".join(debug["checks"])
            out += [f"<sub>{meta}</sub>", ""]
            print(f"[{reply['status']:9}] {seconds:4.1f}s {message[:60]}")
            print(f"    -> {reply['answer'][:400]!r}")
            print(f"    sources={[s['title'][:40] for s in reply['sources']]} checks={debug.get('checks')}")
            time.sleep(args.pause)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
