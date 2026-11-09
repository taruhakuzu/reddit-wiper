import json
from contextlib import contextmanager, suppress

from rich.console import Console
from typer.testing import CliRunner

from conftest import FakeClient, make_item
from reddit_wiper import cli, config
from reddit_wiper.filters import Filters
from reddit_wiper.runner import RunOptions, run


def _console():
    return Console(record=True, width=120)


def _client_with(n_comments=3, n_posts=1, limit=1000):
    client = FakeClient(visible_limit=limit)
    for i in range(n_comments):
        client.add(make_item(f"c{i}", created_utc=1_600_000_000.0 + i))
    for i in range(n_posts):
        client.add(make_item(f"p{i}", "post", created_utc=1_500_000_000.0 + i))
    return client


def test_dry_run_changes_nothing(no_sleep):
    client = _client_with()
    console = _console()
    result, _ = run(client, RunOptions(), console, confirm=lambda u: True, sleep=no_sleep[0])
    assert result.dry_run and client.deletes == [] and client.edits == []
    assert result.stats.selected == 4
    text = console.export_text()
    assert "DRY RUN" in text and "4 items match" in text
    assert not config.backups_dir().exists()


def test_execute_requires_confirmation(no_sleep):
    client = _client_with()
    result, _ = run(
        client, RunOptions(execute=True), _console(), confirm=lambda u: False, sleep=no_sleep[0]
    )
    assert result.cancelled and client.deletes == []


def test_execute_backs_up_then_deletes(no_sleep):
    client = _client_with()
    seen = []
    result, state = run(
        client,
        RunOptions(execute=True),
        _console(),
        confirm=lambda u: seen.append(u) or True,
        sleep=no_sleep[0],
    )
    assert seen == ["testuser"]
    assert sorted(client.deletes) == ["t1_c0", "t1_c1", "t1_c2", "t3_p0"]
    assert client.deletes[0] == "t3_p0"  # oldest first
    assert result.stats.deleted == 4 and result.stats.edited == 4
    backup_json = next(p for p in result.backups if p.suffix == ".json")
    data = json.loads(backup_json.read_text(encoding="utf-8"))
    # Backup holds the ORIGINAL text, written before anything was overwritten.
    assert data["count"] == 4 and all(i["text"] == "hello" for i in data["items"])
    assert len(state.done) == 4


def test_no_backup(no_sleep):
    client = _client_with()
    result, _ = run(
        client,
        RunOptions(execute=True, backup=False),
        _console(),
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert result.backups == [] and result.stats.deleted == 4


def test_filters_applied(no_sleep):
    client = _client_with()
    opts = RunOptions(execute=True, filters=Filters(kinds=("post",)))
    run(client, opts, _console(), confirm=lambda u: True, sleep=no_sleep[0])
    assert client.deletes == ["t3_p0"]


def test_rescan_finds_items_hidden_by_listing_limit(no_sleep):
    client = _client_with(n_comments=5, n_posts=0, limit=2)
    result, _ = run(
        client,
        RunOptions(execute=True, passes=5),
        _console(),
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert len(client.deletes) == 5
    assert result.stats.deleted == 5 and result.stats.found == 5


def test_limit_handles_only_oldest_n_and_skips_rescan(no_sleep):
    client = _client_with(n_comments=5, n_posts=0, limit=2)
    console = _console()
    result, _ = run(
        client,
        RunOptions(execute=True, limit=1),
        console,
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert client.deletes == ["t1_c3"]  # oldest of the 2 visible items; no re-scan pass
    assert result.stats.selected == 1 and result.stats.deleted == 1
    assert "over --limit 1" in console.export_text()
    # Same number of listing reads as a plain dry run: one scan, no re-scan.
    dry = _client_with(n_comments=5, n_posts=0, limit=2)
    run(dry, RunOptions(rescan=True), _console(), confirm=lambda u: True, sleep=no_sleep[0])
    assert client.listing_calls == dry.listing_calls


def test_cli_limit_option_reaches_run_options():
    opts = cli._build_options(
        execute=False,
        overwrite=True,
        export=None,
        only=cli.Only.all,
        subreddit=None,
        exclude_subreddit=None,
        after=None,
        before=None,
        min_score=None,
        max_score=None,
        no_backup=False,
        rounds=1,
        passes=5,
        delay=0.0,
        edit_delay=0.0,
        show=5,
        limit=1,
    )
    assert opts.limit == 1


def test_resume_after_interrupt(no_sleep):
    client = _client_with(n_comments=3, n_posts=0)
    original = client.delete
    calls = {"n": 0}

    def interrupt_on_second(item):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        original(item)

    client.delete = interrupt_on_second
    with suppress(KeyboardInterrupt):
        run(
            client,
            RunOptions(execute=True, passes=1),
            _console(),
            confirm=lambda u: True,
            sleep=no_sleep[0],
        )
    client.delete = original
    result, _ = run(
        client,
        RunOptions(execute=True, passes=1),
        _console(),
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert result.stats.deleted == 2  # only the two that weren't finished
    assert len(client.deletes) == 3


def test_export_items_are_included(tmp_path, no_sleep):
    client = _client_with(n_comments=1, n_posts=0)
    client.add(make_item("old", created_utc=1_200_000_000.0))  # exists, but not listed
    client.visible_limit = 1
    export = tmp_path / "export"
    export.mkdir()
    (export / "comments.csv").write_text(
        "id,permalink,date,ip,subreddit,gildings,link,parent,body,media\n"
        "old,,2008-01-01 00:00:00 UTC,,test,0,,,hello,\n"
        "neverexisted,,2008-01-01 00:00:00 UTC,,test,0,,,x,\n",
        encoding="utf-8",
    )
    _result, _ = run(
        client,
        RunOptions(execute=True, export=export, passes=1),
        _console(),
        confirm=lambda u: True,
        sleep=no_sleep[0],
    )
    assert sorted(client.deletes) == ["t1_c0", "t1_old"]


# ---- CLI --------------------------------------------------------------------------------------
runner = CliRunner()


def test_cli_help_and_version():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0 and "DRY RUN" in result.output
    assert runner.invoke(cli.app, ["--version"]).output.startswith("reddit-wiper ")


def test_cli_wipe_without_config_fails():
    result = runner.invoke(cli.app, ["wipe"])
    assert result.exit_code == 1 and "setup" in result.output


def _patch_login(monkeypatch, client, captured):
    def fake_login(creds, password, otp=None):
        captured.update(password=password, otp=otp)
        return client

    monkeypatch.setattr("reddit_wiper.auth.login", fake_login)
    monkeypatch.setattr("reddit_wiper.runner.time.sleep", lambda s: None)
    monkeypatch.setattr("reddit_wiper.delete.time.sleep", lambda s: None)


def test_cli_wipe_dry_run_then_execute(monkeypatch):
    config.save_credentials(
        config.Credentials(client_id="c", client_secret="s", username="testuser", two_factor=True)
    )
    client = _client_with()
    captured = {}
    _patch_login(monkeypatch, client, captured)

    result = runner.invoke(cli.app, ["wipe", "--delay", "0"], input="pw\n123456\n")
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output and client.deletes == []
    assert captured == {"password": "pw", "otp": "123456"}
    assert "pw" not in result.output.replace("password", "")

    result = runner.invoke(
        cli.app,
        ["wipe", "--execute", "--delay", "0", "--edit-delay", "0"],
        input="pw\n123456\nwrongname\n",
    )
    assert "Cancelled" in result.output and client.deletes == []

    result = runner.invoke(
        cli.app,
        ["wipe", "--execute", "--delay", "0", "--edit-delay", "0", "--only", "posts"],
        input="pw\n123456\nTestUser\n",
    )
    assert result.exit_code == 0, result.output
    assert client.deletes == ["t3_p0"] and "Deleted" in result.output


def test_cli_browser(monkeypatch):
    client = _client_with()

    @contextmanager
    def fake_session(**kwargs):
        assert kwargs["profile_dir"] == config.browser_profile_dir()
        yield client

    monkeypatch.setattr("reddit_wiper.browser.BrowserSession", fake_session)
    result = runner.invoke(cli.app, ["browser", "-s", "test", "--delay", "0"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output and "Logged in as u/testuser" in result.output


def test_cli_setup_saves_credentials(monkeypatch):
    result = runner.invoke(cli.app, ["setup"], input="myid\nmysecret\nu/someone\nn\nn\nn\n")
    assert result.exit_code == 0, result.output
    creds = config.load_credentials()
    assert (creds.client_id, creds.client_secret, creds.username) == ("myid", "mysecret", "someone")
    assert "mysecret" not in result.output


def test_cli_status_and_reset():
    from reddit_wiper.state import State, state_path

    state = State.load(state_path(config.state_dir(), "bob"), "bob")
    state.mark_done("t1_a")
    result = runner.invoke(cli.app, ["status", "bob"])
    assert "deleted: 1" in result.output
    assert runner.invoke(cli.app, ["reset", "bob", "--yes"]).exit_code == 0
    assert not state.path.exists()


def test_cli_browser_logout():
    profile = config.browser_profile_dir()
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("x")
    assert runner.invoke(cli.app, ["browser-logout", "--yes"]).exit_code == 0
    assert not profile.exists()
    assert "No saved" in runner.invoke(cli.app, ["browser-logout", "--yes"]).output
