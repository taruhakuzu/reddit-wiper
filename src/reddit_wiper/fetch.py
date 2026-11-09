"""Finding the user's comments and posts through listings (each capped at ~1000 items)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from reddit_wiper.client import SORTS_WITH_TIME, TIME_FILTERS, Client
from reddit_wiper.errors import AuthError, RateLimitError, WiperError
from reddit_wiper.models import Item, Kind
from reddit_wiper.retry import with_backoff

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int], None]


def listing_plan() -> list[tuple[str, str | None]]:
    """Every listing we can ask Reddit for. Different sorts surface different subsets."""
    plan: list[tuple[str, str | None]] = [("new", None), ("hot", None)]
    for sort in SORTS_WITH_TIME:
        plan.extend((sort, t) for t in TIME_FILTERS)
    return plan


def add_item(items: dict[str, Item], item: Item) -> bool:
    """Insert or merge ``item``. Returns True if it was new."""
    existing = items.get(item.fullname)
    if existing is None:
        items[item.fullname] = item
        return True
    existing.merge(item)
    return False


def collect(
    client: Client,
    kinds: Iterable[Kind],
    max_rounds: int = 2,
    on_progress: ProgressCallback | None = None,
    items: dict[str, Item] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_rate_limited: Callable[[str], None] | None = None,
) -> dict[str, Item]:
    """Walk all listings, de-duplicating by fullname, until a round finds nothing new.

    If Reddit keeps rate limiting us, the scan stops. With ``on_rate_limited`` it is called with
    the listing label and the items found so far are returned; without it RateLimitError is raised.
    """
    items = {} if items is None else items
    kinds = list(kinds)
    # A client may ask for a lighter plan (browser mode: every page load counts against limits).
    plan = getattr(client, "listing_plan", None) or listing_plan()
    # A client that already retries each request itself shouldn't be retried again here.
    client_retries = getattr(client, "handles_rate_limits", False)
    for round_no in range(1, max_rounds + 1):
        new_this_round = 0
        for kind in kinds:
            for sort, time_filter in plan:
                label = f"{kind}s/{sort}" + (f"/{time_filter}" if time_filter else "")

                def read_listing(
                    kind: Kind = kind,
                    sort: str = sort,
                    tf: str | None = time_filter,
                    label: str = label,
                ) -> int:
                    new = 0
                    for item in client.listing(kind, sort, tf):
                        if add_item(items, item):
                            new += 1
                        if on_progress:
                            on_progress(label, len(items))
                    return new

                try:
                    if client_retries:
                        new_this_round += read_listing()
                    else:
                        new_this_round += with_backoff(read_listing, sleep=sleep, what=label)
                except AuthError:
                    raise
                except RateLimitError as exc:
                    # Still limited after every retry: moving on to the next listing would only
                    # keep hitting Reddit. Stop and let the user try again later.
                    if on_rate_limited is not None:
                        log.warning("Rate limited on %s; stopping the scan early", label)
                        on_rate_limited(label)
                        return items
                    raise RateLimitError(
                        f"Reddit keeps rate limiting us ({label}). Stopping the scan; please try "
                        "again later (for example in an hour). Nothing was deleted."
                    ) from exc
                except WiperError as exc:
                    log.warning("Could not read listing %s: %s", label, exc)
        log.debug("Listing round %d: %d new, %d total", round_no, new_this_round, len(items))
        if new_this_round == 0:
            break
    return items


def hydrate(
    client: Client,
    items: dict[str, Item],
    fullnames: Iterable[str],
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Refresh items (typically from a data export) with live data. Returns how many were found.

    Items Reddit no longer returns at all are marked as gone.
    """
    wanted = [name for name in fullnames if name in items]
    found: set[str] = set()
    for start in range(0, len(wanted), 100):
        batch = wanted[start : start + 100]
        fresh_items = with_backoff(
            lambda batch=batch: list(client.info(batch)), sleep=sleep, what="lookup"
        )
        for fresh in fresh_items:
            if fresh.fullname in items:
                found.add(fresh.fullname)
                items[fresh.fullname].merge(fresh)
    # A client that can't look up every kind (browser mode: no comment lookup) can't tell us an
    # item is gone just because it wasn't returned.
    complete = getattr(client, "info_is_complete", True)
    for name in wanted:
        if name not in found and (complete or name.startswith("t3_")):
            items[name].gone = True
    return len(found)
