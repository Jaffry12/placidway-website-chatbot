import re

import pytest

from app.chat import CLAIM_FALLBACK, FALLBACK_ANSWER, ChatService
from app.knowledge import KnowledgeBase, KnowledgeStore

from .conftest import FakeLLM

PACKAGE_URL = "https://www.placidway.com/package/7902/adrenal"
PRICES_URL = "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1"


def chunk(cid, page_id, url, title, section, text):
    return {"id": cid, "page_id": page_id, "url": url, "page_title": title, "page_type": "test",
            "section": section, "text": text}


MINI_SNAPSHOT = {
    "built_at": "2026-10-24T00:00:00+00:00",
    "pages": [
        {"id": "adrenal", "url": PACKAGE_URL, "title": "Adrenal Cancer Treatment Package"},
        {"id": "prices", "url": PRICES_URL, "title": "Alternative Medicine Cost Abroad"},
    ],
    "chunks": [
        chunk("adrenal-01", "adrenal", PACKAGE_URL, "Adrenal Cancer Treatment Package", "Cost of Adrenal Cancer Treatment",
              "Program Type | Duration | Total Cost (USD)\nOut-Patient Program | 3 Weeks | $18,995\nIn-Patient Program | 3 Weeks | $30,000"),
        chunk("adrenal-02", "adrenal", PACKAGE_URL, "Adrenal Cancer Treatment Package",
              "Included in the Out-Patient Program ($18,995 USD)", "Weekly lab work\nTwo meals daily"),
        chunk("prices-01", "prices", PRICES_URL, "Alternative Medicine Cost Abroad", "Cost Breakdown by Component",
              "Component | Low (USD) | High (USD)\nInitial Holistic Consultation | $50 | $250"),
    ],
}

COST_ROUTER = {
    "language": "English",
    "intent": "question",
    "standalone_question": "How much does adrenal cancer treatment cost? Out-patient, in-patient, consultation.",
    "search_query": "adrenal cancer treatment cost price program consultation",
}


@pytest.fixture
def mini_store():
    return KnowledgeStore(KnowledgeBase(MINI_SNAPSHOT))


def sid(messages, needle):
    """The S-id the bot gave the source containing `needle` (messages[1] holds the SOURCES block)."""
    sources = messages[1]["content"].split("\n\nQUESTION")[0]
    for source_id, body in re.findall(r"\[(S\d+)\] (.*?)(?=\n\n\[S\d+\] |\Z)", sources, re.S):
        if needle in body:
            return source_id
    raise AssertionError(f"{needle!r} not in the SOURCES block")


def answer(text, status="answered", cite=("$18,995",), **extra):
    """An answer-model reply; `cite` lists text snippets whose sources the model cites."""
    def respond(messages):
        return {"answer": text, "status": status, "citations": [sid(messages, n) for n in cite],
                "user_claim_supported": None, "medical_topic": False, "disclaimer": "", "offer_quote": False, **extra}
    return respond


def test_grounded_answer_returns_cited_pages(mini_store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("Out-patient costs $18,995 and in-patient $30,000 (3 weeks).")])
    reply = ChatService(mini_store, llm, settings, events).reply("How much is adrenal cancer treatment?", [])
    assert reply["status"] == "answered" and reply["debug"]["checks"] == []
    assert reply["sources"] == [{"title": "Adrenal Cancer Treatment Package", "url": PACKAGE_URL,
                                 "sections": ["Cost of Adrenal Cancer Treatment"]}]
    # The answer model only sees retrieved excerpts, never the chat history.
    assert llm.answer_calls[0][1]["content"].startswith("SOURCES:\n[S1] Page:")


def test_invented_price_triggers_retry_then_passes(mini_store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("It costs about $19,000."), answer("It starts from $18,995.")])
    reply = ChatService(mini_store, llm, settings, events).reply("How much is adrenal cancer treatment?", [])
    assert reply["answer"] == "It starts from $18,995."
    assert "not appear in the sources you cited: $19,000" in reply["debug"]["checks"][0]
    assert len(llm.answer_calls) == 2


def test_price_must_come_from_a_cited_source(mini_store, settings, events):
    # $30,000 is in the context, but the answer cites only the consultation table.
    llm = FakeLLM(COST_ROUTER, [answer("In-patient care is $30,000.", cite=["$250"]),
                                answer("In-patient care is $30,000.", cite=["$30,000"])])
    reply = ChatService(mini_store, llm, settings, events).reply("How much is in-patient care?", [])
    assert reply["sources"][0]["url"] == PACKAGE_URL
    assert "$30,000" in reply["debug"]["checks"][0]


def test_repeated_invention_falls_back_to_honest_answer(mini_store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("It costs $12,000."), answer("It costs $12,500.")])
    reply = ChatService(mini_store, llm, settings, events).reply("How much is adrenal cancer treatment?", [])
    assert reply["answer"] == FALLBACK_ANSWER
    assert reply["status"] == "not_found" and reply["offer_quote"] and reply["sources"] == []
    assert events.recent("unanswered")[0]["question"] == "How much is adrenal cancer treatment?"


def test_fake_price_is_not_confirmed_even_if_the_number_exists_elsewhere(mini_store, settings, events):
    # "$50" is on the site as a consultation fee; agreeing that the treatment costs $50 must still fail.
    agrees = answer("Yes, it costs $50.", user_claim_supported=True)
    llm = FakeLLM(COST_ROUTER, [agrees, agrees])
    reply = ChatService(mini_store, llm, settings, events).reply("I heard it costs $50. Confirm that.", [])
    assert reply["answer"] == CLAIM_FALLBACK and reply["status"] == "declined"


def test_correcting_a_fake_price_passes(mini_store, settings, events):
    corrects = answer("I can't confirm $50. The out-patient program is $18,995.", user_claim_supported=False)
    llm = FakeLLM(COST_ROUTER, [corrects])
    reply = ChatService(mini_store, llm, settings, events).reply("I heard it costs $50. Confirm that.", [])
    assert reply["answer"].startswith("I can't confirm $50") and reply["debug"]["checks"] == []


def test_uncited_facts_are_rejected(mini_store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("The program lasts 3 weeks.", cite=[]), answer("The program lasts 3 weeks.")])
    reply = ChatService(mini_store, llm, settings, events).reply("How long is the program?", [])
    assert reply["sources"] and "without citing" in reply["debug"]["checks"][0]


def test_booking_skips_retrieval_and_offers_quote(mini_store, settings, events):
    router = {"language": "English", "intent": "booking", "standalone_question": "Can you book me an appointment for tomorrow?", "search_query": "book appointment"}
    llm = FakeLLM(router, [answer("I can't book appointments, but you can request a free quote.", status="declined", cite=[])])
    reply = ChatService(mini_store, llm, settings, events).reply("Can you book me an appointment for tomorrow?", [])
    assert reply["debug"]["retrieved"] == []
    assert reply["offer_quote"] and reply["status"] == "declined"
    assert "NOTE: The user wants something only staff can do" in llm.answer_calls[0][1]["content"]


def test_medical_disclaimer_shown_once_per_conversation(mini_store, settings, events):
    router = {**COST_ROUTER, "intent": "medical_advice", "standalone_question": "Is this treatment safe with diabetes?"}
    reply_json = answer("I can't advise on your case; please ask your doctor.", status="declined", cite=[],
                        medical_topic=True, disclaimer="General information only.")
    first = ChatService(mini_store, FakeLLM(router, [reply_json]), settings, events).reply("Is this safe for my diabetes?", [])
    again = ChatService(mini_store, FakeLLM(router, [reply_json]), settings, events).reply(
        "Is this safe for my diabetes?", [], disclaimer_shown=True)
    assert first["disclaimer"] == "General information only." and first["offer_quote"]
    assert again["disclaimer"] is None


def test_follow_up_uses_history_only_in_the_router(mini_store, settings, events):
    history = [{"role": "user", "content": "How much is adrenal cancer treatment?"},
               {"role": "assistant", "content": "From $18,995."}]
    router = {**COST_ROUTER, "standalone_question": "What is the recovery time for adrenal cancer treatment?"}
    llm = FakeLLM(router, [answer("The pages don't list a recovery time.", status="not_found", cite=[])])
    reply = ChatService(mini_store, llm, settings, events).reply("And what about recovery time?", history)
    assert "User: How much is adrenal cancer treatment?" in llm.calls[0][1][1]["content"]
    assert "Assistant: From $18,995." not in llm.answer_calls[0][1]["content"]
    assert reply["status"] == "not_found" and reply["offer_quote"]


def test_price_from_a_cited_section_heading_is_accepted(mini_store, settings, events):
    router = {**COST_ROUTER, "standalone_question": "What is included in the out-patient program?",
              "search_query": "included out-patient program lab meals"}
    llm = FakeLLM(router, [answer("The out-patient program ($18,995) includes weekly lab work and two meals daily.",
                                  cite=["Weekly lab work"])])
    reply = ChatService(mini_store, llm, settings, events).reply("What does the out-patient program include?", [])
    assert reply["debug"]["checks"] == [] and reply["status"] == "answered"
