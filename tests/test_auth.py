from types import SimpleNamespace
from unittest import mock

import praw
import prawcore
import pytest
from praw.exceptions import RedditAPIException

from reddit_wiper import auth
from reddit_wiper.config import Credentials
from reddit_wiper.errors import (
    AuthError,
    ConfigError,
    ItemUnavailableError,
    RateLimitError,
    WiperError,
)
from reddit_wiper.models import Item


@pytest.fixture
def reddit():
    # Constructing praw.Reddit makes no network calls.
    return praw.Reddit(
        client_id="id",
        client_secret="secret",
        user_agent="test-agent",
        check_for_updates=False,
        check_for_async=False,
    )


def _response(status, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, text="", json=lambda: {})


def test_build_password_2fa():
    assert auth.build_password("pw", "123456") == "pw:123456"
    assert auth.build_password("pw", None) == "pw"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            prawcore.exceptions.TooManyRequests(_response(429, {"retry-after": "12"})),
            RateLimitError,
        ),
        (prawcore.exceptions.NotFound(_response(404)), ItemUnavailableError),
        (prawcore.exceptions.Forbidden(_response(403)), ItemUnavailableError),
        (prawcore.exceptions.ServerError(_response(500)), WiperError),
        (prawcore.exceptions.InvalidToken(_response(401)), AuthError),
        (RedditAPIException([["RATELIMIT", "you are doing that too much", None]]), RateLimitError),
        (RedditAPIException([["TOO_OLD", "archived", None]]), ItemUnavailableError),
        (RedditAPIException([["SOMETHING", "else", None]]), WiperError),
    ],
)
def test_translate(exc, expected):
    def boom():
        raise exc

    with pytest.raises(expected):
        auth.translate(boom)


def test_translate_retry_after():
    exc = prawcore.exceptions.TooManyRequests(_response(429, {"retry-after": "12"}))
    with pytest.raises(RateLimitError) as info:
        auth.translate(lambda: (_ for _ in ()).throw(exc))
    assert info.value.retry_after == 12.0


def test_praw_to_item(reddit):
    comment = praw.models.Comment(
        reddit,
        _data={
            "id": "c1",
            "body": "hello",
            "subreddit": "python",
            "created_utc": 5.0,
            "score": 3,
            "permalink": "/r/python/comments/x/t/c1/",
            "author": "me",
            "archived": True,
        },
    )
    item = auth.praw_to_item(comment)
    assert item.fullname == "t1_c1" and item.text == "hello" and item.archived and not item.gone

    post = praw.models.Submission(
        reddit,
        _data={
            "id": "p1",
            "title": "T",
            "selftext": "",
            "url": "https://i.redd.it/x.jpg",
            "is_self": False,
            "subreddit": "pics",
            "author": "[deleted]",
            "permalink": "/r/pics/p1",
        },
    )
    item = auth.praw_to_item(post)
    assert item.kind == "post" and not item.is_self and item.gone
    assert auth.praw_to_item(object()) is None


def test_prawclient_listing_info_edit_delete(reddit):
    client = auth.PrawClient(reddit, "me")
    comment = praw.models.Comment(reddit, _data={"id": "c1", "body": "x", "author": "me"})
    with mock.patch.object(
        praw.models.reddit.redditor.Redditor, "comments", new_callable=mock.PropertyMock
    ) as comments:
        comments.return_value.top.return_value = iter([comment])
        items = list(client.listing("comment", "top", "all"))
        comments.return_value.top.assert_called_once_with(limit=None, time_filter="all")
    assert [i.fullname for i in items] == ["t1_c1"]

    with mock.patch.object(reddit, "info", side_effect=lambda fullnames: iter([comment])) as info:
        names = [f"t1_{n}" for n in range(150)]
        assert len(list(client.info(names))) == 2  # two batches of <=100
        assert len(info.call_args_list[0].kwargs["fullnames"]) == 100

    with (
        mock.patch.object(praw.models.Comment, "edit") as edit,
        mock.patch.object(praw.models.Comment, "delete") as delete,
    ):
        target = Item(id="c1", kind="comment")
        client.edit(target, "new text")
        client.delete(target)
        edit.assert_called_once_with("new text")
        delete.assert_called_once()

    with mock.patch.object(praw.models.Submission, "delete") as delete:
        client.delete(Item(id="p1", kind="post"))
        delete.assert_called_once()


def test_login_requires_config():
    with pytest.raises(ConfigError):
        auth.login(Credentials(), "pw")


def test_login_failure_is_auth_error():
    creds = Credentials(client_id="id", client_secret="sec", username="u", user_agent="ua")
    with mock.patch("praw.Reddit") as reddit_cls:
        reddit_cls.return_value.user.me.side_effect = prawcore.exceptions.OAuthException(
            _response(400), "invalid_grant", None
        )
        with pytest.raises(AuthError):
            auth.login(creds, "pw", "123456")
        assert reddit_cls.call_args.kwargs["password"] == "pw:123456"


def test_login_success():
    creds = Credentials(client_id="id", client_secret="sec", username="u", user_agent="ua")
    with mock.patch("praw.Reddit") as reddit_cls:
        reddit_cls.return_value.user.me.return_value = SimpleNamespace(name="u")
        client = auth.login(creds, "pw")
    assert client.username == "u"
