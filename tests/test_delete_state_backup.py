import csv
import json

from conftest import make_item
from reddit_wiper.backup import write_backup
from reddit_wiper.delete import Wiper, random_filler
from reddit_wiper.errors import ItemUnavailableError, RateLimitError, WiperError
from reddit_wiper.retry import backoff_delay
from reddit_wiper.state import State


def _wiper(client, tmp_path, sleep, **kw):
    state = State.load(tmp_path / "state.json", "testuser")
    return Wiper(client, state, sleep=sleep, filler=lambda: "XXX", **kw), state


def test_overwrite_then_delete(fake_client, tmp_path, no_sleep):
    sleep, sleeps = no_sleep
    comment, text_post, link_post = (
        make_item("c"),
        make_item("t", "post", is_self=True),
        make_item("l", "post", is_self=False),
    )
    fake_client.add(comment, text_post, link_post)
    wiper, state = _wiper(fake_client, tmp_path, sleep, edit_delay=2.0, delay=1.0)
    stats = wiper.run([comment, text_post, link_post])
    assert fake_client.edits == [("t1_c", "XXX"), ("t3_t", "XXX")]  # link post not edited
    assert fake_client.deletes == ["t1_c", "t3_t", "t3_l"]
    assert stats.edited == 2 and stats.deleted == 3 and stats.failed == 0
    assert sleeps.count(2.0) == 2 and sleeps.count(1.0) == 2
    assert state.done == {"t1_c": "deleted", "t3_t": "deleted", "t3_l": "deleted"}


def test_no_overwrite(fake_client, tmp_path, no_sleep):
    item = make_item("c")
    fake_client.add(item)
    wiper, _ = _wiper(fake_client, tmp_path, no_sleep[0], overwrite=False)
    wiper.run([item])
    assert fake_client.edits == [] and fake_client.deletes == ["t1_c"]


def test_errors_do_not_stop_the_run(fake_client, tmp_path, no_sleep):
    items = [make_item(x) for x in "abcd"]
    fake_client.add(*items)
    fake_client.fail_edit.add("t1_a")  # archived: edit fails, delete still happens
    fake_client.fail_delete["t1_b"] = ItemUnavailableError("gone")
    fake_client.fail_delete["t1_c"] = WiperError("HTTP 500")
    wiper, state = _wiper(fake_client, tmp_path, no_sleep[0])
    stats = wiper.run(items)
    assert stats.edit_failed == 1 and stats.deleted == 2 and stats.already_gone == 1
    assert stats.failed == 1
    assert "t1_c" in state.failed and not state.is_done("t1_c")
    assert state.done["t1_b"] == "gone"


def test_unexpected_exception_is_logged_and_skipped(fake_client, tmp_path, no_sleep):
    items = [make_item("a"), make_item("b")]
    fake_client.add(*items)
    fake_client.fail_delete["t1_a"] = ValueError("weird")
    wiper, _ = _wiper(fake_client, tmp_path, no_sleep[0])
    stats = wiper.run(items)
    assert stats.failed == 1 and stats.deleted == 1


def test_rate_limit_backoff_then_success(fake_client, tmp_path, no_sleep):
    sleep, sleeps = no_sleep
    item = make_item("a")
    fake_client.add(item)
    attempts = {"n": 0}
    original = fake_client.delete

    def flaky(it):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimitError("429")
        original(it)

    fake_client.delete = flaky
    wiper, _ = _wiper(fake_client, tmp_path, sleep, overwrite=False, backoff_base=1.0)
    stats = wiper.run([item])
    assert stats.deleted == 1
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] >= 1.0  # exponential


def test_rate_limit_gives_up_after_max_retries(fake_client, tmp_path, no_sleep):
    item = make_item("a")
    fake_client.add(item)
    fake_client.fail_delete["t1_a"] = RateLimitError("429")
    wiper, state = _wiper(fake_client, tmp_path, no_sleep[0], overwrite=False, max_retries=2)
    assert wiper.run([item]).failed == 1
    assert "t1_a" in state.failed


def test_backoff_delay_uses_retry_after_and_caps():
    assert 30 <= backoff_delay(0, 5, retry_after=30) <= 31
    assert backoff_delay(20, 5) == 600


def test_resume_skips_done_and_edited(fake_client, tmp_path, no_sleep):
    items = [make_item("a"), make_item("b")]
    fake_client.add(*items)
    state = State.load(tmp_path / "state.json", "testuser")
    state.mark_done("t1_a")
    state.mark_edited("t1_b")  # crashed after edit, before delete
    reloaded = State.load(tmp_path / "state.json", "testuser")
    wiper = Wiper(fake_client, reloaded, sleep=no_sleep[0], filler=lambda: "X")
    stats = wiper.run(items)
    assert stats.skipped_done == 1
    assert fake_client.edits == []  # not edited twice
    assert fake_client.deletes == ["t1_b"]


def test_gone_items_are_not_touched(fake_client, tmp_path, no_sleep):
    item = make_item("a", gone=True)
    wiper, state = _wiper(fake_client, tmp_path, no_sleep[0])
    assert wiper.run([item]).already_gone == 1
    assert fake_client.deletes == [] and state.done["t1_a"] == "gone"


def test_corrupt_state_is_moved_aside(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    state = State.load(path, "u")
    assert state.done == {} and (tmp_path / "s.corrupt.json").exists()


def test_state_clear(tmp_path):
    state = State.load(tmp_path / "s.json", "u")
    state.mark_done("t1_a")
    assert state.path.exists()
    state.clear()
    assert not state.path.exists()


def test_random_filler_varies():
    texts = {random_filler() for _ in range(20)}
    assert len(texts) == 20 and all(t.endswith(".") for t in texts)


def test_backup_json_and_csv(tmp_path):
    items = [
        make_item("a", text='çok güzel, "quoted"\nline', permalink="/r/x/1/"),
        make_item("p", "post", title="T", url="https://example.com"),
    ]
    json_path, csv_path = write_backup(items, tmp_path / "b")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["count"] == 2 and data["items"][0]["text"] == 'çok güzel, "quoted"\nline'
    assert data["items"][0]["permalink"] == "https://www.reddit.com/r/x/1/"
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["fullname"] for r in rows] == ["t1_a", "t3_p"]
    assert rows[1]["title"] == "T"
    assert set(rows[0]) >= {"id", "kind", "subreddit", "created_iso", "score", "text", "permalink"}
