import time

import pytest
from rich.console import Console

from conftest import FakeClient, make_item
from reddit_wiper import config
from reddit_wiper.errors import RateLimitError
from reddit_wiper.filters import Filters
from reddit_wiper.runner import RunOptions, run
from reddit_wiper.scan_cache import clear_scan, load_scan, save_scan, scan_path


def _console():
    return Console(record=True, width=120)


def test_round_trip(tmp_path):
    path = scan_path(tmp_path, "SomeUser")
    item = make_item("a", text="çok güzel", title="", sources={"api", "export"})
    save_scan(path, {item.fullname: item}, complete=False, kinds=["comment"])
    scan = load_scan(path)
    assert scan is not None and not scan.complete and scan.kinds == {"comment"}
    assert scan.items["t1_a"] == item
    clear_scan(path)
    assert load_scan(path) is None


def test_old_or_broken_scans_are_ignored(tmp_path):
    path = scan_path(tmp_path, "u")
    save_scan(path, {}, complete=True, kinds=["comment"])
    assert load_scan(path, max_age=-1) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_scan(path) is None


def _client():
    client = FakeClient()
    client.add(make_item("c1", created_utc=1.0), make_item("c2", created_utc=2.0))
    return client


def test_execute_reuses_the_dry_run_scan(no_sleep):
    client = _client()
    run(client, RunOptions(), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    scanned = client.listing_calls
    console = _console()
    result, _ = run(
        client,
        RunOptions(execute=True, limit=1),
        console,
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert client.listing_calls == scanned  # no second scan
    assert "Using the scan saved" in console.export_text()
    assert client.deletes == ["t1_c1"] and result.stats.deleted == 1


def test_rescan_option_ignores_saved_scan(no_sleep):
    client = _client()
    run(client, RunOptions(), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    scanned = client.listing_calls
    run(client, RunOptions(rescan=True), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    assert client.listing_calls == 2 * scanned


def test_saved_scan_of_comments_only_is_not_used_for_posts(no_sleep):
    client = _client()
    comments_only = RunOptions(filters=Filters(kinds=("comment",)))
    run(client, comments_only, _console(), confirm=lambda u: True, sleep=no_sleep[0])
    scanned = client.listing_calls
    run(client, RunOptions(), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    assert client.listing_calls > scanned


def test_scanning_one_kind_keeps_the_saved_other_kind(no_sleep):
    client = _client()
    client.add(make_item("p1", "post"))
    comments_only = RunOptions(filters=Filters(kinds=("comment",)))
    run(client, comments_only, _console(), confirm=lambda u: True, sleep=no_sleep[0])
    posts_only = RunOptions(filters=Filters(kinds=("post",)), rescan=True)
    run(client, posts_only, _console(), confirm=lambda u: True, sleep=no_sleep[0])
    scan = load_scan(scan_path(config.scans_dir(), "testuser"))
    assert scan is not None and scan.kinds == {"comment", "post"}
    assert set(scan.items) == {"t1_c1", "t1_c2", "t3_p1"}


class LimitedAfterNew(FakeClient):
    handles_rate_limits = True

    def listing(self, kind, sort, time_filter=None):
        self.listing_calls += 1
        if sort != "new" or kind == "post":
            raise RateLimitError("429")
        yield from super().listing(kind, sort, time_filter)


def test_partial_scan_is_used_and_saved_as_incomplete(no_sleep):
    client = LimitedAfterNew()
    client.add(make_item("c1"), make_item("c2"))
    console = _console()
    result, _ = run(client, RunOptions(), console, confirm=lambda u: True, sleep=no_sleep[0])
    assert result.stats.selected == 2
    text = " ".join(console.export_text().split())
    assert "scan stopped early" in text and "Continuing with the 2 items" in text
    scan = load_scan(scan_path(config.scans_dir(), "testuser"))
    assert scan is not None and not scan.complete and len(scan.items) == 2


def test_rate_limited_before_finding_anything_still_fails(no_sleep):
    class Limited(FakeClient):
        handles_rate_limits = True

        def listing(self, kind, sort, time_filter=None):
            raise RateLimitError("429")
            yield  # pragma: no cover

    with pytest.raises(RateLimitError, match="try again later"):
        run(Limited(), RunOptions(), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    assert load_scan(scan_path(config.scans_dir(), "testuser")) is None


def test_scan_age_is_reported(tmp_path):
    path = scan_path(tmp_path, "u")
    save_scan(path, {}, complete=True, kinds=["comment"])
    scan = load_scan(path)
    assert scan is not None and 0 <= scan.age < 5 and scan.saved_at <= time.time()
