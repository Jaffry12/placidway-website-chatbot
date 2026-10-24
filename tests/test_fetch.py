import httpx
import pytest

from app.ingest import fetch as fetch_module
from app.ingest.fetch import FetchError, PoliteFetcher
from app.ingest.pipeline import build_snapshot
from app.sources import Source

ROBOTS = "User-agent: *\nAllow: /\nDisallow: /private/\nCrawl-delay: 10\n"
PAGE = "<div class='package-dt-page-main'><h1>Test page</h1><h2>Cost</h2><p>Price: $100</p></div>"


@pytest.fixture
def sleeps(monkeypatch):
    """Record sleeps instead of waiting, and make the clock advance only when we 'sleep'."""
    clock = {"now": 1000.0}
    recorded: list[float] = []

    def fake_sleep(seconds):
        recorded.append(round(seconds, 2))
        clock["now"] += seconds

    monkeypatch.setattr(fetch_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: clock["now"])
    return recorded


def make_fetcher(handler, delay_s=1.0):
    return PoliteFetcher("TestBot/1.0 (contact: test@example.com)", delay_s=delay_s, transport=httpx.MockTransport(handler))


def site(requests_seen, page_status=200):
    def handler(request):
        requests_seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        return httpx.Response(page_status, text=PAGE)
    return handler


def test_sends_user_agent_and_obeys_robots_disallow(sleeps):
    seen = []
    fetcher = make_fetcher(site(seen))
    page = fetcher.fetch("https://example.test/ok")
    assert page.status == 200 and "Test page" in page.html
    assert all(r.headers["User-Agent"].startswith("TestBot/1.0") for r in seen)
    with pytest.raises(FetchError, match="disallows"):
        fetcher.fetch("https://example.test/private/page")


def test_crawl_delay_from_robots_overrides_a_shorter_delay(sleeps):
    fetcher = make_fetcher(site([]), delay_s=1.0)
    fetcher.fetch("https://example.test/a")
    fetcher.fetch("https://example.test/b")
    assert fetcher.delay_for("https://example.test/a") == 10
    assert sleeps[-1] == 10  # second page waited the full Crawl-delay


def test_server_errors_are_retried_then_reported(sleeps):
    seen = []
    fetcher = make_fetcher(site(seen, page_status=503))
    with pytest.raises(FetchError, match="giving up"):
        fetcher.fetch("https://example.test/a")
    assert sum(1 for r in seen if r.url.path == "/a") == 3  # first try + 2 retries


def test_failed_page_keeps_its_previous_copy(sleeps):
    sources = (Source("ok", "Test", "https://example.test/ok"), Source("down", "Test", "https://example.test/down"))

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if request.url.path == "/down":
            return httpx.Response(404)
        return httpx.Response(200, text=PAGE)

    previous = {
        "pages": [{"id": "down", "url": "https://example.test/down", "content_hash": "old", "title": "Old copy"}],
        "chunks": [{"id": "down-01", "page_id": "down", "text": "kept"}],
    }
    snapshot, report = build_snapshot(make_fetcher(handler), previous, sources)
    assert report["added"] == ["ok"] and report["failed"][0]["id"] == "down"
    assert {p["id"] for p in snapshot["pages"]} == {"ok", "down"}
    assert any(c["id"] == "down-01" for c in snapshot["chunks"])


PLACIDWAY_STYLE = """User-agent: *
User-agent: GPTBot
Allow: /
# comment
Disallow: /video/5469/
Disallow: /admin/
Disallow: /*.pdf$
Allow: /admin/public/
Crawl-delay: 10

User-agent: StrictBot
Disallow: /
"""


def test_robots_longest_match_wins_over_a_leading_allow_all():
    from app.ingest.robots import parse_robots

    rules = parse_robots(PLACIDWAY_STYLE, "TestBot/1.0")
    assert rules.crawl_delay == 10
    assert rules.allows("https://www.placidway.com/package/7902/Adrenal")
    assert not rules.allows("https://www.placidway.com/video/5469/x")  # urllib.robotparser says allowed
    assert not rules.allows("https://www.placidway.com/admin/settings")
    assert rules.allows("https://www.placidway.com/admin/public/page")  # longer Allow wins
    assert not rules.allows("https://www.placidway.com/files/brochure.pdf")
    assert rules.allows("https://www.placidway.com/files/brochure.pdf?x=1")  # "$" anchors the end
    assert not parse_robots(PLACIDWAY_STYLE, "StrictBot/2.0").allows("https://www.placidway.com/")
