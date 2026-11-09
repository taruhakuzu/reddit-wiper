"""scan -> preview -> confirm -> backup -> wipe -> rescan. Shared by API and browser mode."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from reddit_wiper import config
from reddit_wiper.backup import write_backup
from reddit_wiper.client import Client
from reddit_wiper.delete import Stats, Wiper
from reddit_wiper.errors import RateLimitError
from reddit_wiper.export_import import read_export
from reddit_wiper.fetch import add_item, collect, hydrate
from reddit_wiper.filters import Filters
from reddit_wiper.models import Item
from reddit_wiper.scan_cache import DEFAULT_MAX_AGE, load_scan, save_scan, scan_path
from reddit_wiper.state import State, state_path

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    filters: Filters = field(default_factory=Filters)
    execute: bool = False
    overwrite: bool = True
    backup: bool = True
    export: Path | None = None
    rounds: int = 2
    passes: int = 5
    delay: float = 1.0
    edit_delay: float = 2.0
    show: int = 25
    limit: int | None = None  # handle at most this many items, and don't re-scan
    rescan: bool = False  # ignore a saved scan and read the listings again
    cache_max_age: float = DEFAULT_MAX_AGE  # seconds a saved scan stays usable


@dataclass
class RunResult:
    stats: Stats
    backups: list[Path] = field(default_factory=list)
    dry_run: bool = True
    cancelled: bool = False


def gather(
    client: Client,
    opts: RunOptions,
    console: Console,
    sleep: Callable[[float], None] = time.sleep,
    cached: dict[str, Item] | None = None,
) -> tuple[dict[str, Item], bool]:
    """Scan Reddit (or start from a saved scan) plus the data export.

    Returns the items and whether the listing scan was complete. If Reddit keeps rate limiting
    us part-way, the items found so far are kept rather than thrown away.
    """
    items: dict[str, Item] = {}
    limited: list[str] = []
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True
    ) as progress:
        task = progress.add_task("Scanning listings...", total=None)

        def on_progress(label: str, total: int) -> None:
            progress.update(task, description=f"Scanning {label}: {total} items found")

        if cached is not None:
            items.update(cached)
        else:
            collect(
                client,
                opts.filters.kinds,
                max_rounds=opts.rounds,
                on_progress=on_progress,
                items=items,
                sleep=sleep,
                on_rate_limited=limited.append,
            )
        if limited and not items:
            raise RateLimitError(
                f"Reddit keeps rate limiting us ({limited[0]}). Please try again later "
                "(for example in an hour). Nothing was deleted."
            )
        if limited:
            console.print(
                f"[yellow]Reddit kept rate limiting us at {limited[0]}, so the scan stopped early. "
                f"Continuing with the {len(items)} items found so far; the rest can be found by "
                "running again later with --rescan.[/]"
            )

        if opts.export:
            progress.update(task, description="Reading data export...")
            export_names = []
            for item in read_export(opts.export):
                if item.kind in opts.filters.kinds:
                    add_item(items, item)
                    if item.sources == {"export"}:
                        export_names.append(item.fullname)
            if export_names:
                progress.update(task, description=f"Looking up {len(export_names)} older items...")
                found = hydrate(client, items, export_names, sleep=sleep)
                log.info(
                    "Data export: %d items not in listings, %d of them still exist",
                    len(export_names),
                    found,
                )
    return items, not limited


def select(items: dict[str, Item], filters: Filters, state: State) -> tuple[list[Item], Counter]:
    """Items to act on (oldest first) and a count of why the rest were skipped."""
    skipped: Counter[str] = Counter()
    chosen: list[Item] = []
    for item in items.values():
        if state.is_done(item.fullname):
            skipped["already done (state file)"] += 1
            continue
        if item.gone:
            skipped["already deleted"] += 1
            continue
        reason = filters.reason_to_skip(item)
        if reason:
            skipped[f"filtered: {reason}"] += 1
            continue
        chosen.append(item)
    chosen.sort(key=lambda i: i.created_utc or 0.0)
    return chosen, skipped


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h"


def print_preview(console: Console, items: list[Item], skipped: Counter, show: int) -> None:
    comments = sum(1 for i in items if i.kind == "comment")
    posts = len(items) - comments
    console.print()
    console.print(
        f"[bold]{len(items)}[/bold] items match: "
        f"[cyan]{comments}[/cyan] comments, [cyan]{posts}[/cyan] posts"
    )
    for reason, count in sorted(skipped.items()):
        console.print(f"  skipped {count}: {reason}")
    if not items:
        return
    subs = Counter(i.subreddit or "?" for i in items)
    top = ", ".join(f"r/{s} ({n})" for s, n in subs.most_common(8))
    console.print(f"Top subreddits: {top}")
    dated = [i.created for i in items if i.created]
    if dated:
        console.print(f"Date range: {min(dated):%Y-%m-%d} - {max(dated):%Y-%m-%d}")

    if show > 0:
        table = Table(show_lines=False, header_style="bold")
        # Short fixed columns; the text gets whatever width is left, trimmed with "…".
        for col in ("type", "date", "subreddit", "score", "action"):
            table.add_column(col, no_wrap=True)
        table.add_column("text", no_wrap=True)
        # Rich shrinks every column when one is too long, so cut the text to fit beforehand.
        # ~62 = the other columns (type 7, date 10, subreddit 14, score 5, action 8) + borders.
        text_width = max(10, console.width - 62)
        for item in items[:show]:
            text = (item.title + " | " if item.title else "") + " ".join(item.text.split())
            action = "edit+del" if item.editable else "delete"
            sub = item.subreddit if len(item.subreddit) <= 14 else item.subreddit[:13] + "…"
            table.add_row(
                item.kind,
                f"{item.created:%Y-%m-%d}" if item.created else "?",
                sub,
                "" if item.score is None else str(item.score),
                action,
                text if len(text) <= text_width else text[: text_width - 1] + "…",
            )
        console.print(table)
        if len(items) > show:
            console.print(f"... and {len(items) - show} more (use --show N to list more)")


def print_report(console: Console, result: RunResult, log_file: Path | None, state: State) -> None:
    s = result.stats
    table = Table(title="Summary", show_header=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    rows = [("Found", s.found), ("Selected", s.selected)]
    if not result.dry_run:
        rows += [
            ("Overwritten", s.edited),
            ("Overwrite failed (deleted anyway)", s.edit_failed),
            ("Deleted", s.deleted),
            ("Already gone", s.already_gone),
            ("Skipped (done earlier)", s.skipped_done),
            ("Errors", s.failed),
        ]
    for label, value in rows:
        table.add_row(label, str(value))
    console.print(table)
    for path in result.backups:
        console.print(f"Backup: {path}")
    if log_file:
        console.print(f"Log:    {log_file}")
    if not result.dry_run:
        console.print(f"State:  {state.path}")
        if s.failed:
            console.print("[yellow]Some items failed. Run the same command again to retry them.")


def run(
    client: Client,
    opts: RunOptions,
    console: Console,
    confirm: Callable[[str], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[RunResult, State]:
    state = State.load(state_path(config.state_dir(), client.username), client.username)
    cache_file = scan_path(config.scans_dir(), client.username)
    kinds = set(opts.filters.kinds)
    saved = None if opts.rescan else load_scan(cache_file, opts.cache_max_age)
    if saved is not None and not kinds <= saved.kinds:
        saved = None  # the saved scan didn't look for every kind we need now
    if saved is not None:
        note = "" if saved.complete else ", cut short by rate limits"
        console.print(
            f"Using the scan saved {_ago(saved.age)} ago ({len(saved.items)} items{note}). "
            "Add --rescan to read Reddit again."
        )
        items, complete = gather(client, opts, console, sleep=sleep, cached=saved.items)
    else:
        items, complete = gather(client, opts, console, sleep=sleep)
        # Keep what an earlier scan found for kinds we didn't scan now (e.g. `--only posts`
        # mustn't throw away a saved comment scan).
        old = load_scan(cache_file, opts.cache_max_age)
        to_save = dict(items)
        if old is not None:
            kept = {k: v for k, v in old.items.items() if v.kind not in kinds}
            to_save.update(kept)
            kinds = kinds | old.kinds
            complete = complete and old.complete
        save_scan(cache_file, to_save, complete, kinds)
    selected, skipped = select(items, opts.filters, state)
    if opts.limit is not None and len(selected) > opts.limit:
        skipped[f"over --limit {opts.limit}"] += len(selected) - opts.limit
        selected = selected[: opts.limit]
    print_preview(console, selected, skipped, opts.show)

    total = Stats(found=len(items), selected=len(selected))
    result = RunResult(stats=total, dry_run=not opts.execute)
    if not opts.execute:
        console.print(
            "\n[bold green]DRY RUN[/bold green]: nothing was changed. "
            "Add --execute to really delete these items."
        )
        return result, state
    if not selected:
        console.print("Nothing to delete.")
        return result, state
    if not confirm(client.username):
        console.print("[yellow]Cancelled. Nothing was deleted.")
        result.cancelled = True
        return result, state

    wiper = Wiper(
        client,
        state,
        overwrite=opts.overwrite,
        delay=opts.delay,
        edit_delay=opts.edit_delay,
        sleep=sleep,
    )
    for pass_no in range(1, opts.passes + 1):
        if opts.backup:
            json_path, csv_path = write_backup(selected, config.backups_dir())
            result.backups += [json_path, csv_path]
            log.info("Backed up %d items to %s", len(selected), json_path)
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Pass {pass_no}: wiping", total=len(selected))
            stats = wiper.run(selected, on_item=lambda _item, p=progress, t=task: p.advance(t))
        total.add(stats)

        # Deleting items lets older ones show up in the ~1000-item listings. Look again.
        if pass_no == opts.passes or opts.limit is not None:
            break
        console.print("Re-scanning for items that were hidden behind the 1000-item limit...")
        rescan, _ = gather(client, RunOptions(filters=opts.filters, rounds=1), console, sleep=sleep)
        new_items = {k: v for k, v in rescan.items() if k not in items}
        items.update(new_items)
        save_scan(cache_file, items, complete, kinds)
        selected, _ = select(new_items, opts.filters, state)
        if not selected:
            break
        console.print(f"Found {len(selected)} more items.")
        total.found += len(new_items)
        total.selected += len(selected)
    return result, state
