"""Re-crawl the 7 source pages and rebuild the bundled knowledge snapshot.

    python -m scripts.ingest           # fetch, rebuild data/knowledge.json, print what changed
    python -m scripts.ingest --check   # same, but exit with code 1 if anything changed (for CI)
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import BUNDLED_SNAPSHOT, ROOT, get_settings
from app.ingest.fetch import PoliteFetcher
from app.ingest.pipeline import build_snapshot, load_snapshot, save_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 when any page changed")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    previous = load_snapshot(BUNDLED_SNAPSHOT)
    with PoliteFetcher(settings.user_agent, delay_s=settings.request_delay_s) as fetcher:
        snapshot, report = build_snapshot(fetcher, previous)
    save_snapshot(snapshot, BUNDLED_SNAPSHOT, pages_dir=ROOT / "data" / "pages")

    for page in snapshot["pages"]:
        print(f"{page['id']:30} {page['words']:5} words  {page['chunks']:3} chunks  {page['title'][:60]}")
    print(f"\n{len(snapshot['chunks'])} chunks from {len(snapshot['pages'])} pages")
    for key in ("added", "changed", "failed"):
        if report[key]:
            print(f"{key}: {report[key]}")
    if report["failed"]:
        return 2
    return 1 if args.check and (report["changed"] or report["added"]) else 0


if __name__ == "__main__":
    sys.exit(main())
