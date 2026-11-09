"""Overwrite-then-delete, with backoff, resumable state and per-item error handling."""

from __future__ import annotations

import logging
import random
import string
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from reddit_wiper.client import Client
from reddit_wiper.errors import AuthError, ItemUnavailableError, RateLimitError, WiperError
from reddit_wiper.models import Item
from reddit_wiper.retry import with_backoff
from reddit_wiper.state import State

log = logging.getLogger(__name__)


def random_filler(min_words: int = 8, max_words: int = 24) -> str:
    """Meaningless replacement text, different for every item."""
    rng = random.SystemRandom()
    words = [
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 9)))
        for _ in range(rng.randint(min_words, max_words))
    ]
    return " ".join(words).capitalize() + "."


@dataclass
class Stats:
    found: int = 0
    selected: int = 0
    skipped_done: int = 0
    already_gone: int = 0
    edited: int = 0
    edit_failed: int = 0
    deleted: int = 0
    failed: int = 0

    def add(self, other: Stats) -> None:
        for name in self.__dataclass_fields__:
            if name not in ("found", "selected"):
                setattr(self, name, getattr(self, name) + getattr(other, name))


class Wiper:
    def __init__(
        self,
        client: Client,
        state: State,
        *,
        overwrite: bool = True,
        edit_delay: float = 2.0,
        delay: float = 1.0,
        max_retries: int = 6,
        backoff_base: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        filler: Callable[[], str] = random_filler,
    ) -> None:
        self.client = client
        self.state = state
        self.overwrite = overwrite
        self.edit_delay = edit_delay
        self.delay = delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.sleep = sleep
        self.filler = filler

    def _call(self, fn: Callable[[], None], what: str) -> None:
        with_backoff(
            fn, max_retries=self.max_retries, base=self.backoff_base, sleep=self.sleep, what=what
        )

    def run(self, items: Sequence[Item], on_item: Callable[[Item], None] | None = None) -> Stats:
        stats = Stats(selected=len(items))
        for index, item in enumerate(items):
            try:
                self.process(item, stats)
            finally:
                if on_item:
                    on_item(item)
            if self.delay and index < len(items) - 1:
                self.sleep(self.delay)
        return stats

    def process(self, item: Item, stats: Stats) -> None:
        name = item.fullname
        if self.state.is_done(name):
            stats.skipped_done += 1
            return
        if item.gone:
            log.info("%s is already deleted; skipping", name)
            self.state.mark_done(name, "gone")
            stats.already_gone += 1
            return

        if self.overwrite and item.editable and name not in self.state.edited:
            try:
                self._call(lambda: self.client.edit(item, self.filler()), f"edit {name}")
            except AuthError:
                raise
            except Exception as exc:
                # Archived/locked items often can't be edited but can still be deleted.
                log.warning("Could not overwrite %s (%s); deleting it anyway", name, exc)
                stats.edit_failed += 1
            else:
                self.state.mark_edited(name)
                stats.edited += 1
                if self.edit_delay:
                    self.sleep(self.edit_delay)

        try:
            self._call(lambda: self.client.delete(item), f"delete {name}")
        except AuthError:
            raise
        except ItemUnavailableError as exc:
            log.warning("%s is not available any more (%s); marking as gone", name, exc)
            self.state.mark_done(name, "gone")
            stats.already_gone += 1
        except (RateLimitError, WiperError) as exc:
            log.error("Failed to delete %s: %s", name, exc)
            self.state.mark_failed(name, str(exc))
            stats.failed += 1
        except Exception as exc:  # never let one odd item stop the whole run
            log.exception("Unexpected error deleting %s", name)
            self.state.mark_failed(name, repr(exc))
            stats.failed += 1
        else:
            log.debug("Deleted %s", name)
            self.state.mark_done(name, "deleted")
            stats.deleted += 1
