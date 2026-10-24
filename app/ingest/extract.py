"""Turn a PlacidWay HTML page into clean, heading-aware sections of text.

The pages are long (300-900 KB) and mix the page's own content with site-wide widgets that
advertise other clinics and countries. Those widgets are dropped here, so the bot can never
quote, say, a Saudi Arabian surgery price as if it came from the ITC pages.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
TEXT_BLOCKS = {"p", "dt", "dd", "blockquote", "figcaption", "caption", "pre", "address"}
INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "del", "dfn", "em", "font", "i", "ins",
    "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var",
}
BLOCK_LIKE = [
    "div", "p", "ul", "ol", "li", "table", "tr", "section", "article", "dl", "blockquote", "figure",
    "h1", "h2", "h3", "h4", "h5", "h6",
]
DROP_TAGS = [
    "script", "style", "noscript", "svg", "iframe", "template", "form", "button", "input", "select",
    "textarea", "option", "header", "footer", "nav", "video", "audio", "canvas", "picture", "img",
]
# Matched against each class name on its own, so "price-social-block-ctm" (the price box) survives.
DROP_CLASS = re.compile(r"^(breadcrumb\S*|modal\S*|social-\S+|share\S*|cookie\S*|g-recaptcha|mobile-search\S*|treatments?-list\S*)$", re.I)
CONTENT_ROOTS = ("div.package-dt-page-main", "main", "article", "body")

# Headings of widgets that are not about the page's own subject: booking/quote forms,
# cross-promotion of other clinics and countries, accreditation logos, footer blocks.
NOISE_HEADING = re.compile(
    r"""^(
        book\ a\ consultation | no\ slots\ available | request\ a\ video\ call
      | confirm\ your\ virtual\ consultation | request\ (a|your)\ (free\ )?(quote|consultation)
      | explore\b.*(stories|testimonials) | learn\ more\ with | transformative\b
      | trusted\ medical\ tourism\ platform | follow\ placidway | about\ placidway | news\ &\ media
      | customize\ your\ experience | for\ providers | consulting\ & | the\ placidway\ network
      | language\ sites | related\ experiences | discuss\ your\ .*options | get\ more\ information | (jci|iso|cofepris|turkey\ moh|dhcc|nabh|korea\ mohw)$
    )""",
    re.I | re.X,
)

# Cross-promotion of other clinics and packages. Kept only on the price-comparison page,
# where comparing clinics is the point; on ITC's own pages they are ads for someone else.
PROMO_HEADING = re.compile(r"^(unlock the best|.*explore top medical centers|.*solutions near you)", re.I)

# Widget titles that carry no content of their own; the text under them stays in the current section.
IGNORED_HEADING = re.compile(r"^(medical center reviews|package price)$", re.I)

UI_TEXT = {
    "share this listing", "copy link", "chat with center", "read more", "read less", "view more",
    "view all", "see more", "send", "×", "all", "watch video", "book now", "get a free quote",
    "request a free quote", "actions", "next", "previous", "close", "show more", "show less",
    "medical center reviews", "speak with a specialist today", "contact placidway for a free quote",
    "write a review", "messenger whatsapp email free call", "contact itc for consultation",
}
COUNTER = re.compile(r"^\d[\d.,]*\s?[kKmM]?$")  # view/like counters such as "23.2K"
TEMPLATE_ARTIFACT = re.compile(r"<\?|\?>|=\$[A-Za-z_]")  # unrendered PHP, e.g. "=$priceRange?>"
MONEY_ONLY = re.compile(r"^(US\$|USD|\$|€|£)\s?\d[\d,.]*(\s?(USD|EUR|GBP))?$", re.I)
MONEY_THEN_LABEL = re.compile(r"^((?:US\$|\$|€|£)\s?\d[\d,.]*)\s+([A-Za-z][A-Za-z ]{2,30})$")
LABEL_THEN_MONEY = re.compile(r"^([A-Za-z][A-Za-z ]{2,30}?)\s+((?:US\$|\$|€|£)\s?\d[\d,.]*)$")


@dataclass
class Section:
    path: list[str]  # heading trail from the page title down to this section
    lines: list[str] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return " > ".join(self.path[1:]) if len(self.path) > 1 else (self.path[0] if self.path else "")


@dataclass
class ExtractedPage:
    title: str
    sections: list[Section]
    dropped_headings: list[str]
    skipped_artifacts: int

    def markdown(self) -> str:
        out = [f"# {self.title}", ""]
        for section in self.sections:
            if len(section.path) > 1:
                out.append(f"{'#' * min(len(section.path), 6)} {section.path[-1]}")
            out.extend(section.lines)
            out.append("")
        return "\n".join(out).strip() + "\n"


def _clean(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"\s+([,.;:!?)])", r"\1", text).strip()


def _text(el: Tag) -> str:
    return _clean(el.get_text(" "))


def _card_heading(el: Tag) -> Tag | None:
    """Return the heading of a small "card" whose heading is not its first text, else None."""
    if el.name not in ("div", "li", "article", "section"):
        return None
    headings = el.find_all(["h2", "h3", "h4"])
    if len(headings) != 1 or len(el.get_text(" ", strip=True)) > 1500:
        return None
    heading = headings[0]
    first_text = next((s for s in el.find_all(string=True) if s.strip()), None)
    if first_text is None or heading in first_text.parents:
        return None
    return heading


class _Walker:
    """Walks the DOM in document order and emits headings and text lines."""

    def __init__(self) -> None:
        self.blocks: list[tuple[str, int, str]] = []  # (kind, heading level, text)

    def line(self, text: str) -> None:
        text = _clean(text)
        if text:
            self.blocks.append(("text", 0, text))

    def row(self, tr: Tag) -> None:
        cells: list[str] = []
        for cell in tr.find_all(["td", "th"], recursive=False):
            if cell.find("li"):  # keep list items apart: "Breast Cancer; Prostate Cancer; ..."
                value = "; ".join(_text(li) for li in cell.find_all("li"))
            elif cell.find(["p", "div", "br"]):
                value = _clean("; ".join(s.strip() for s in cell.find_all(string=True) if s.strip()))
            else:
                value = _text(cell)
            if not value or value.lower() in UI_TEXT:
                continue
            if cells and cells[-1] == value:  # "Breast Cancer | Breast Cancer" -> one cell
                continue
            cells.append(value)
        if cells:
            self.line(" | ".join(cells))

    def walk(self, el: Tag) -> None:
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                self.line(" ".join(buffer))
                buffer.clear()

        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buffer.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in HEADINGS:
                flush()
                heading = _text(child)
                if heading and not IGNORED_HEADING.match(heading):
                    self.blocks.append(("heading", HEADINGS[name], heading))
            elif name == "br":
                flush()
            elif name in INLINE and not child.find(BLOCK_LIKE):
                buffer.append(child.get_text(" "))
            elif name == "tr":
                flush()
                self.row(child)
            elif name == "li" and not child.find(BLOCK_LIKE):
                flush()
                self.line(f"- {_text(child)}")
            elif name in TEXT_BLOCKS and not child.find(["h1", "h2", "h3", "h4", "table", "ul", "ol"]):
                flush()
                self.line(_text(child))
            elif (card_heading := _card_heading(child)) is not None:
                # Cards print the price above the title. Emit the title first so the price
                # lands in the card's own section instead of the previous card's.
                flush()
                self.blocks.append(("heading", HEADINGS[card_heading.name], _text(card_heading)))
                card_heading.extract()
                self.walk(child)
            else:
                flush()
                self.walk(child)
        flush()


def _postprocess(lines: list[str]) -> tuple[list[str], int]:
    """Drop UI noise, then join labels to their values ("Price Range:" + "$18995 - $18995")."""
    kept: list[str] = []
    artifacts = 0
    for line in lines:
        bare = line.lstrip("- ").strip()
        if bare.lower() in UI_TEXT or COUNTER.match(bare):
            continue
        if TEMPLATE_ARTIFACT.search(line):
            artifacts += 1
            continue
        if kept and kept[-1] == line:
            continue
        if (m := MONEY_THEN_LABEL.match(line)) is not None:
            line = f"{m.group(2)}: {m.group(1)}"  # "$18,995 Price starting from"
        elif (m := LABEL_THEN_MONEY.match(line)) is not None:
            line = f"{m.group(1)}: {m.group(2)}"  # "Price starting from $24,000"
        kept.append(line)

    merged: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        nxt = kept[i + 1] if i + 1 < len(kept) else None
        is_label = len(line) <= 40 and not any(ch.isdigit() for ch in line)
        if nxt is not None and line.endswith(":") and len(line.split()) <= 4 and " | " not in nxt:
            merged.append(f"{line} {nxt}")
            i += 2
            continue
        if nxt is not None and MONEY_ONLY.match(line) and len(nxt.split()) <= 5 and not any(c.isdigit() for c in nxt):
            merged.append(f"{nxt}: {line}")  # "$18,995" + "Price starting from"
            i += 2
            continue
        if nxt is not None and is_label and len(line.split()) <= 5 and MONEY_ONLY.match(nxt):
            merged.append(f"{line}: {nxt}")  # "Price starting from" + "$50,000"
            i += 2
            continue
        merged.append(line)
        i += 1
    return merged, artifacts


def extract(html: str, keep_promotions: bool = False) -> ExtractedPage:
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    for tag in soup(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(class_=DROP_CLASS):
        if not tag.decomposed:
            tag.decompose()

    root = next((found for sel in CONTENT_ROOTS if (found := soup.select_one(sel)) is not None), soup)
    walker = _Walker()
    walker.walk(root)

    h1 = next((text for kind, level, text in walker.blocks if kind == "heading" and level == 1), "")
    title = h1 or (_clean(title_tag.get_text()) if title_tag else "Untitled page")

    # A heading repeated on every card (the clinic name under each of 129 videos) is a label, not a section.
    repeats = Counter(text for kind, level, text in walker.blocks if kind == "heading" and level > 1)

    sections: list[Section] = [Section(path=[title])]
    stack: list[tuple[int, str]] = []
    for kind, level, text in walker.blocks:
        if kind == "heading" and level > 1 and repeats[text] >= 3:
            kind = "text"
        if kind == "heading":
            if level == 1:
                stack = []
                sections.append(Section(path=[title]))
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            sections.append(Section(path=[title] + [t for _, t in stack]))
        else:
            sections[-1].lines.append(text)

    kept: list[Section] = []
    dropped: list[str] = []
    artifacts = 0
    for section in sections:
        noisy = [
            h for h in section.path[1:]
            if NOISE_HEADING.match(h) or (not keep_promotions and PROMO_HEADING.match(h))
        ]
        if noisy:
            if section.path[-1] == noisy[0]:
                dropped.append(noisy[0])
            continue
        section.lines, skipped = _postprocess(section.lines)
        artifacts += skipped
        section.lines = [ln for ln in section.lines if ln != title]
        if section.lines:
            kept.append(section)
    return ExtractedPage(title=title, sections=kept, dropped_headings=dropped, skipped_artifacts=artifacts)
