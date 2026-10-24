"""Checks against the real snapshot in data/knowledge.json."""

from app.knowledge import tokenize
from app.sources import SOURCES


def top_pages(kb, query, k=3):
    return [h.chunk.page_id for h in kb.search(query, k)]


def test_snapshot_covers_exactly_the_seven_brief_urls(kb):
    assert {p["url"] for p in kb.pages.values()} == {s.url for s in SOURCES}
    assert all(c.url in {s.url for s in SOURCES} for c in kb.chunks)


def test_tokenizer_normalises_prices_and_plurals():
    assert tokenize("Prices: $18,995 included") == tokenize("price 18995 includes") == ["pric", "18995", "includ"]


def test_package_questions_find_their_own_page(kb):
    assert top_pages(kb, "cost price alternative adrenal cancer treatment Tijuana")[0] == "adrenal-package"
    assert "anal-package" in top_pages(kb, "anal cancer package included out-patient in-patient program", 4)
    assert "adenocarcinoma-package" in top_pages(kb, "adenocarcinoma in-patient program cost")


def test_price_list_and_comparison_pages(kb):
    assert "itc-price-list" in top_pages(kb, "breast cancer price list ITC")
    hits = kb.search("ozone therapy price cheaper Mexico Turkey", 3)
    assert hits[0].chunk.page_id == "alternative-medicine-prices"
    assert "Turkey" in hits[0].chunk.text and "Mexico" in hits[0].chunk.text


def test_comparisons_keep_both_near_identical_package_pages(kb):
    pages = top_pages(kb, "compare cost adrenal anal cancer package cheaper", 6)
    assert "adrenal-package" in pages and "anal-package" in pages


def test_identical_reviews_are_not_returned_twice(kb):
    texts = [h.chunk.text for h in kb.search("Frank H review airport pickup medical director", 6)]
    assert len(texts) == len(set(texts))


def test_comparison_page_prices_stay_with_their_cards(kb):
    croatia = [c for c in kb.chunks if "Murter, Croatia" in c.text and c.page_id == "alternative-medicine-prices"]
    assert croatia, "Croatia card missing from the comparison page"
    for chunk in croatia:
        lines = chunk.text.splitlines()
        start = lines.index(next(line for line in lines if "Murter, Croatia" in line))
        card = lines[start : start + 4]
        assert not any("$24,000" in line for line in card)


def test_unrelated_questions_score_low(kb):
    assert kb.search("weather forecast", 5) == []
