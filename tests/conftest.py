"""Shared fixtures. No test ever talks to the real Reddit."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import pytest

from reddit_wiper.errors import WiperError
from reddit_wiper.models import Item, Kind


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to reach the network. Loopback (used internally by asyncio
    and the Playwright driver) is still allowed."""
    import socket

    original_connect = socket.socket.connect
    loopback = {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in loopback:
            raise RuntimeError(f"Network access is not allowed in tests ({host})")
        return original_connect(sock, address, *args, **kwargs)

    def no_create_connection(*args, **kwargs):
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", no_create_connection)


@pytest.fixture(autouse=True)
def fast_browser(monkeypatch):
    """No polite page delays in tests."""
    monkeypatch.setattr("reddit_wiper.browser.PAGE_DELAY", 0.0)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Point config and data dirs at a temp folder and clear REDDIT_* env vars."""
    monkeypatch.setenv("REDDIT_WIPER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REDDIT_WIPER_DATA_DIR", str(tmp_path / "data"))
    for key in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
        "REDDIT_USER_AGENT",
        "REDDIT_2FA",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def make_item(item_id: str, kind: Kind = "comment", **kw) -> Item:
    defaults = dict(
        subreddit="test",
        created_utc=1_600_000_000.0,
        score=1,
        text="hello",
        is_self=kind == "post",
        sources={"api"},
    )
    defaults.update(kw)
    return Item(id=item_id, kind=kind, **defaults)


@dataclass
class FakeClient:
    """In-memory stand-in for the API / browser clients.

    ``visible_limit`` simulates Reddit's listing cap: only the newest N live items are listed.
    """

    items: dict[str, Item] = field(default_factory=dict)
    username: str = "testuser"
    visible_limit: int = 1000
    fail_edit: set[str] = field(default_factory=set)
    fail_delete: dict[str, Exception] = field(default_factory=dict)
    edits: list[tuple[str, str]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    listing_calls: int = 0

    def add(self, *items: Item) -> None:
        for item in items:
            self.items[item.fullname] = item

    def _live(self, kind: Kind) -> list[Item]:
        live = [i for i in self.items.values() if i.kind == kind and not i.gone]
        live.sort(key=lambda i: i.created_utc or 0, reverse=True)
        return live[: self.visible_limit]

    def listing(self, kind: Kind, sort: str, time_filter: str | None = None) -> Iterator[Item]:
        self.listing_calls += 1
        for item in self._live(kind):
            yield Item(**{**item.__dict__, "sources": {"api"}})

    def info(self, fullnames: Iterable[str]) -> Iterator[Item]:
        for name in fullnames:
            if name in self.items:
                item = self.items[name]
                yield Item(**{**item.__dict__, "sources": {"api"}})

    def edit(self, item: Item, text: str) -> None:
        if item.fullname in self.fail_edit:
            raise WiperError("TOO_OLD: archived")
        self.edits.append((item.fullname, text))
        self.items[item.fullname].text = text

    def delete(self, item: Item) -> None:
        exc = self.fail_delete.get(item.fullname)
        if exc is not None:
            raise exc
        self.deletes.append(item.fullname)
        self.items[item.fullname].gone = True


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def no_sleep():
    calls: list[float] = []
    return calls.append, calls
