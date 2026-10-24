from app.chat import CLAIM_FALLBACK, FALLBACK_ANSWER, ChatService

from .conftest import FakeLLM

COST_ROUTER = {
    "language": "English",
    "intent": "question",
    "standalone_question": "How much does alternative adrenal cancer treatment cost in Tijuana, Mexico?",
    "search_query": "cost price adrenal cancer treatment package Tijuana Mexico out-patient in-patient",
}


def answer(text, status="answered", citations=("S1",), **extra):
    return {"answer": text, "status": status, "citations": list(citations), "user_claim_supported": None,
            "medical_topic": False, "disclaimer": "", "offer_quote": False, **extra}


def service(store, settings, events, llm):
    return ChatService(store, llm, settings, events)


def test_grounded_answer_returns_cited_pages(store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("The out-patient program costs $18,995 and in-patient $30,000 (3 weeks).", citations=["S1", "S2"])])
    reply = service(store, settings, events, llm).reply("How much is adrenal cancer treatment?", [])
    assert reply["status"] == "answered"
    assert reply["sources"] and all(s["url"].startswith("https://www.placidway.com/") for s in reply["sources"])
    assert reply["debug"]["checks"] == []
    # The answer model only sees retrieved excerpts, never the chat history.
    prompt = llm.answer_calls[0][1]["content"]
    assert prompt.startswith("SOURCES:\n[S1] Page:")


def test_invented_price_triggers_retry_then_passes(store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("It costs about $19,000."), answer("It starts from $18,995.")])
    reply = service(store, settings, events, llm).reply("How much is adrenal cancer treatment?", [])
    assert reply["answer"] == "It starts from $18,995."
    assert "do not appear in SOURCES: $19,000" in reply["debug"]["checks"][0]
    assert len(llm.answer_calls) == 2


def test_repeated_invention_falls_back_to_honest_answer(store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("It costs $12,000."), answer("It costs $12,500.")])
    reply = service(store, settings, events, llm).reply("How much is adrenal cancer treatment?", [])
    assert reply["answer"] == FALLBACK_ANSWER
    assert reply["status"] == "not_found" and reply["offer_quote"] and reply["sources"] == []
    assert events.recent("unanswered")[0]["question"] == "How much is adrenal cancer treatment?"


def test_fake_price_from_user_is_never_confirmed(store, settings, events):
    router = {**COST_ROUTER, "standalone_question": "I heard alternative cancer treatment at ITC costs $50. Is that right?"}
    agrees = answer("Yes, it costs $50.", user_claim_supported=True)
    still_agrees = answer("That's right, $50.", user_claim_supported=True)
    llm = FakeLLM(router, [agrees, still_agrees])
    reply = service(store, settings, events, llm).reply("I heard it costs $50. Confirm that.", [])
    assert reply["answer"] == CLAIM_FALLBACK


def test_correcting_a_fake_price_passes(store, settings, events):
    router = {**COST_ROUTER, "standalone_question": "I heard alternative cancer treatment at ITC costs $50. Is that right?"}
    corrects = answer("I can't confirm $50. The pages list programs starting from $18,995.", user_claim_supported=False)
    llm = FakeLLM(router, [corrects])
    reply = service(store, settings, events, llm).reply("I heard it costs $50. Confirm that.", [])
    assert reply["answer"] == corrects["answer"] and reply["debug"]["checks"] == []


def test_uncited_facts_are_rejected(store, settings, events):
    llm = FakeLLM(COST_ROUTER, [answer("It starts from $18,995.", citations=[]), answer("It starts from $18,995.", citations=["S1"])])
    reply = service(store, settings, events, llm).reply("How much is adrenal cancer treatment?", [])
    assert reply["sources"] and "without citing" in reply["debug"]["checks"][0]


def test_booking_skips_retrieval_and_offers_quote(store, settings, events):
    router = {"language": "English", "intent": "booking", "standalone_question": "Can you book me an appointment for tomorrow?", "search_query": "book appointment"}
    llm = FakeLLM(router, [answer("I can't book appointments, but you can request a free quote.", status="declined", citations=[])])
    reply = service(store, settings, events, llm).reply("Can you book me an appointment for tomorrow?", [])
    assert reply["debug"]["retrieved"] == []
    assert reply["offer_quote"] and reply["status"] == "declined"
    assert "NOTE: The user wants something only staff can do" in llm.answer_calls[0][1]["content"]


def test_medical_disclaimer_shown_once_per_conversation(store, settings, events):
    router = {**COST_ROUTER, "intent": "medical_advice", "standalone_question": "Is this treatment safe with diabetes?"}
    reply_json = answer("I can't advise on your case; please ask your doctor.", status="declined", citations=[], medical_topic=True, disclaimer="General information only.")
    first = service(store, settings, events, FakeLLM(router, [reply_json])).reply("Is this safe for my diabetes?", [])
    again = service(store, settings, events, FakeLLM(router, [reply_json])).reply("Is this safe for my diabetes?", [], disclaimer_shown=True)
    assert first["disclaimer"] == "General information only." and first["offer_quote"]
    assert again["disclaimer"] is None


def test_follow_up_uses_history_only_in_the_router(store, settings, events):
    history = [{"role": "user", "content": "How much is adrenal cancer treatment?"}, {"role": "assistant", "content": "From $18,995."}]
    router = {**COST_ROUTER, "standalone_question": "What is the recovery time for alternative adrenal cancer treatment at ITC?"}
    llm = FakeLLM(router, [answer("The pages don't list a recovery time.", status="not_found", citations=[])])
    reply = service(store, settings, events, llm).reply("And what about recovery time?", history)
    router_prompt = llm.calls[0][1][1]["content"]
    assert "User: How much is adrenal cancer treatment?" in router_prompt
    assert "Assistant: From $18,995." not in llm.answer_calls[0][1]["content"]
    assert reply["status"] == "not_found" and reply["offer_quote"]
