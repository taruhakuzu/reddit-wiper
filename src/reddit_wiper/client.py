"""The small interface both the API (PRAW) client and the browser client implement."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from reddit_wiper.models import Item, Kind

SORTS_WITH_TIME = ("top", "controversial")
TIME_FILTERS = ("all", "year", "month", "week", "day", "hour")


class Client(Protocol):
    username: str

    def listing(self, kind: Kind, sort: str, time_filter: str | None = None) -> Iterator[Item]:
        """Yield the user's own comments or posts from one listing (max ~1000 items)."""
        ...

    def info(self, fullnames: Iterable[str]) -> Iterator[Item]:
        """Look up items by fullname (t1_/t3_). Missing ones are simply not yielded."""
        ...

    def edit(self, item: Item, text: str) -> None: ...

    def delete(self, item: Item) -> None: ...
