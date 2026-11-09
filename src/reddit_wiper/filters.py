"""Choosing which items to act on."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from reddit_wiper.models import Item, Kind


def _norm_sub(name: str) -> str:
    name = name.strip()
    for prefix in ("/r/", "r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
    return name.casefold()


@dataclass
class Filters:
    kinds: tuple[Kind, ...] = ("comment", "post")
    include_subreddits: set[str] = field(default_factory=set)
    exclude_subreddits: set[str] = field(default_factory=set)
    after: datetime | None = None  # only items created at/after this moment
    before: datetime | None = None  # only items created before this moment
    min_score: int | None = None
    max_score: int | None = None

    def __post_init__(self) -> None:
        self.include_subreddits = {_norm_sub(s) for s in self.include_subreddits if s.strip()}
        self.exclude_subreddits = {_norm_sub(s) for s in self.exclude_subreddits if s.strip()}
        self.after = _aware(self.after)
        self.before = _aware(self.before)

    def reason_to_skip(self, item: Item) -> str | None:
        """None if the item passes every filter, otherwise a short reason."""
        if item.kind not in self.kinds:
            return "type"
        sub = item.subreddit.casefold()
        if self.include_subreddits and sub not in self.include_subreddits:
            return "subreddit"
        if sub in self.exclude_subreddits:
            return "subreddit"
        if self.after or self.before:
            created = item.created
            if created is None:
                return "unknown date"
            if self.after and created < self.after:
                return "date"
            if self.before and created >= self.before:
                return "date"
        if self.min_score is not None or self.max_score is not None:
            if item.score is None:
                return "unknown score"
            if self.min_score is not None and item.score < self.min_score:
                return "score"
            if self.max_score is not None and item.score > self.max_score:
                return "score"
        return None

    def apply(self, items: Iterable[Item]) -> list[Item]:
        return [item for item in items if self.reason_to_skip(item) is None]


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
