"""Exponential backoff for rate-limited calls."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from reddit_wiper.errors import RateLimitError

log = logging.getLogger(__name__)
T = TypeVar("T")

MAX_WAIT = 600.0


def backoff_delay(attempt: int, base: float, retry_after: float | None = None) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based)."""
    if retry_after and retry_after > 0:
        return min(retry_after + random.uniform(0, 1), MAX_WAIT)
    return min(base * (2**attempt) + random.uniform(0, base), MAX_WAIT)


def with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int = 6,
    base: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    what: str = "request",
) -> T:
    """Call ``fn``; on :class:`RateLimitError` wait (exponentially longer) and retry."""
    attempt = 0
    while True:
        try:
            return fn()
        except RateLimitError as exc:
            if attempt >= max_retries:
                raise
            wait = backoff_delay(attempt, base, exc.retry_after)
            log.warning(
                "Rate limited during %s; waiting %.0fs (retry %d/%d)",
                what,
                wait,
                attempt + 1,
                max_retries,
            )
            sleep(wait)
            attempt += 1
