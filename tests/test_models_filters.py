from datetime import datetime, timezone

from conftest import make_item
from reddit_wiper.filters import Filters
from reddit_wiper.models import Item, item_from_json, split_fullname


def test_fullname_and_editable():
    assert make_item("a").fullname == "t1_a"
    assert make_item("b", "post").fullname == "t3_b"
    assert make_item("a").editable
    assert make_item("b", "post", is_self=True).editable
    assert not make_item("c", "post", is_self=False).editable  # link/image post
    assert not make_item("d", archived=True).editable
    assert not make_item("e", gone=True).editable


def test_split_fullname():
    assert split_fullname("t1_x") == ("comment", "x")
    assert split_fullname("t3_y") == ("post", "y")
    assert split_fullname("t5_z") is None


def test_item_from_json_comment_and_deleted():
    item = item_from_json(
        {
            "name": "t1_abc",
            "body": "hi",
            "author": "me",
            "score": 5,
            "subreddit": "python",
            "created_utc": 1.0,
        },
        "browser",
    )
    assert item.kind == "comment" and item.text == "hi" and item.score == 5 and not item.gone
    deleted = item_from_json({"name": "t1_x", "body": "[deleted]", "author": "[deleted]"}, "x")
    assert deleted.gone
    removed = item_from_json({"name": "t1_y", "body": "[removed]", "author": "me"}, "x")
    assert not removed.gone  # moderator-removed items are still yours to delete
    assert item_from_json({"name": "t5_sub"}, "x") is None


def test_merge_prefers_fresh_score():
    a = make_item("a", score=None, subreddit="", sources={"export"})
    a.merge(make_item("a", score=7, subreddit="python"))
    assert a.score == 7 and a.subreddit == "python" and a.sources == {"export", "api"}


def test_to_record_absolute_permalink():
    rec = make_item("a", permalink="/r/x/comments/1/t/a/").to_record()
    assert rec["permalink"] == "https://www.reddit.com/r/x/comments/1/t/a/"
    assert rec["fullname"] == "t1_a"


def _at(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc).timestamp()


def test_filters():
    items = [
        make_item("1", subreddit="Python", created_utc=_at(2020, 1, 1), score=10),
        make_item("2", subreddit="AskReddit", created_utc=_at(2022, 1, 1), score=-3),
        make_item("3", "post", subreddit="python", created_utc=_at(2023, 6, 1), score=500),
        make_item("4", subreddit="python", created_utc=None, score=None),
    ]

    def ids(f: Filters) -> list[str]:
        return [i.id for i in f.apply(items)]

    assert ids(Filters()) == ["1", "2", "3", "4"]
    assert ids(Filters(kinds=("post",))) == ["3"]
    assert ids(Filters(include_subreddits={"r/python"})) == ["1", "3", "4"]
    assert ids(Filters(exclude_subreddits={"PYTHON"})) == ["2"]
    assert ids(Filters(after=datetime(2021, 1, 1))) == ["2", "3"]  # unknown date is skipped
    assert ids(Filters(before=datetime(2022, 1, 1))) == ["1"]
    assert ids(Filters(max_score=10)) == ["1", "2"]
    assert ids(Filters(min_score=0)) == ["1", "3"]
    assert Filters(max_score=1).reason_to_skip(items[3]) == "unknown score"


def test_item_created():
    assert Item(id="a", kind="comment", created_utc=0).created.year == 1970
