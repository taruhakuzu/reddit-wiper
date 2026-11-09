"""Saved scan results, so a dry run and the following --execute don't both have to scan Reddit.

Scanning means hundreds of page loads, and Reddit rate limits that quickly. The saved file holds
your own items (it is personal data) and lives in the user data folder, never in the project.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from reddit_wiper.models import Item
from reddit_wiper.state import state_path

log = logging.getLogger(__name__)
CACHE_VERSION = 1
DEFAULT_MAX_AGE = 24 * 3600.0  # seconds


@dataclass
class Scan:
    items: dict[str, Item]
    saved_at: float  # unix time
    complete: bool  # False when rate limits cut the scan short
    kinds: set[str]  # which kinds were scanned ("comment", "post")

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.saved_at)


def scan_path(directory: Path, username: str) -> Path:
    return state_path(directory, username)


def save_scan(path: Path, items: dict[str, Item], complete: bool, kinds: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "saved_at": time.time(),
        "complete": complete,
        "kinds": sorted(kinds),
        "items": [{**asdict(i), "sources": sorted(i.sources)} for i in items.values()],
    }
    fd, tmp = tempfile.mkstemp(prefix=".scan-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_scan(path: Path, max_age: float = DEFAULT_MAX_AGE) -> Scan | None:
    """The saved scan, or None if there is none, it is too old or it can't be read."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return None
        items: dict[str, Item] = {}
        for raw in data["items"]:
            item = Item(**{**raw, "sources": set(raw.get("sources") or [])})
            items[item.fullname] = item
        scan = Scan(
            items=items,
            saved_at=float(data["saved_at"]),
            complete=bool(data["complete"]),
            kinds=set(data["kinds"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("Ignoring unreadable saved scan %s: %s", path.name, exc)
        return None
    if scan.age > max_age:
        log.debug("Saved scan is %.0f s old; scanning again", scan.age)
        return None
    return scan


def clear_scan(path: Path) -> None:
    path.unlink(missing_ok=True)
