"""Split extracted pages into retrieval chunks that follow the page's own headings.

Rules:
- A chunk never mixes two top-level (H2) sections, so a price table is never glued to an
  unrelated section of the page.
- Small sub-sections under the same H2 (FAQ entries, video cards) are packed together.
- Long sections are split on line boundaries; a split table repeats its header row, so
  "$30,000" never loses its "Program Type | Duration | Total Cost (USD)" column names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import ExtractedPage

MAX_WORDS = 180


@dataclass
class Chunk:
    id: str
    page_id: str
    url: str
    page_title: str
    page_type: str
    section: str
    text: str

    @property
    def search_text(self) -> str:
        # Headings are the most telling words on these pages ("Included in the In-Patient Program"),
        # so the section label counts twice.
        return f"{self.page_title}\n{self.section}\n{self.section}\n{self.text}"


def _words(text: str) -> int:
    return len(text.split())


def _split_long_line(line: str, max_words: int) -> list[str]:
    if _words(line) <= max_words:
        return [line]
    parts: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", line):
        if current and _words(current) + _words(sentence) > max_words:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _pieces(lines: list[str], max_words: int) -> list[list[str]]:
    """Pack lines into pieces of at most max_words, repeating a table's header row on a split."""
    expanded = [part for line in lines for part in _split_long_line(line, max_words)]
    pieces: list[list[str]] = []
    current: list[str] = []
    count = 0
    table_header: str | None = None
    for line in expanded:
        is_row = " | " in line
        if not is_row:
            table_header = None
        elif table_header is None:
            table_header = line
        size = _words(line)
        if current and count + size > max_words:
            pieces.append(current)
            current, count = [], 0
            if is_row and table_header and line != table_header:
                current, count = [table_header], _words(table_header)
        current.append(line)
        count += size
    if current:
        pieces.append(current)
    return pieces


def chunk_page(
    page_id: str, url: str, page_type: str, page: ExtractedPage, max_words: int = MAX_WORDS
) -> list[Chunk]:
    groups: list[dict] = []  # {"top": str, "label": str, "lines": [...], "words": int, "sections": int}
    for section in page.sections:
        top = section.path[1] if len(section.path) > 1 else "Overview"
        # Sub-sections keep their own heading as the first line (an FAQ question, a video title).
        lines = ([section.path[-1]] if len(section.path) > 2 else []) + section.lines
        for piece in _pieces(lines, max_words):
            size = sum(_words(line) for line in piece)
            last = groups[-1] if groups else None
            if last and last["top"] == top and last["words"] + size <= max_words:
                last["lines"].extend(piece)
                last["words"] += size
                last["sections"] += 1
            else:
                groups.append({"top": top, "label": section.heading or top, "lines": list(piece), "words": size, "sections": 1})

    chunks: list[Chunk] = []
    for n, group in enumerate(groups, start=1):
        label = group["top"] if group["sections"] > 1 else group["label"]
        chunks.append(
            Chunk(
                id=f"{page_id}-{n:02d}",
                page_id=page_id,
                url=url,
                page_title=page.title,
                page_type=page_type,
                section=label,
                text="\n".join(group["lines"]),
            )
        )
    return chunks

