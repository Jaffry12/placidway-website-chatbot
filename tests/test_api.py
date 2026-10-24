import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import FakeLLM, make_settings

ROUTER = {"language": "English", "intent": "smalltalk", "standalone_question": "Hello", "search_query": "hello"}
GREETING = {"answer": "Hi! Ask me about ITC's packages.", "status": "answered", "citations": [],
            "user_claim_supported": None, "medical_topic": False, "disclaimer": "", "offer_quote": False}


@pytest.fixture
def client(tmp_path, store, events):
    settings = make_settings(tmp_path)
    llm = FakeLLM(ROUTER, [GREETING] * 5)
    refreshed = {}

    def fake_refresh(settings, store):
        refreshed["called"] = True
        return {"changed": [], "unchanged": ["itc-price-list"], "added": [], "failed": []}

    app = create_app(settings=settings, store=store, llm=llm, events=events, refresher=fake_refresh)
    test_client = TestClient(app)
    test_client.refreshed = refreshed
    return test_client


def test_health_and_sources(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["llm_configured"] and health["pages"] == 7
    pages = client.get("/api/sources").json()["pages"]
    assert len(pages) == 7 and all("markdown" not in p for p in pages)


def test_chat_round_trip(client):
    res = client.post("/api/chat", json={"message": "Hello", "history": []})
    assert res.status_code == 200
    assert res.json()["answer"] == GREETING["answer"]


@pytest.mark.parametrize("payload", [{"message": "   "}, {"message": "x" * 801}, {"history": []}])
def test_chat_rejects_bad_input(client, payload):
    assert client.post("/api/chat", json=payload).status_code == 422


def test_chat_without_api_key_is_503(tmp_path, store, events):
    app = create_app(settings=make_settings(tmp_path, llm_api_key=""), store=store, events=events)
    res = TestClient(app).post("/api/chat", json={"message": "Hello"})
    assert res.status_code == 503


def test_rate_limit(tmp_path, store, events):
    llm = FakeLLM(ROUTER, [GREETING] * 5)
    app = create_app(settings=make_settings(tmp_path, rate_limit_per_minute=2), store=store, llm=llm, events=events)
    client = TestClient(app)
    codes = [client.post("/api/chat", json={"message": "Hello"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_lead_capture_validates_and_logs(client, events):
    assert client.post("/api/lead", json={"name": "Ana", "email": "not-an-email", "consent": True}).status_code == 422
    assert client.post("/api/lead", json={"name": "Ana", "email": "ana@example.com", "consent": False}).status_code == 422
    ok = client.post("/api/lead", json={"name": "Ana", "email": "ana@example.com", "question": "Adrenal package", "consent": True})
    assert ok.json() == {"ok": True}
    assert events.recent("leads")[0]["email"] == "ana@example.com"


def test_admin_endpoints_need_the_token(client):
    assert client.post("/api/admin/refresh").status_code == 401
    assert client.post("/api/admin/refresh", headers={"X-Admin-Token": "wrong"}).status_code == 401
    res = client.post("/api/admin/refresh", headers={"X-Admin-Token": "secret-token"})
    assert res.status_code == 200 and client.refreshed["called"]
    assert client.get("/api/admin/unanswered", headers={"X-Admin-Token": "secret-token"}).json() == {"unanswered": []}
