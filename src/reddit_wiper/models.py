"""Plain data model for a comment or post, independent of PRAW / the browser."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Kind = Literal["comment", "post"]

PREFIX = {"comment": "t1_", "post": "t3_"}


@dataclass
class Item:
    id: str  # base36 id without prefix, e.g. "abc123"
    kind: Kind
    subreddit: str = ""
    created_utc: float | None = None
    score: int | None = None
    title: str = ""
    text: str = ""
    permalink: str = ""
    url: str = ""
    is_self: bool = False
    archived: bool = False
    locked: bool = False
    gone: bool = False  # already deleted / removed
    sources: set[str] = field(default_factory=set)

    @property
    def fullname(self) -> str:
        return PREFIX[self.kind] + self.id

    @property
    def editable(self) -> bool:
        """Comments and text (self) posts with a body can be overwritten. Links/images can't."""
        if self.gone or self.archived:
            return False
        if self.kind == "comment":
            return True
        return self.is_self

    @property
    def created(self) -> datetime | None:
        if self.created_utc is None:
            return None
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc)

    def merge(self, other: Item) -> None:
        """Fill in fields we don't know yet from another record of the same item."""
        for name in ("subreddit", "title", "text", "permalink", "url"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if self.created_utc is None:
            self.created_utc = other.created_utc
        # Fresh API data is better than export data for these.
        if other.score is not None:
            self.score = other.score
        self.is_self = self.is_self or other.is_self
        self.archived = self.archived or other.archived
        self.locked = self.locked or other.locked
        self.gone = self.gone or other.gone
        self.sources |= other.sources

    def to_record(self) -> dict[str, Any]:
        """Flat dict used for backups and listings."""
        data = asdict(self)
        data.pop("sources")
        data["fullname"] = self.fullname
        data["created_iso"] = self.created.isoformat() if self.created else ""
        data["sources"] = ",".join(sorted(self.sources))
        if self.permalink and self.permalink.startswith("/"):
            data["permalink"] = "https://www.reddit.com" + self.permalink
        return data


def split_fullname(value: str) -> tuple[Kind, str] | None:
    value = value.strip()
    if value.startswith("t1_"):
        return "comment", value[3:]
    if value.startswith("t3_"):
        return "post", value[3:]
    return None


def item_from_json(data: dict[str, Any], source: str) -> Item | None:
    """Build an Item from a Reddit JSON 'thing' (the ``data`` dict of a t1/t3 child)."""
    name = data.get("name") or ""
    parsed = split_fullname(name)
    if parsed is None:
        return None
    kind, item_id = parsed
    text = data.get("body") if kind == "comment" else data.get("selftext")
    text = text or ""
    # A deleted item has no author any more. "[removed]" (by a moderator) is still yours to delete.
    gone = data.get("author") in (None, "[deleted]")
    return Item(
        id=item_id,
        kind=kind,
        subreddit=data.get("subreddit") or "",
        created_utc=_float(data.get("created_utc")),
        score=_int(data.get("score")),
        title=(data.get("title") or "") if kind == "post" else "",
        text=text,
        permalink=data.get("permalink") or "",
        url=(data.get("url") or "") if kind == "post" else "",
        is_self=bool(data.get("is_self")) if kind == "post" else False,
        archived=bool(data.get("archived")),
        locked=bool(data.get("locked")),
        gone=gone,
        sources={source},
    )


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
