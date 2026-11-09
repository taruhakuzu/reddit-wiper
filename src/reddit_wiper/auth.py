"""Authentication with a personal "script" Reddit app via PRAW, and the PRAW-backed client."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

import praw
import prawcore
from praw.exceptions import RedditAPIException

from reddit_wiper.config import Credentials
from reddit_wiper.errors import AuthError, ItemUnavailableError, RateLimitError, WiperError
from reddit_wiper.logging_utils import register_secret
from reddit_wiper.models import Item, Kind

log = logging.getLogger(__name__)
T = TypeVar("T")

# Reddit API error types that mean "this item can't be changed any more" rather than a bug.
UNAVAILABLE_ERRORS = {
    "DELETED_COMMENT",
    "DELETED_LINK",
    "NOT_AUTHOR",
    "THREAD_LOCKED",
    "TOO_OLD",
    "NO_THING_ID",
    "INVALID_THING_ID",
    "NOT_FOUND",
    "SUBREDDIT_NOEXIST",
}


def build_password(password: str, otp: str | None) -> str:
    """PRAW's 2FA convention: ``password:123456``."""
    return f"{password}:{otp}" if otp else password


def login(creds: Credentials, password: str, otp: str | None = None) -> PrawClient:
    creds.require_api()
    if not password:
        raise AuthError("A password is required for a script app.")
    for secret in (creds.client_secret, password, otp, build_password(password, otp)):
        register_secret(secret)
    reddit = praw.Reddit(
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        username=creds.username,
        password=build_password(password, otp),
        user_agent=creds.user_agent,
        ratelimit_seconds=600,  # let PRAW wait out short RATELIMIT responses itself
        check_for_updates=False,
        check_for_async=False,
    )
    try:
        me = reddit.user.me()
    except prawcore.exceptions.OAuthException as exc:
        raise AuthError(
            "Login failed. Check client id/secret, username and password (and the 2FA code, "
            "if you use two-factor authentication). Note that new Reddit API apps need to be "
            "approved by Reddit before they work."
        ) from exc
    except prawcore.exceptions.ResponseException as exc:
        raise AuthError(f"Login failed: HTTP {exc.response.status_code}") from exc
    if me is None:
        raise AuthError("Login failed: Reddit returned no user.")
    log.info("Logged in as u/%s", me.name)
    return PrawClient(reddit, me.name)


def translate(fn: Callable[[], T]) -> T:
    """Run ``fn`` and map PRAW/prawcore exceptions to our own types."""
    try:
        return fn()
    except prawcore.exceptions.TooManyRequests as exc:
        retry = exc.response.headers.get("retry-after") if exc.response is not None else None
        raise RateLimitError("HTTP 429 Too Many Requests", _to_float(retry)) from exc
    except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden) as exc:
        raise ItemUnavailableError(type(exc).__name__) from exc
    except (prawcore.exceptions.OAuthException, prawcore.exceptions.InvalidToken) as exc:
        raise AuthError(
            "Reddit rejected the login (token expired?). Run the command again to resume."
        ) from exc
    except RedditAPIException as exc:
        types = {item.error_type for item in exc.items}
        message = "; ".join(f"{i.error_type}: {i.message}" for i in exc.items)
        if "RATELIMIT" in types:
            raise RateLimitError(message) from exc
        if types & UNAVAILABLE_ERRORS:
            raise ItemUnavailableError(message) from exc
        raise WiperError(message) from exc
    except prawcore.exceptions.ResponseException as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 429:
            raise RateLimitError("HTTP 429") from exc
        raise WiperError(f"HTTP {status}") from exc
    except prawcore.exceptions.RequestException as exc:
        raise WiperError(f"Network error: {exc.original_exception!r}") from exc


def praw_to_item(obj: Any, source: str = "api") -> Item | None:
    """Convert a PRAW Comment/Submission. Reads only already-loaded attributes (``vars``), so it
    never triggers an extra network request for a lazily-loaded object."""
    if isinstance(obj, praw.models.Comment):
        kind: Kind = "comment"
    elif isinstance(obj, praw.models.Submission):
        kind = "post"
    else:
        return None
    data = vars(obj)
    text = data.get("body" if kind == "comment" else "selftext") or ""
    return Item(
        id=obj.id,
        kind=kind,
        subreddit=str(data.get("subreddit") or ""),
        created_utc=data.get("created_utc"),
        score=data.get("score"),
        title=(data.get("title") or "") if kind == "post" else "",
        text=text,
        permalink=data.get("permalink") or "",
        url=(data.get("url") or "") if kind == "post" else "",
        is_self=bool(data.get("is_self")) if kind == "post" else False,
        archived=bool(data.get("archived")),
        locked=bool(data.get("locked")),
        # PRAW turns a "[deleted]" author into None.
        gone="author" in data and data["author"] is None,
        sources={source},
    )


class PrawClient:
    """Client implementation on top of an authenticated ``praw.Reddit``."""

    def __init__(self, reddit: praw.Reddit, username: str) -> None:
        self.reddit = reddit
        self.username = username

    def listing(self, kind: Kind, sort: str, time_filter: str | None = None) -> Iterator[Item]:
        redditor = self.reddit.redditor(self.username)
        sub = redditor.comments if kind == "comment" else redditor.submissions
        method = getattr(sub, sort)
        kwargs: dict[str, Any] = {"limit": None}
        if time_filter:
            kwargs["time_filter"] = time_filter
        generator = iter(method(**kwargs))
        while True:
            try:
                obj = translate(lambda: next(generator))
            except StopIteration:
                return
            item = praw_to_item(obj)
            if item is not None:
                yield item

    def info(self, fullnames: Iterable[str]) -> Iterator[Item]:
        names = list(fullnames)
        for start in range(0, len(names), 100):
            batch = names[start : start + 100]
            results = translate(lambda batch=batch: list(self.reddit.info(fullnames=batch)))
            for obj in results:
                item = praw_to_item(obj)
                if item is not None:
                    yield item

    def _thing(self, item: Item) -> Any:
        if item.kind == "comment":
            return self.reddit.comment(id=item.id)
        return self.reddit.submission(id=item.id)

    def edit(self, item: Item, text: str) -> None:
        thing = self._thing(item)
        translate(lambda: thing.edit(text))

    def delete(self, item: Item) -> None:
        thing = self._thing(item)
        translate(thing.delete)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
