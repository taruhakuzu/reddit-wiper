"""Local JSON + CSV backup of everything that is about to be deleted."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from reddit_wiper import __version__
from reddit_wiper.models import Item

FIELDS = [
    "fullname",
    "id",
    "kind",
    "subreddit",
    "created_iso",
    "created_utc",
    "score",
    "title",
    "text",
    "url",
    "permalink",
]


def write_backup(items: Sequence[Item], directory: Path) -> tuple[Path, Path]:
    """Write ``backup-<timestamp>.json`` and ``.csv``. Returns both paths."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    json_path = directory / f"backup-{stamp}.json"
    csv_path = directory / f"backup-{stamp}.csv"
    records = [item.to_record() for item in items]

    payload = {
        "tool": "reddit-wiper",
        "version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "items": [{key: record.get(key) for key in FIELDS} for record in records],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # utf-8-sig so the file opens with the right encoding in Excel too.
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return json_path, csv_path
