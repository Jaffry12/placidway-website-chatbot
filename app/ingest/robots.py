"""robots.txt rules per RFC 9309: the longest matching rule wins, and a tie goes to Allow.

Python's urllib.robotparser applies the first matching line instead. PlacidWay's file starts with
"Allow: /", so that parser would wrongly allow every path, including the ones it disallows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class RobotRules:
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow?, path pattern)
    crawl_delay: float | None = None

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None  # (pattern length, allow?)
        for allow, pattern in self.rules:
            if _matches(pattern, target):
                candidate = (len(pattern), allow)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and allow):
                    best = candidate
        return True if best is None else best[1]


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None


def parse_robots(text: str, user_agent: str) -> RobotRules:
    """Rules of the group(s) naming our product token, or of the "*" group(s) if none do."""
    token = user_agent.split("/")[0].strip().lower()
    groups: list[tuple[list[str], RobotRules]] = []
    agents: list[str] = []
    rules = RobotRules()
    in_rules = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # a user-agent line after rules starts a new group
                groups.append((agents, rules))
                agents, rules, in_rules = [], RobotRules(), False
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            in_rules = True
            if value:  # an empty "Disallow:" allows everything, so it adds no rule
                rules.rules.append((key == "allow", value))
        elif key == "crawl-delay":
            in_rules = True
            try:
                rules.crawl_delay = float(value)
            except ValueError:
                pass
    if agents:
        groups.append((agents, rules))

    mine = [r for names, r in groups if any(name != "*" and name in token for name in names)]
    chosen = mine or [r for names, r in groups if "*" in names]
    merged = RobotRules()
    for group in chosen:
        merged.rules.extend(group.rules)
        if group.crawl_delay is not None:
            merged.crawl_delay = max(merged.crawl_delay or 0, group.crawl_delay)
    return merged
