"""Append-only JSONL logs for unanswered questions and quote requests.

Each event also goes to stdout, so on Vercel it shows up in the deployment's runtime logs
(the files under /tmp only live as long as the serverless instance).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("placidway.events")


class EventLog:
    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.Lock()

    def record(self, kind: str, payload: dict) -> dict:
        event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
        line = json.dumps(event, ensure_ascii=False)
        log.info("%s %s", kind, line)
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with (self.directory / f"{kind}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as exc:  # logging must never break a chat reply
            log.warning("could not write %s event: %s", kind, exc)
        return event

    def recent(self, kind: str, limit: int = 50) -> list[dict]:
        path = self.directory / f"{kind}.jsonl"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            lines = deque(fh, maxlen=limit)
        return [json.loads(line) for line in reversed(lines) if line.strip()]
