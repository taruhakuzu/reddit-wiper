import json
from urllib.parse import parse_qs, urlsplit

import pytest

from reddit_wiper import browser
from reddit_wiper.browser import PlaywrightTransport, WebClient, item_from_scrape
from reddit_wiper.errors import AuthError, ItemUnavailableError, RateLimitError, WiperError
from reddit_wiper.fetch import hydrate
from reddit_wiper.logging_utils import redact
from reddit_wiper.models import Item

ME = "testuser"


def thing(fullname, author=ME, **kw):
    data = {
        "fullname": fullname,
        "author": author,
        "subreddit": "test",
        "permalink": f"/r/test/comments/x/t/{fullname[3:]}/",
        "timestamp": "1600000000000",
        "score": "3",
        "text": "hello",
        "is_self": False,
    }
    data.update(kw)
    return data


def page_json(things=(), after=None, logged=ME, modhash="mh12345"):
    return json.dumps(
        {"logged": logged, "modhash": modhash, "things": list(things), "after": after}
    )


class FakeTransport:
    """Pretends to be old.reddit.com inside a logged-in browser tab."""

    def __init__(self, logged_in=True, pages=None):
        self.logged_in = logged_in
        self.pages = pages or {}
        self.requests = []
        self.post_response = (200, "{}", None, "https://old.reddit.com/api/del")

    def request(self, method, path, form=None, modhash=None, parse=False):
        self.requests.append((method, path, form, modhash, parse))
        url = urlsplit(path)
        if method == "POST":
            return self.post_response
        if url.path == "/":
            if not self.logged_in:
                return 200, page_json(logged=None, modhash=""), None, "https://old.reddit.com/"
            return 200, page_json(), None, "https://old.reddit.com/"
        after = parse_qs(url.query).get("after", [""])[0]
        key = (url.path, after)
        if key not in self.pages:
            return 404, "", None, "https://old.reddit.com" + url.path
        return 200, self.pages[key], None, "https://old.reddit.com" + url.path


def test_item_from_scrape():
    item = item_from_scrape(thing("t1_abc", score="1,234"))
    assert item.kind == "comment" and item.score == 1234 and item.created.year == 2020
    post = item_from_scrape(thing("t3_p", is_self=True, title=" T ", url="https://x"))
    assert post.kind == "post" and post.is_self and post.title == "T"
    assert item_from_scrape(thing("t1_d", author="[deleted]")).gone
    assert item_from_scrape(thing("t5_sub")) is None


def test_not_logged_in():
    with pytest.raises(AuthError):
        WebClient(FakeTransport(logged_in=False))


def test_login_redirect_is_auth_error():
    class Redirecting(FakeTransport):
        def request(self, method, path, form=None, modhash=None, parse=False):
            return 200, page_json(), None, "https://old.reddit.com/login/?reason=lor2"

    with pytest.raises(AuthError):
        WebClient(Redirecting())


def test_listing_paginates_and_skips_other_authors():
    t = FakeTransport(
        pages={
            ("/user/testuser/comments/", ""): page_json(
                [thing("t1_a"), thing("t1_x", author="someoneelse"), thing("t1_b")], after="t1_b"
            ),
            ("/user/testuser/comments/", "t1_b"): page_json([thing("t1_c")]),
        }
    )
    client = WebClient(t)
    assert client.username == ME
    names = [i.fullname for i in client.listing("comment", "top", "year")]
    assert names == ["t1_a", "t1_b", "t1_c"]
    query = parse_qs(urlsplit(t.requests[1][1]).query)
    assert query["sort"] == ["top"] and query["t"] == ["year"] and query["limit"] == ["100"]
    assert t.requests[1][4] is True  # parsed as HTML


def test_self_post_text_is_read_from_post_page():
    post = thing("t3_p", is_self=True, text="", permalink="/r/test/comments/p/title/")
    t = FakeTransport(
        pages={
            ("/user/testuser/submitted/", ""): page_json([post]),
            ("/r/test/comments/p/title/", ""): page_json(
                [{**post, "text": "full body"}, thing("t1_reply", author="other")]
            ),
        }
    )
    client = WebClient(t)
    items = list(client.listing("post", "new"))
    assert items[0].text == "full body"
    list(client.listing("post", "new"))  # second listing uses the cache
    assert sum(1 for r in t.requests if r[1].startswith("/r/test/comments/p/")) == 1


def test_info_only_looks_up_posts_and_hydrate_keeps_comments():
    t = FakeTransport(pages={("/by_id/t3_p,t3_gone", ""): page_json([thing("t3_p")])})
    client = WebClient(t)
    items = {
        "t3_p": Item(id="p", kind="post", sources={"export"}),
        "t3_gone": Item(id="gone", kind="post", sources={"export"}),
        "t1_c": Item(id="c", kind="comment", sources={"export"}),
    }
    assert hydrate(client, items, list(items)) == 1
    assert items["t3_gone"].gone  # post lookup is reliable
    assert not items["t1_c"].gone  # comment couldn't be looked up: still attempted


def test_edit_and_delete_send_modhash():
    t = FakeTransport()
    client = WebClient(t)
    client.edit(Item(id="a", kind="comment"), "new")
    client.delete(Item(id="p", kind="post"))
    (m1, p1, f1, _h1, _), (m2, p2, f2, h2, _) = t.requests[-2:]
    assert (m1, p1) == ("POST", "/api/editusertext")
    assert f1["thing_id"] == "t1_a" and f1["text"] == "new" and f1["uh"] == "mh12345"
    assert (m2, p2) == ("POST", "/api/del") and f2["id"] == "t3_p" and h2 == "mh12345"
    assert redact("uh=mh12345") == "uh=***"  # modhash never reaches the logs


def _errors(*errs):
    return json.dumps({"json": {"errors": [list(e) for e in errs]}})


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ((429, "", "7"), RateLimitError),
        ((404, "", None), ItemUnavailableError),
        ((500, "", None), WiperError),
        ((401, "", None), AuthError),
        ((200, "<html>login</html>", None), WiperError),
        ((200, _errors(("RATELIMIT", "slow", "x")), None), RateLimitError),
        ((200, _errors(("TOO_OLD", "archived", "x")), None), ItemUnavailableError),
    ],
)
def test_post_errors(response, expected):
    t = FakeTransport()
    client = WebClient(t)
    t.post_response = (*response, "https://old.reddit.com/api/del")
    with pytest.raises(expected):
        client.delete(Item(id="a", kind="comment"))


# ---- Playwright glue, with fake pages -------------------------------------------------------
class FakePage:
    def __init__(self, logged_in_after: int):
        self.calls = 0
        self.logged_in_after = logged_in_after
        self.url = "about:blank"
        self.evals = 0

    def goto(self, url, wait_until=None):
        self.calls += 1
        if self.calls > self.logged_in_after:
            self.url = url
        else:
            self.url = "https://old.reddit.com/login/?reason=lor2&dest=secret"

    def wait_for_load_state(self, state=None):
        pass

    def evaluate(self, script, args):
        self.evals += 1
        return {"status": 200, "retryAfter": None, "finalUrl": self.url, "text": page_json()}


def test_connect_waits_until_logged_in():
    page = FakePage(logged_in_after=1)  # first check: still on the login page
    messages = []
    client = None
    for _ in range(browser.LOGIN_ATTEMPTS):
        try:
            client = browser.BrowserSession._connect(page, RuntimeError)
            break
        except AuthError as exc:
            messages.append(str(exc))
    assert client is not None and client.username == ME
    assert len(messages) == 1 and "login page" in messages[0]
    assert "secret" not in messages[0]  # query strings are not shown/logged


def test_transport_retries_when_tab_navigates():
    class NavError(Exception):
        pass

    class Page(FakePage):
        def evaluate(self, script, args):
            if self.evals == 0:
                self.evals += 1
                raise NavError("Execution context was destroyed, because of a navigation")
            return super().evaluate(script, args)

    page = Page(0)
    page.url = "https://old.reddit.com/"
    status, text, _, _ = PlaywrightTransport(page, NavError).request("GET", "/", parse=True)
    assert status == 200 and ME in text and page.evals == 2

    class Broken(FakePage):
        def evaluate(self, script, args):
            raise NavError("something else")

    with pytest.raises(WiperError):
        PlaywrightTransport(Broken(0), NavError).request("GET", "/x")


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.opened = []

    def new_page(self):
        page = FakePage(logged_in_after=0)
        self.opened.append(page)
        return page

    def close(self):
        pass


def test_session_reuses_saved_login(monkeypatch):
    import playwright.sync_api as sync_api

    saved = FakePage(logged_in_after=0)  # profile already logged in
    ctx = FakeContext(saved)
    session = browser.BrowserSession(
        wait_for_login=lambda: pytest.fail("should not ask to log in"),
        on_already_logged_in=lambda: None,
    )

    def fake_launch(error_type):
        session._context = ctx

    class FakePW:
        def stop(self):
            pass

    class Starter:
        def start(self):
            return FakePW()

    monkeypatch.setattr(session, "_launch", fake_launch)
    monkeypatch.setattr(sync_api, "sync_playwright", Starter)
    client = session.__enter__()
    assert client.username == ME and ctx.opened == []  # no login tab opened
    session.__exit__(None, None, None)


# ---- The in-browser HTML parser, run in a real headless browser on a fixture page ----------
OLD_REDDIT_HTML = """<!doctype html><html><head>
<script id="config">r.setup({"logged": "testuser", "modhash": "abc123", "other": 1})</script>
</head><body><div id="siteTable" class="sitetable">
<div class="thing comment" data-fullname="t1_c1" data-author="testuser" data-subreddit="python"
     data-permalink="/r/python/comments/x/t/c1/" data-timestamp="1600000000000">
  <div class="entry"><span class="score unvoted" title="42">42 points</span>
    <form class="usertext"><div class="usertext-body"><div class="md"><p>my comment</p></div>
    </div></form></div></div>
<div class="thing link self" data-fullname="t3_p1" data-author="testuser" data-subreddit="pics"
     data-permalink="/r/pics/comments/p1/t/" data-timestamp="1500000000000" data-score="7"
     data-url="/r/pics/comments/p1/t/">
  <div class="entry"><a class="title" href="#">My title</a></div></div>
<div class="thing comment" data-fullname="t1_c2" data-author="testuser" data-subreddit="python"
     data-permalink="/r/python/comments/x/t/c2/">
  <div class="entry"><p class="tagline"><span class="score unvoted" title="3">3 points</span>
    <time title="x" datetime="2024-03-05T10:20:30+00:00" class="live-timestamp">1 year ago</time>
    </p><form class="usertext"><div class="usertext-body"><div class="md"><p>no stamp</p></div>
    </div></form></div></div>
</div><span class="next-button">
<a href="https://old.reddit.com/user/testuser/comments/?count=25&amp;after=t1_c1">next</a></span>
</body></html>"""


def test_fetch_js_parses_old_reddit_html():
    """Runs the real in-page parser against a fixture page. Every request is answered locally."""
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"no Playwright browser installed: {exc}")
        page = b.new_page()
        page.route(
            "**/*",
            lambda route: route.fulfill(status=200, content_type="text/html", body=OLD_REDDIT_HTML),
        )
        page.goto("https://old.reddit.com/")
        status, text, _, _ = PlaywrightTransport(page).request("GET", "/user/x/", parse=True)
        b.close()
    finally:
        pw.stop()
    data = json.loads(text)
    assert status == 200 and data["logged"] == ME and data["modhash"] == "abc123"
    assert data["after"] == "t1_c1"
    c1, p1, c2 = (item_from_scrape(t) for t in data["things"])
    assert c1.fullname == "t1_c1" and c1.text == "my comment" and c1.score == 42
    assert c1.subreddit == "python" and c1.created.year == 2020
    assert p1.fullname == "t3_p1" and p1.is_self and p1.title == "My title" and p1.score == 7
    # Real comment listings carry no data-timestamp; the date comes from <time datetime>.
    assert c2.created_utc == 1709634030.0 and c2.score == 3 and c2.text == "no stamp"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1600000000000", 1600000000.0),
        ("2024-03-05T10:20:30+00:00", 1709634030.0),
        ("2024-03-05T10:20:30Z", 1709634030.0),
        ("2024-03-05T10:20:30", 1709634030.0),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_scraped_dates(raw, expected):
    item = item_from_scrape({"fullname": "t1_x", "author": ME, "timestamp": raw})
    assert item.created_utc == expected


def test_page_rate_limit_retries_same_page_without_restarting():
    sleeps = []

    class Limited(FakeTransport):
        hits = 0

        def request(self, method, path, form=None, modhash=None, parse=False):
            if "after=t1_b" in path and Limited.hits < 2:
                Limited.hits += 1
                return 429, "", "5", "https://old.reddit.com" + path
            return super().request(method, path, form, modhash, parse)

    t = Limited(
        pages={
            ("/user/testuser/comments/", ""): page_json([thing("t1_a"), thing("t1_b")], "t1_b"),
            ("/user/testuser/comments/", "t1_b"): page_json([thing("t1_c")]),
        }
    )
    client = WebClient(t, page_delay=1.0, sleep=sleeps.append)
    names = [i.fullname for i in client.listing("comment", "new")]
    assert names == ["t1_a", "t1_b", "t1_c"]  # no duplicates: page 1 wasn't re-read
    first_page_reads = [r for r in t.requests if r[1].startswith("/user/") and "after" not in r[1]]
    assert len(first_page_reads) == 1
    assert sum(1 for s in sleeps if s >= 5) == 2  # two backoffs honoring Retry-After
