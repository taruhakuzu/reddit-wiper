"""Reading the GDPR/CCPA data export Reddit sends you (ZIP file or extracted folder).

The export contains ``comments.csv`` and ``posts.csv`` with *every* item you ever made, which lets
us reach content older than the ~1000 items Reddit listings return. Only the columns we need are
read; the IP address column is ignored on purpose.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from reddit_wiper.errors import ConfigError
from reddit_wiper.models import Item, Kind

log = logging.getLogger(__name__)

FILES: dict[str, Kind] = {"comments.csv": "comment", "posts.csv": "post"}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z")

# Some export rows are huge (long posts); lift the csv module's default field limit.
csv.field_size_limit(2**31 - 1)


def parse_date(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    log.debug("Unrecognised date in export: %r", value)
    return None


def _clean_id(value: str) -> str:
    value = (value or "").strip()
    if value[:3] in ("t1_", "t3_"):
        value = value[3:]
    return value


def rows_to_items(rows: Iterable[dict[str, str]], kind: Kind) -> Iterator[Item]:
    for row in rows:
        item_id = _clean_id(row.get("id", ""))
        if not item_id:
            continue
        url = (row.get("url") or "").strip() if kind == "post" else ""
        body = row.get("body") or ""
        permalink = (row.get("permalink") or "").strip()
        # A post is a text post if it has a body or its URL points back at itself.
        is_self = kind == "post" and (bool(body.strip()) or not url or "/comments/" in url)
        yield Item(
            id=item_id,
            kind=kind,
            subreddit=(row.get("subreddit") or "").strip(),
            created_utc=parse_date(row.get("date", "")),
            score=None,
            title=(row.get("title") or "") if kind == "post" else "",
            text=body,
            permalink=permalink.replace("https://www.reddit.com", "").replace(
                "https://reddit.com", ""
            ),
            url=url,
            is_self=is_self,
            sources={"export"},
        )


def read_export(path: Path) -> list[Item]:
    """Read comments.csv / posts.csv from a ZIP or a directory (searched recursively)."""
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"Export not found: {path}")
    items: list[Item] = []
    found_any = False
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                base = PurePosixPath(name).name.lower()
                if base in FILES:
                    found_any = True
                    with archive.open(name) as raw:
                        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                        items.extend(rows_to_items(csv.DictReader(text), FILES[base]))
    elif path.is_dir():
        for csv_path in sorted(path.rglob("*.csv")):
            base = csv_path.name.lower()
            if base in FILES:
                found_any = True
                with csv_path.open(encoding="utf-8-sig", newline="") as fh:
                    items.extend(rows_to_items(csv.DictReader(fh), FILES[base]))
    else:
        raise ConfigError(f"{path} is neither a ZIP file nor a folder.")
    if not found_any:
        raise ConfigError(f"No comments.csv or posts.csv found in {path}.")
    log.info("Read %d items from the data export", len(items))
    return items
