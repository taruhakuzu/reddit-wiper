from conftest import FakeClient, make_item
from reddit_wiper.errors import RateLimitError, WiperError
from reddit_wiper.fetch import collect, hydrate, listing_plan


def test_listing_plan_covers_all_sorts():
    plan = listing_plan()
    assert ("new", None) in plan and ("hot", None) in plan
    assert ("top", "all") in plan and ("controversial", "hour") in plan
    assert len(plan) == 2 + 2 * 6


def test_collect_dedupes_and_stops_when_nothing_new(fake_client):
    fake_client.add(make_item("a"), make_item("b"), make_item("p", "post"))
    items = collect(fake_client, ["comment", "post"], max_rounds=5)
    assert set(items) == {"t1_a", "t1_b", "t3_p"}
    # round 1 finds everything, round 2 finds nothing new -> stop after 2 rounds
    assert fake_client.listing_calls == 2 * 2 * len(listing_plan())


def test_collect_respects_kinds(fake_client):
    fake_client.add(make_item("a"), make_item("p", "post"))
    assert set(collect(fake_client, ["post"])) == {"t3_p"}


class FlakyClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.raised = 0

    def listing(self, kind, sort, time_filter=None):
        if self.raised < 2:
            self.raised += 1
            raise RateLimitError("slow down", retry_after=1)
        yield from super().listing(kind, sort, time_filter)


def test_collect_backs_off_on_rate_limit(no_sleep):
    sleep, calls = no_sleep
    client = FlakyClient()
    client.add(make_item("a"))
    items = collect(client, ["comment"], sleep=sleep)
    assert "t1_a" in items
    assert len(calls) == 2


class BrokenListing(FakeClient):
    def listing(self, kind, sort, time_filter=None):
        if sort == "hot":
            raise WiperError("HTTP 500")
        yield from super().listing(kind, sort, time_filter)


def test_collect_survives_a_broken_listing():
    client = BrokenListing()
    client.add(make_item("a"))
    assert "t1_a" in collect(client, ["comment"])


def test_hydrate_updates_and_marks_missing(fake_client):
    fake_client.add(make_item("a", score=42, subreddit="live"))
    items = {
        "t1_a": make_item("a", score=None, subreddit="", sources={"export"}),
        "t1_gone": make_item("gone", score=None, sources={"export"}),
    }
    found = hydrate(fake_client, items, ["t1_a", "t1_gone"])
    assert found == 1
    assert items["t1_a"].score == 42 and items["t1_a"].subreddit == "live"
    assert items["t1_gone"].gone


def test_client_can_choose_a_lighter_listing_plan(fake_client):
    fake_client.listing_plan = (("new", None), ("top", "all"))
    fake_client.add(make_item("a"))
    collect(fake_client, ["comment"], max_rounds=1)
    assert fake_client.listing_calls == 2


class AlwaysLimited(FakeClient):
    def listing(self, kind, sort, time_filter=None):
        self.listing_calls += 1
        raise RateLimitError("429")
        yield  # pragma: no cover


def test_collect_stops_when_rate_limit_does_not_clear(no_sleep):
    import pytest

    client = AlwaysLimited()
    with pytest.raises(RateLimitError, match="try again later"):
        collect(client, ["comment", "post"], sleep=no_sleep[0])
    assert client.listing_calls == 7  # one listing, 1 try + 6 retries, then stop


class LimitedAfterNew(FakeClient):
    """Serves the "new" listing, then Reddit starts saying 429 for good."""

    handles_rate_limits = True  # like WebClient: the client already retried each page

    def listing(self, kind, sort, time_filter=None):
        if sort != "new":
            self.listing_calls += 1
            raise RateLimitError("429")
        yield from super().listing(kind, sort, time_filter)  # counts the call itself


def test_collect_keeps_partial_results_when_asked(no_sleep):
    client = LimitedAfterNew()
    client.add(make_item("a"), make_item("b"))
    limited = []
    items = collect(client, ["comment", "post"], sleep=no_sleep[0], on_rate_limited=limited.append)
    assert set(items) == {"t1_a", "t1_b"}
    assert limited == ["comments/hot"]  # the next listing after "new"
    # No listing-level retries on top of the client's own page retries, and no sleeping here.
    assert client.listing_calls == 2 and no_sleep[1] == []
