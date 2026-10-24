import json
import time

import httpx
import pytest

from app.llm import LLMClient, LLMError, parse_json_object

from .conftest import make_settings


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def client(tmp_path, handler, **overrides) -> LLMClient:
    settings = make_settings(tmp_path, llm_fallback_models=("model-b", "model-c"), **overrides)
    return LLMClient(settings, transport=httpx.MockTransport(handler))


def test_parse_json_object_tolerates_fences_and_chatter():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 2} Hope that helps.') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json_object("no json here")


def test_falls_back_when_a_model_is_out_of_quota(tmp_path):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "answer-model":
            return httpx.Response(429, text='{"error": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}')
        return completion('{"answer": "from %s"}' % model)

    llm = client(tmp_path, handler)
    assert llm.chat_json("answer-model", [])["answer"] == "from model-b"
    # The exhausted model is rested, so the next call goes straight to a working one.
    seen.clear()
    llm.chat_json("answer-model", [])
    assert seen == ["model-b"]


def test_slow_model_is_hedged_with_the_next_one(tmp_path):
    def handler(request):
        model = json.loads(request.content)["model"]
        if model == "answer-model":
            time.sleep(1.5)
        return completion('{"answer": "from %s"}' % model)

    llm = client(tmp_path, handler, llm_hedge_after_s=0.2)
    started = time.monotonic()
    assert llm.chat_json("answer-model", [])["answer"] == "from model-b"
    assert time.monotonic() - started < 1.0


def test_rejected_reasoning_setting_is_dropped_for_that_model(tmp_path):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, text="Request contains an invalid argument.")
        return completion('{"ok": true}')

    llm = client(tmp_path, handler)
    assert llm.chat_json("answer-model", [], reasoning_effort="none") == {"ok": True}
    assert "reasoning_effort" not in bodies[-1]


def test_every_model_failing_raises(tmp_path):
    llm = client(tmp_path, lambda request: httpx.Response(503, text="overloaded"))
    with pytest.raises(LLMError):
        llm.chat_json("answer-model", [])
