"""Command line interface."""

import logging
import shutil
import subprocess
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from reddit_wiper import __version__, config
from reddit_wiper.errors import WiperError
from reddit_wiper.filters import Filters
from reddit_wiper.logging_utils import register_secret, setup_logging
from reddit_wiper.runner import RunOptions, print_report, run
from reddit_wiper.scan_cache import clear_scan, load_scan, scan_path
from reddit_wiper.state import State, state_path

console = Console()
log = logging.getLogger("reddit_wiper")

app = typer.Typer(
    name="reddit-wiper",
    help=(
        "Delete the comments and posts on YOUR OWN Reddit account.\n\n"
        "Every command is a harmless DRY RUN unless you add --execute. "
        "Start with `reddit-wiper setup` (API mode) or `reddit-wiper browser` (no API app needed)."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


class Only(str, Enum):
    all = "all"
    comments = "comments"
    posts = "posts"


DATE_FORMATS = ["%Y-%m-%d"]

# ---- shared options -------------------------------------------------------------------------
ExecuteOpt = Annotated[
    bool,
    typer.Option(
        "--execute", help="Really delete. Without this flag nothing is changed (dry run)."
    ),
]
OverwriteOpt = Annotated[
    bool,
    typer.Option(
        "--overwrite/--no-overwrite",
        help="Overwrite comments and text posts with random text before deleting them.",
    ),
]
ExportOpt = Annotated[
    Path | None,
    typer.Option(
        "--export",
        help="Reddit data export (ZIP or extracted folder) to reach items older than "
        "the 1000-item listing limit.",
        exists=True,
        resolve_path=True,
    ),
]
OnlyOpt = Annotated[Only, typer.Option("--only", help="Only comments, only posts, or all.")]
SubOpt = Annotated[
    list[str] | None,
    typer.Option("--subreddit", "-s", help="Only these subreddits (repeatable)."),
]
ExSubOpt = Annotated[
    list[str] | None,
    typer.Option("--exclude-subreddit", "-x", help="Never touch these subreddits (repeatable)."),
]
AfterOpt = Annotated[
    datetime | None,
    typer.Option(
        "--after", formats=DATE_FORMATS, help="Only items created on/after this date (YYYY-MM-DD)."
    ),
]
BeforeOpt = Annotated[
    datetime | None,
    typer.Option(
        "--before", formats=DATE_FORMATS, help="Only items created before this date (YYYY-MM-DD)."
    ),
]
MinScoreOpt = Annotated[
    int | None, typer.Option("--min-score", help="Only items with score >= this.")
]
MaxScoreOpt = Annotated[
    int | None,
    typer.Option(
        "--max-score", help="Only items with score <= this (e.g. keep your popular posts)."
    ),
]
NoBackupOpt = Annotated[
    bool, typer.Option("--no-backup", help="Do not write a local JSON/CSV backup before deleting.")
]
RoundsOpt = Annotated[
    int, typer.Option("--rounds", min=1, max=10, help="Listing rounds until no new items appear.")
]
PassesOpt = Annotated[
    int,
    typer.Option(
        "--passes",
        min=1,
        max=50,
        help="Delete-and-rescan passes (older items surface after newer ones are deleted).",
    ),
]
DelayOpt = Annotated[float, typer.Option("--delay", min=0.0, help="Seconds to wait between items.")]
EditDelayOpt = Annotated[
    float,
    typer.Option(
        "--edit-delay", min=0.0, help="Seconds to wait between overwriting and deleting an item."
    ),
]
LimitOpt = Annotated[
    int | None,
    typer.Option(
        "--limit",
        min=1,
        help="Handle at most N items (oldest first) and skip the re-scan. Handy for a first test.",
    ),
]
RescanOpt = Annotated[
    bool,
    typer.Option(
        "--rescan",
        help="Read Reddit again instead of reusing the scan saved by a run in the last 24 hours.",
    ),
]
ShowOpt = Annotated[
    int, typer.Option("--show", min=0, help="How many items to list in the preview.")
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="More detailed console output.")]


def _version(value: bool) -> None:
    if value:
        console.print(f"reddit-wiper {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    pass


def _build_options(
    *,
    execute: bool,
    overwrite: bool,
    export: Path | None,
    only: Only,
    subreddit: list[str] | None,
    exclude_subreddit: list[str] | None,
    after: datetime | None,
    before: datetime | None,
    min_score: int | None,
    max_score: int | None,
    no_backup: bool,
    rounds: int,
    passes: int,
    delay: float,
    edit_delay: float,
    show: int,
    limit: int | None = None,
    rescan: bool = False,
) -> RunOptions:
    kinds = {"all": ("comment", "post"), "comments": ("comment",), "posts": ("post",)}[only.value]
    filters = Filters(
        kinds=kinds,
        include_subreddits=set(subreddit or []),
        exclude_subreddits=set(exclude_subreddit or []),
        after=after,
        before=before,
        min_score=min_score,
        max_score=max_score,
    )
    return RunOptions(
        filters=filters,
        execute=execute,
        overwrite=overwrite,
        backup=not no_backup,
        export=export,
        rounds=rounds,
        passes=passes,
        delay=delay,
        edit_delay=edit_delay,
        show=show,
        limit=limit,
        rescan=rescan,
    )


def _confirm(username: str) -> bool:
    console.print(
        "\n[bold red]This permanently deletes the items listed above. It cannot be undone.[/]"
    )
    typed = typer.prompt(
        f"Type your username ({username}) to confirm", default="", show_default=False
    )
    return typed.strip().removeprefix("u/").lower() == username.lower()


def _run_and_report(client, opts: RunOptions, log_file: Path) -> None:
    try:
        result, state = run(client, opts, console, confirm=_confirm)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Progress is saved; run the same command to resume.")
        raise typer.Exit(130) from None
    print_report(console, result, log_file, state)
    if result.stats.failed:
        raise typer.Exit(1)


def _fail(message: str, code: int = 1) -> None:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(code)


# ---- commands --------------------------------------------------------------------------------
@app.command()
def setup() -> None:
    """Interactive setup: save your Reddit script-app credentials to your user config folder."""
    path = config.credentials_file()
    current = config.load_credentials()
    console.print(
        "Create a personal [bold]script[/] app at https://www.reddit.com/prefs/apps first "
        "(see the README). Reddit must approve new API apps before they work.\n"
        "Credentials are stored only on this computer, in:\n  " + str(path) + "\n"
    )
    client_id = typer.prompt("Client ID (under the app name)", default=current.client_id or None)
    client_secret = typer.prompt(
        "Client secret", hide_input=True, default=current.client_secret or None, show_default=False
    )
    username = (
        typer.prompt("Reddit username (without u/)", default=current.username or None)
        .strip()
        .removeprefix("u/")
    )
    two_factor = typer.confirm(
        "Does this account use two-factor authentication (2FA)?", default=current.two_factor
    )
    store_password = typer.confirm(
        "Save your password in the credentials file? (No = you'll be asked each run; safer)",
        default=False,
    )
    password = ""
    if store_password:
        password = typer.prompt("Reddit password", hide_input=True)
    register_secret(client_secret)
    register_secret(password)
    creds = config.Credentials(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        username=username,
        password=password,
        user_agent=config.default_user_agent(username),
        two_factor=two_factor,
    )
    saved = config.save_credentials(creds, path, store_password=store_password)
    console.print(f"[green]Saved[/] to {saved}")
    if typer.confirm("Test the login now?", default=True):
        from reddit_wiper.auth import login

        pwd = password or typer.prompt("Reddit password", hide_input=True)
        otp = typer.prompt("2FA code", hide_input=True) if two_factor else None
        try:
            client = login(creds, pwd, otp)
        except WiperError as exc:
            _fail(str(exc))
        console.print(
            f"[green]Login works.[/] Logged in as u/{client.username}. "
            "Next: reddit-wiper wipe   (dry run)"
        )


@app.command()
def wipe(
    execute: ExecuteOpt = False,
    overwrite: OverwriteOpt = True,
    export: ExportOpt = None,
    only: OnlyOpt = Only.all,
    subreddit: SubOpt = None,
    exclude_subreddit: ExSubOpt = None,
    after: AfterOpt = None,
    before: BeforeOpt = None,
    min_score: MinScoreOpt = None,
    max_score: MaxScoreOpt = None,
    no_backup: NoBackupOpt = False,
    rounds: RoundsOpt = 2,
    passes: PassesOpt = 5,
    delay: DelayOpt = 1.0,
    edit_delay: EditDelayOpt = 2.0,
    show: ShowOpt = 25,
    limit: LimitOpt = None,
    rescan: RescanOpt = False,
    otp: Annotated[
        bool,
        typer.Option("--otp", help="Ask for a 2FA code (also enabled by REDDIT_2FA=true / setup)."),
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """[bold]API mode[/]: find and delete your items using your Reddit script app."""
    log_file = setup_logging(console, verbose)
    opts = _build_options(
        **{k: v for k, v in locals().items() if k not in ("otp", "verbose", "log_file")}
    )
    from reddit_wiper.auth import login

    creds = config.load_credentials()
    try:
        creds.require_api()
    except WiperError as exc:
        _fail(str(exc))
    password = creds.password or typer.prompt(
        f"Reddit password for u/{creds.username}", hide_input=True
    )
    code = typer.prompt("2FA code", hide_input=True) if (otp or creds.two_factor) else None
    try:
        client = login(creds, password, code)
        _run_and_report(client, opts, log_file)
    except WiperError as exc:  # includes AuthError
        _fail(str(exc))
    except (typer.Exit, KeyboardInterrupt):
        raise
    except Exception as exc:  # keep tracebacks in the log file, not on screen
        log.debug("Unexpected error", exc_info=True)
        _fail(f"Unexpected error: {exc}. Details are in the log file: {log_file}")


@app.command()
def browser(
    execute: ExecuteOpt = False,
    overwrite: OverwriteOpt = True,
    export: ExportOpt = None,
    only: OnlyOpt = Only.all,
    subreddit: SubOpt = None,
    exclude_subreddit: ExSubOpt = None,
    after: AfterOpt = None,
    before: BeforeOpt = None,
    min_score: MinScoreOpt = None,
    max_score: MaxScoreOpt = None,
    no_backup: NoBackupOpt = False,
    rounds: RoundsOpt = 1,
    passes: PassesOpt = 5,
    delay: DelayOpt = 2.0,
    edit_delay: EditDelayOpt = 2.0,
    show: ShowOpt = 25,
    limit: LimitOpt = None,
    rescan: RescanOpt = False,
    channel: Annotated[
        str | None,
        typer.Option("--channel", help="Browser to use: chromium (downloaded), chrome or msedge."),
    ] = None,
    fresh_profile: Annotated[
        bool,
        typer.Option(
            "--fresh-profile",
            help="Use a throw-away browser profile and don't keep the login. Logging in from a "
            "new profile every time can make Reddit lock your account.",
        ),
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """[bold]Browser mode[/]: no API app needed. You log in yourself in a browser window.

    The login is kept in a private browser profile so you only log in once
    (forget it with `reddit-wiper browser-logout`).
    """
    log_file = setup_logging(console, verbose)
    opts = _build_options(
        **{
            k: v
            for k, v in locals().items()
            if k not in ("channel", "verbose", "log_file", "fresh_profile")
        }
    )
    from reddit_wiper.browser import BrowserSession

    prompted = []

    def wait_for_login() -> None:
        if not prompted:
            console.print(
                "\nA browser window opened. [bold]Log in to Reddit there[/] (your password goes "
                "only to Reddit, never to this tool).\nWhen you are logged in and see your Reddit "
                "front page, come back here and press [bold]Enter[/]."
            )
        prompted.append(True)
        input()

    def not_logged_in(message: str) -> None:
        console.print(
            f"[yellow]{message}[/]\nFinish logging in in the browser window (complete any 2FA or "
            "verification step), then press [bold]Enter[/] here again."
        )

    try:
        with BrowserSession(
            channel=None if channel == "chromium" else channel,
            wait_for_login=wait_for_login,
            on_not_logged_in=not_logged_in,
            profile_dir=None if fresh_profile else config.browser_profile_dir(),
            on_already_logged_in=lambda: console.print("Using your saved browser login."),
        ) as client:
            console.print(f"Logged in as u/{client.username}")
            _run_and_report(client, opts, log_file)
    except WiperError as exc:
        _fail(str(exc))
    except (typer.Exit, KeyboardInterrupt):
        raise
    except Exception as exc:  # keep tracebacks in the log file, not on screen
        log.debug("Unexpected error", exc_info=True)
        _fail(f"Unexpected error: {exc}. Details are in the log file: {log_file}")


@app.command("browser-logout")
def browser_logout(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask.")] = False,
) -> None:
    """Forget the saved browser-mode login (deletes the private browser profile)."""
    profile = config.browser_profile_dir()
    if not profile.exists():
        console.print("No saved browser login.")
        return
    if yes or typer.confirm(f"Delete {profile}?", default=False):
        shutil.rmtree(profile)
        console.print("Saved browser login deleted.")


@app.command("install-browser")
def install_browser() -> None:
    """Download the Chromium build Playwright uses for browser mode (one time, ~150 MB)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        _fail('Playwright is not installed. Run: pipx install "reddit-wiper[browser]"')
    raise typer.Exit(subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"]))


@app.command()
def status(
    username: Annotated[str | None, typer.Argument(help="Defaults to the configured user.")] = None,
) -> None:
    """Show saved progress and where reddit-wiper keeps its files."""
    user = username or config.load_credentials().username
    console.print(f"Config:  {config.credentials_file()}")
    console.print(f"Data:    {config.data_dir()}")
    console.print(f"Backups: {config.backups_dir()}")
    console.print(f"Logs:    {config.logs_dir()}")
    if not user:
        console.print("No username configured; pass one: reddit-wiper status USERNAME")
        return
    path = state_path(config.state_dir(), user)
    state = State.load(path, user)
    deleted = sum(1 for v in state.done.values() if v == "deleted")
    console.print(f"\nProgress for u/{user} ({path}):")
    console.print(
        f"  deleted: {deleted}, already gone: {len(state.done) - deleted}, "
        f"overwritten: {len(state.edited)}, failed (will retry): {len(state.failed)}"
    )
    scan = load_scan(scan_path(config.scans_dir(), user), max_age=float("inf"))
    if scan is not None:
        console.print(
            f"  saved scan: {len(scan.items)} items, {scan.age / 3600:.1f} h old"
            + ("" if scan.complete else " (cut short by rate limits)")
        )


@app.command()
def reset(
    username: Annotated[str, typer.Argument(help="Whose saved progress to forget.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask.")] = False,
) -> None:
    """Forget saved progress and the saved scan, so the next run starts fresh (backups are kept)."""
    path = state_path(config.state_dir(), username)
    scan = scan_path(config.scans_dir(), username)
    if not path.exists() and not scan.exists():
        console.print("No saved progress.")
        return
    if yes or typer.confirm(f"Delete saved progress and scan for u/{username}?", default=False):
        State.load(path, username).clear()
        clear_scan(scan)
        console.print("Progress cleared.")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        console.print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
