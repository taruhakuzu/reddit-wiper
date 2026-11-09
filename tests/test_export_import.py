import zipfile

import pytest

from reddit_wiper.errors import ConfigError
from reddit_wiper.export_import import parse_date, read_export

COMMENTS = (
    "id,permalink,date,ip,subreddit,gildings,link,parent,body,media\n"
    "c1,https://www.reddit.com/r/python/comments/x/t/c1/,2019-05-03 14:22:11 UTC,1.2.3.4,"
    'python,0,https://www.reddit.com/r/python/comments/x/t/,t3_x,"multi\nline, body",\n'
    "t1_c2,,2018-01-01 00:00:00 UTC,,AskReddit,0,,,second,\n"
)
POSTS = (
    "id,permalink,date,ip,subreddit,gildings,title,url,body\n"
    "p1,https://www.reddit.com/r/pics/comments/p1/cat/,2017-02-02 10:00:00 UTC,,pics,0,"
    "A cat,https://i.redd.it/cat.jpg,\n"
    "p2,https://www.reddit.com/r/self/comments/p2/hi/,2017-03-03 10:00:00 UTC,,self,0,"
    "Hi,https://www.reddit.com/r/self/comments/p2/hi/,some text\n"
)


def _write_folder(folder):
    nested = folder / "export_user_20260101"
    nested.mkdir(parents=True)
    (nested / "comments.csv").write_text(COMMENTS, encoding="utf-8", newline="")
    (nested / "posts.csv").write_text(POSTS, encoding="utf-8", newline="")
    (nested / "messages.csv").write_text("id\n1\n", encoding="utf-8")
    return nested


def _check(items):
    by_id = {i.fullname: i for i in items}
    assert set(by_id) == {"t1_c1", "t1_c2", "t3_p1", "t3_p2"}
    c1 = by_id["t1_c1"]
    assert c1.subreddit == "python" and c1.text == "multi\nline, body"
    assert c1.permalink == "/r/python/comments/x/t/c1/"
    assert c1.created.year == 2019 and c1.sources == {"export"}
    assert not by_id["t3_p1"].is_self  # image post: delete only
    assert by_id["t3_p2"].is_self
    assert "ip" not in c1.to_record()


def test_read_export_folder(tmp_path):
    _write_folder(tmp_path / "export")
    _check(read_export(tmp_path / "export"))


def test_read_export_zip(tmp_path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("export/comments.csv", "﻿" + COMMENTS)
        zf.writestr("export/posts.csv", POSTS)
    _check(read_export(zip_path))


def test_read_export_errors(tmp_path):
    with pytest.raises(ConfigError):
        read_export(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ConfigError):
        read_export(tmp_path / "empty")
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(ConfigError):
        read_export(tmp_path / "file.txt")


def test_parse_date():
    assert parse_date("2020-01-01 00:00:00 UTC") == 1577836800.0
    assert parse_date("") is None
    assert parse_date("garbage") is None
