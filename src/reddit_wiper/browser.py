"""Browser mode: works through your own logged-in old.reddit.com session, no API app needed.

A real browser window opens (via Playwright). You log in to Reddit yourself in that window;
reddit-wiper never sees your password. It then reads your normal old.reddit.com pages (your
profile's comments/submitted pages) and deletes with the same requests old.reddit.com's own
"edit" and "delete" buttons send (``/api/editusertext``, ``/api/del``), from a tab of that
browser, authenticated by your session, pausing between actions.

Reddit redirects its ``.json`` endpoints to the login page for browser sessions, so pages are
read as HTML and parsed inside the browser.

Playwright is an optional dependency: ``pipx install "reddit-wiper[browser]"``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from reddit_wiper.errors import AuthError, ItemUnavailableError, RateLimitError, WiperError
from reddit_wiper.logging_utils import register_secret
from reddit_wiper.models import Item, Kind
from reddit_wiper.retry import with_backoff

log = logging.getLogger(__name__)

BASE_URL = "https://old.reddit.com"
LOGIN_URL = "https://old.reddit.com/login"
LOGIN_ATTEMPTS = 5
MAX_PAGES = 12  # 12 x 100 items covers Reddit's ~1000-item listing cap
PAGE_DELAY = 2.0  # seconds between page loads, like a person clicking "next"
PAGE_RETRIES = 4  # ~10+20+40+80 s of backoff per page before giving up on the scan

# Runs inside the browser tab. With ``parse`` set, the HTML response is turned into JSON:
# who is logged in, the modhash, every ".thing" (post/comment) on the page and the next-page id.
_FETCH_JS = r"""
async ({url, method, form, modhash, parse}) => {
  const opts = {method, credentials: 'include', headers: {'X-Requested-With': 'XMLHttpRequest'}};
  if (modhash) { opts.headers['X-Modhash'] = modhash; }
  if (form) { opts.body = new URLSearchParams(form); }
  const r = await fetch(url, opts);
  let text = await r.text();
  const out = {status: r.status, retryAfter: r.headers.get('retry-after'), finalUrl: r.url};
  if (parse) {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    const logged = text.match(/"logged":\s*"([^"]+)"/);
    const modhashMatch = text.match(/"modhash":\s*"([^"]*)"/);
    const things = [...doc.querySelectorAll('.thing[data-fullname]')].map(t => {
      const entry = t.querySelector(':scope > .entry');
      const md = entry ? entry.querySelector('.usertext-body .md') : null;
      const title = entry ? entry.querySelector('a.title') : null;
      const scoreSpan = entry ? entry.querySelector('.score.unvoted') : null;
      // Comments have no data-timestamp; their date lives in the tagline's <time datetime>.
      const timeEl = entry ? entry.querySelector('.tagline time[datetime]') : null;
      return {
        fullname: t.dataset.fullname,
        author: t.dataset.author || null,
        subreddit: t.dataset.subreddit || '',
        permalink: t.dataset.permalink || '',
        timestamp: t.dataset.timestamp || (timeEl ? timeEl.getAttribute('datetime') : null),
        score: t.dataset.score || (scoreSpan ? scoreSpan.getAttribute('title') : null),
        url: t.dataset.url || '',
        title: title ? title.textContent : '',
        text: md ? md.textContent.trim() : '',
        is_self: t.classList.contains('self'),
        archived: t.classList.contains('archived'),
        locked: t.classList.contains('locked'),
      };
    });
    const next = doc.querySelector('.next-button a');
    let after = null;
    if (next) {
      try { after = new URL(next.getAttribute('href'), url).searchParams.get('after'); }
      catch (e) {}
    }
    text = JSON.stringify({
      logged: logged ? logged[1] : null,
      modhash: modhashMatch ? modhashMatch[1] : '',
      things, after,
    });
  }
  out.text = text;
  return out;
}
"""

UNAVAILABLE_ERRORS = {
    "DELETED_COMMENT",
    "DELETED_LINK",
    "NOT_AUTHOR",
    "THREAD_LOCKED",
    "TOO_OLD",
    "NO_THING_ID",
    "INVALID_THING_ID",
    "NOT_FOUND",
}


class Transport(Protocol):
    """Sends a same-origin request from inside the logged-in browser.

    Returns ``(status, text, retry_after, final_url)``. With ``parse=True`` the text is the JSON
    summary of an HTML page produced by ``_FETCH_JS``.
    """

    def request(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        modhash: str | None = None,
        parse: bool = False,
    ) -> tuple[int, str, str | None, str]: ...


def item_from_scrape(data: dict[str, Any]) -> Item | None:
    """Build an Item from one ``.thing`` scraped off an old.reddit.com page."""
    name = str(data.get("fullname") or "")
    if name.startswith("t1_"):
        kind: Kind = "comment"
    elif name.startswith("t3_"):
        kind = "post"
    else:
        return None
    return Item(
        id=name[3:],
        kind=kind,
        subreddit=data.get("subreddit") or "",
        created_utc=_created_utc(data.get("timestamp")),
        score=_int(data.get("score")),
        title=(data.get("title") or "").strip() if kind == "post" else "",
        text=data.get("text") or "",
        permalink=data.get("permalink") or "",
        url=(data.get("url") or "") if kind == "post" else "",
        is_self=bool(data.get("is_self")) if kind == "post" else False,
        archived=bool(data.get("archived")),
        locked=bool(data.get("locked")),
        gone=data.get("author") in (None, "", "[deleted]"),
        sources={"browser"},
    )


class WebClient:
    """Client implementation that talks to old.reddit.com through the user's browser session."""

    # Looking items up by ID only works for posts (/by_id/); comments from a data export are
    # attempted directly instead of being treated as "gone".
    info_is_complete = False

    # Web pages are rate limited much more tightly than the API, so browser mode reads the four
    # listings that surface the most distinct items. Items hidden behind the ~1000 cap appear in
    # the re-scans after each delete pass, or come from the data export (--export).
    listing_plan = (("new", None), ("top", "all"), ("controversial", "all"), ("hot", None))

    # Each page already backs off and retries on its own, so callers must not retry whole
    # listings on top of that (nested retries turned a rate limit into a 15+ minute wait).
    handles_rate_limits = True

    def __init__(
        self,
        transport: Transport,
        page_delay: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.page_delay = PAGE_DELAY if page_delay is None else page_delay
        self.sleep = sleep
        self._last_page = 0.0
        self.modhash = ""
        self._self_text: dict[str, str] = {}
        page = self._get_page("/")
        if not page.get("logged"):
            raise AuthError("You don't seem to be logged in to Reddit in the browser window.")
        self.username: str = page["logged"]
        self.modhash = page.get("modhash") or ""
        if not self.modhash:
            raise AuthError("Logged in, but Reddit didn't provide a session key (modhash).")
        register_secret(self.modhash)

    # -- low level ---------------------------------------------------------------------------
    def _send(
        self, method: str, path: str, form: dict[str, str] | None = None, parse: bool = False
    ) -> Any:
        status, text, retry_after, final_url = self.transport.request(
            method, path, form, self.modhash if method == "POST" else None, parse
        )
        if status == 429:
            raise RateLimitError("HTTP 429 Too Many Requests", _float(retry_after))
        if status == 401 or "/login" in urlsplit(final_url or "").path:
            raise AuthError("The browser session is not logged in to Reddit.")
        if status in (403, 404):
            raise ItemUnavailableError(f"HTTP {status}")
        if status >= 400:
            raise WiperError(f"HTTP {status}")
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise WiperError("Unexpected response from Reddit (not JSON)") from exc

    def _get_page(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Read one page, politely: a pause between pages, and if Reddit still says "slow down",
        back off and retry *this page* (so a long listing doesn't start over)."""
        if params:
            path = f"{path}?{urlencode(params)}"
        wait = self.page_delay - (time.monotonic() - self._last_page)
        if self._last_page and wait > 0:
            self.sleep(wait)
        try:
            result = with_backoff(
                lambda: self._send("GET", path, parse=True),
                max_retries=PAGE_RETRIES,
                base=10.0,
                sleep=self.sleep,
                what=f"page {urlsplit(path).path}",
            )
        finally:
            self._last_page = time.monotonic()
        return result if isinstance(result, dict) else {}

    def _post(self, path: str, form: dict[str, str]) -> Any:
        form = {**form, "uh": self.modhash, "api_type": "json"}
        result = self._send("POST", path, form)
        errors = (result.get("json") or {}).get("errors") if isinstance(result, dict) else None
        if errors:
            types = {str(e[0]) for e in errors if e}
            message = "; ".join(": ".join(str(x) for x in e[:2]) for e in errors)
            if "RATELIMIT" in types:
                raise RateLimitError(message)
            if types & UNAVAILABLE_ERRORS:
                raise ItemUnavailableError(message)
            raise WiperError(message)
        return result

    def _own_items(self, page: dict[str, Any]) -> Iterator[Item]:
        for data in page.get("things") or []:
            if (data.get("author") or "").lower() != self.username.lower():
                continue  # e.g. other people's comments on a post page
            item = item_from_scrape(data)
            if item is not None:
                yield item

    def _fill_self_text(self, item: Item) -> None:
        """Listings don't include a text post's body; read it from the post page for the backup."""
        if item.kind != "post" or not item.is_self or item.text or not item.permalink:
            return
        if item.fullname not in self._self_text:
            text = ""
            try:
                page = self._get_page(urlsplit(item.permalink).path)
                for found in self._own_items(page):
                    if found.fullname == item.fullname:
                        text = found.text
            except AuthError:
                raise
            except WiperError as exc:
                log.debug("Could not read the text of %s: %s", item.fullname, exc)
            self._self_text[item.fullname] = text
        item.text = self._self_text[item.fullname]

    # -- Client interface --------------------------------------------------------------------
    def listing(self, kind: Kind, sort: str, time_filter: str | None = None) -> Iterator[Item]:
        section = "comments" if kind == "comment" else "submitted"
        after = ""
        for _ in range(MAX_PAGES):
            params = {"sort": sort, "limit": "100"}
            if time_filter:
                params["t"] = time_filter
            if after:
                params["after"] = after
            page = self._get_page(f"/user/{self.username}/{section}/", params)
            for item in self._own_items(page):
                if item.kind == kind:
                    self._fill_self_text(item)
                    yield item
            after = page.get("after") or ""
            if not after:
                return

    def info(self, fullnames: Iterable[str]) -> Iterator[Item]:
        posts = [n for n in fullnames if n.startswith("t3_")]
        for start in range(0, len(posts), 100):
            batch = posts[start : start + 100]
            page = self._get_page("/by_id/" + ",".join(batch), {"limit": "100"})
            for item in self._own_items(page):
                self._fill_self_text(item)
                yield item

    def edit(self, item: Item, text: str) -> None:
        self._post("/api/editusertext", {"thing_id": item.fullname, "text": text})

    def delete(self, item: Item) -> None:
        self._post("/api/del", {"id": item.fullname})


class PlaywrightTransport:
    """Runs fetch() inside a dedicated old.reddit.com tab that only this tool uses."""

    def __init__(self, page: Any, playwright_error: type[Exception] = Exception) -> None:
        self.page = page
        self.playwright_error = playwright_error

    def request(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        modhash: str | None = None,
        parse: bool = False,
    ) -> tuple[int, str, str | None, str]:
        args = {
            "url": BASE_URL + path,
            "method": method,
            "form": form,
            "modhash": modhash,
            "parse": parse,
        }
        for attempt in range(3):
            try:
                result = self.page.evaluate(_FETCH_JS, args)
                break
            except self.playwright_error as exc:
                # The tab navigated underneath us (e.g. a late redirect). Get back and retry.
                if attempt == 2 or "navigation" not in str(exc).lower():
                    raise WiperError(f"Browser request failed: {exc}") from exc
                log.debug("Tab navigated during a request; retrying")
                _goto(self.page, BASE_URL + "/", self.playwright_error)
        return (
            int(result["status"]),
            result.get("text") or "",
            result.get("retryAfter"),
            result.get("finalUrl") or "",
        )


class BrowserSession:
    """Context manager that opens a visible browser and makes sure the user is logged in.

    With ``profile_dir`` the browser profile (and so the Reddit login) is kept between runs, so
    you log in once instead of every run. Repeated logins from a brand-new browser look like a
    new device each time, and Reddit may lock the account and force a password reset.
    """

    def __init__(
        self,
        channel: str | None = None,
        wait_for_login: Callable[[], None] | None = None,
        on_not_logged_in: Callable[[str], None] | None = None,
        profile_dir: Path | None = None,
        on_already_logged_in: Callable[[], None] | None = None,
    ):
        self.channel = channel
        self.wait_for_login = wait_for_login or (lambda: input())
        self.on_not_logged_in = on_not_logged_in or (lambda message: log.warning("%s", message))
        self.on_already_logged_in = on_already_logged_in or (lambda: None)
        self.profile_dir = profile_dir
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None

    def _launch(self, playwright_error: type[Exception]) -> None:
        channels = [self.channel] if self.channel else [None, "chrome", "msedge"]
        last_error: Exception | None = None
        for channel in channels:
            kwargs: dict[str, Any] = {"headless": False}
            if channel:
                kwargs["channel"] = channel
            try:
                if self.profile_dir is not None:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    self._context = self._pw.chromium.launch_persistent_context(
                        str(self.profile_dir), **kwargs
                    )
                else:
                    # A fresh, temporary profile: cookies are thrown away when it closes.
                    self._browser = self._pw.chromium.launch(**kwargs)
                    self._context = self._browser.new_context()
                return
            except playwright_error as exc:
                last_error = exc
                log.debug("Could not launch browser channel %s: %s", channel or "chromium", exc)
        raise WiperError(
            "Could not start a browser. Run `reddit-wiper install-browser` to download one, "
            f"or pass --channel chrome / --channel msedge. ({last_error})"
        )

    def __enter__(self) -> WebClient:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise WiperError(
                "Browser mode needs Playwright. Install it with: "
                'pipx install "reddit-wiper[browser]" (or pip install "reddit-wiper[browser]"), '
                "then run: reddit-wiper install-browser"
            ) from exc

        self._pw = sync_playwright().start()
        try:
            self._launch(PlaywrightError)
            context = self._context
            pages = list(context.pages)
            first_page = pages[0] if pages else context.new_page()

            # Saved session from an earlier run? Then there's nothing to log in to.
            try:
                client = self._connect(first_page, PlaywrightError)
            except WiperError:
                client = None
            if client is not None:
                self.on_already_logged_in()
                return client

            # The user logs in in the first tab; our requests go through a separate tab so
            # redirects in the login tab can't interrupt them.
            _goto(first_page, LOGIN_URL, PlaywrightError)
            work_page = None
            for attempt in range(1, LOGIN_ATTEMPTS + 1):
                self.wait_for_login()
                if work_page is None:
                    work_page = context.new_page()
                try:
                    return self._connect(work_page, PlaywrightError)
                except AuthError as exc:
                    if attempt == LOGIN_ATTEMPTS:
                        raise
                    self.on_not_logged_in(str(exc))
            raise AuthError("Not logged in.")  # pragma: no cover - loop always returns/raises
        except BaseException:
            self.__exit__(None, None, None)
            raise

    @staticmethod
    def _connect(page: Any, playwright_error: type[Exception]) -> WebClient:
        """Put the tab on old.reddit.com (same origin for our requests), then check that the
        session is logged in."""
        _goto(page, BASE_URL + "/", playwright_error)
        if "/login" in urlsplit(page.url).path:
            raise AuthError(
                "Reddit still shows the login page, so the browser isn't logged in yet "
                f"(current page: {_short_url(page.url)})."
            )
        try:
            return WebClient(PlaywrightTransport(page, playwright_error))
        except AuthError:
            raise
        except WiperError as exc:
            raise AuthError(f"Could not confirm the login: {exc}") from exc

    def __exit__(self, *exc: object) -> None:
        try:
            if self._context is not None:
                with contextlib.suppress(Exception):
                    self._context.close()
            if self._browser is not None:
                with contextlib.suppress(Exception):
                    self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._context = None
            self._browser = None
            self._pw = None


def _goto(page: Any, url: str, playwright_error: type[Exception]) -> None:
    """Navigate, tolerating redirects/other navigations that interrupt ours."""
    try:
        page.goto(url, wait_until="domcontentloaded")
    except playwright_error as exc:
        log.debug("Navigation to %s was interrupted: %s", _short_url(url), exc)
    with contextlib.suppress(playwright_error):
        page.wait_for_load_state("domcontentloaded")


def _short_url(url: str) -> str:
    """URL without query string (login redirects carry tokens/destinations we don't want logged)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _created_utc(value: Any) -> float | None:
    """old.reddit gives posts a millisecond ``data-timestamp`` and comments an ISO ``datetime``."""
    if not value:
        return None
    millis = _float(value)
    if millis is not None:
        return millis / 1000
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
