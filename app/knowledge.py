"""Knowledge base: the snapshot's chunks plus a BM25 keyword index over them.

Why BM25 and not embeddings: the corpus is 7 pages (~100 chunks), and the questions hinge on
exact terms (treatment names, "in-patient", "$18,995"). Keyword scoring is fast, needs no extra
API, and every score can be explained. Multilingual and follow-up questions are first rewritten
into an English search query by the router model (see chat.py), so the index stays English-only.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from .ingest.chunk import Chunk
from .ingest.pipeline import load_snapshot

STOPWORDS = set(
    """a an and are as at be been but by can could do does for from had has have how i if in into is it its
    me my of on or our so than that the their them there these they this to was we were what when where which
    who why will with would you your about also any more most much other some such very just""".split()
)
MAX_PER_PAGE = 4
DUPLICATE_OVERLAP = 0.8  # the same patient reviews appear on three of the pages


def _stem(word: str) -> str:
    """Deliberately tiny, but consistent: price/prices -> pric, include/included/includes -> includ."""
    if len(word) <= 4:
        return word
    if word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith("ing"):
        word = word[:-3]
    elif word.endswith("ed"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 18,995 -> 18995
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in STOPWORDS]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class KnowledgeBase:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        self.pages = {p["id"]: p for p in snapshot["pages"]}
        self.chunks = [Chunk(**c) for c in snapshot["chunks"]]
        corpus = [tokenize(c.search_text) for c in self.chunks]
        self._index = BM25Okapi(corpus) if corpus else None
        self._token_sets = [set(tokenize(c.text)) for c in self.chunks]

    @classmethod
    def from_file(cls, path: Path) -> "KnowledgeBase":
        snapshot = load_snapshot(path)
        if snapshot is None:
            raise FileNotFoundError(f"no knowledge snapshot at {path}; run `python -m scripts.ingest`")
        return cls(snapshot)

    @property
    def built_at(self) -> str:
        return self.snapshot.get("built_at", "")

    def search(self, query: str, k: int = 6) -> list[Hit]:
        terms = tokenize(query)
        if not terms or self._index is None:
            return []
        scores = self._index.get_scores(terms)
        query_terms = set(terms)
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        hits: list[Hit] = []
        chosen: list[int] = []
        per_page: dict[str, int] = {}
        for i in ranked:
            if scores[i] <= 0 or len(hits) >= k:
                break
            chunk = self.chunks[i]
            if per_page.get(chunk.page_id, 0) >= MAX_PER_PAGE:
                continue  # leave room for other pages, e.g. both sides of a comparison
            if any(self._is_redundant(i, j, query_terms) for j in chosen):
                continue  # near-copy of a passage already selected
            per_page[chunk.page_id] = per_page.get(chunk.page_id, 0) + 1
            chosen.append(i)
            hits.append(Hit(chunk, float(scores[i])))
        return hits

    def _is_redundant(self, candidate: int, chosen: int, query_terms: set[str]) -> bool:
        """Near-identical text adds nothing, unless its page is one the user named.

        The three package pages share a template ("adrenal" vs "anal" is often the only difference),
        so for "is the adrenal package cheaper than the anal one?" both copies must survive.
        """
        sa, sb = self._token_sets[candidate], self._token_sets[chosen]
        overlap = len(sa & sb) / max(1, len(sa | sb))
        if overlap < DUPLICATE_OVERLAP:
            return False
        own_title = set(tokenize(self.chunks[candidate].page_title))
        other_title = set(tokenize(self.chunks[chosen].page_title))
        return not ((own_title - other_title) & query_terms)


class KnowledgeStore:
    """Holds the live KnowledgeBase and lets the refresh endpoint swap it atomically."""

    def __init__(self, kb: KnowledgeBase):
        self._kb = kb
        self._lock = threading.Lock()

    @property
    def kb(self) -> KnowledgeBase:
        return self._kb

    def swap(self, kb: KnowledgeBase) -> None:
        with self._lock:
            self._kb = kb
