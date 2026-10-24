"""Fetch -> extract -> chunk -> snapshot. Used by the CLI, the admin refresh endpoint and CI."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..sources import SOURCES, Source
from .chunk import chunk_page
from .extract import extract
from .fetch import FetchError, PoliteFetcher

log = logging.getLogger(__name__)
SNAPSHOT_VERSION = 1


def build_snapshot(
    fetcher: PoliteFetcher,
    previous: dict | None = None,
    sources: tuple[Source, ...] = SOURCES,
) -> tuple[dict, dict]:
    """Return (snapshot, report). A page that fails to download keeps its previous version."""
    old_pages = {p["id"]: p for p in (previous or {}).get("pages", [])}
    old_chunks = (previous or {}).get("chunks", [])
    pages: list[dict] = []
    chunks: list[dict] = []
    report = {"changed": [], "unchanged": [], "added": [], "failed": []}

    for source in sources:
        try:
            fetched = fetcher.fetch(source.url)
        except FetchError as exc:
            log.error("keeping previous copy of %s: %s", source.id, exc)
            report["failed"].append({"id": source.id, "error": str(exc)})
            if source.id in old_pages:
                pages.append(old_pages[source.id])
                chunks.extend(c for c in old_chunks if c["page_id"] == source.id)
            continue

        page = extract(fetched.html, keep_promotions=source.page_type == "Price comparison")
        markdown = page.markdown()
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
        old = old_pages.get(source.id)
        if old is None:
            report["added"].append(source.id)
        elif old["content_hash"] != content_hash:
            report["changed"].append(source.id)
        else:
            report["unchanged"].append(source.id)

        page_chunks = chunk_page(source.id, source.url, source.page_type, page)
        pages.append(
            {
                "id": source.id,
                "url": source.url,
                "title": page.title,
                "page_type": source.page_type,
                "fetched_at": fetched.fetched_at,
                "content_hash": content_hash,
                "words": len(markdown.split()),
                "chunks": len(page_chunks),
                "dropped_widgets": page.dropped_headings,
                "markdown": markdown,
            }
        )
        chunks.extend(asdict(c) for c in page_chunks)

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
        "chunks": chunks,
    }
    return snapshot, report


def save_snapshot(snapshot: dict, path: Path, pages_dir: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # atomic swap, so a reader never sees half a file
    if pages_dir is not None:
        pages_dir.mkdir(parents=True, exist_ok=True)
        for page in snapshot["pages"]:
            header = f"<!-- {page['url']} | fetched {page['fetched_at']} -->\n\n"
            (pages_dir / f"{page['id']}.md").write_text(header + page["markdown"], encoding="utf-8")


def load_snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
