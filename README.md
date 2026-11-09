# reddit-wiper

**Delete every comment and post on your own Reddit account. Open source, local-only, safe by default.**

[Türkçe README](README.tr.md)

- **Dry run by default.** Nothing is changed until you add `--execute` *and* type your username.
- **Overwrite, then delete.** Comments and text posts are first edited to random text, then deleted.
- **Gets past the 1000-item limit.** It scans every listing (new, hot, top, controversial for all
  time ranges), re-scans after deleting, and can read your **Reddit data export** to reach
  everything you ever posted.
- **Backs up first.** Everything is saved to local JSON + CSV before it is deleted.
- **Resumable.** Interrupted? Run the same command again; it continues where it stopped.
- **Two modes.** *API mode* uses your own Reddit "script" app. *Browser mode* needs no API app:
  you log in yourself in a browser window.
- **No telemetry.** The tool only talks to reddit.com. Nothing is sent anywhere else.
- Works on Windows, macOS and Linux with Python 3.10+.

> [!WARNING]
> **Deletion is permanent.** Reddit has no undo. Read [Warnings](#warnings) before using `--execute`.

---

## Contents

- [Install](#install)
- [Choose a mode](#choose-a-mode)
- [API mode: create your Reddit app](#api-mode-create-your-reddit-app)
- [Browser mode](#browser-mode)
- [Usage examples](#usage-examples)
- [Getting past the 1000-item limit: the data export](#getting-past-the-1000-item-limit-the-data-export)
- [Where files are stored](#where-files-are-stored)
- [Privacy and security](#privacy-and-security)
- [Warnings](#warnings)
- [FAQ](#faq)
- [Development](#development)

## Install

You need Python 3.10 or newer. The easiest way is [pipx](https://pipx.pypa.io/), which installs
command-line tools in their own environment:

```bash
pipx install reddit-wiper
```

For browser mode, install the optional extra and download a browser once:

```bash
pipx install "reddit-wiper[browser]"
reddit-wiper install-browser
```

(`install-browser` downloads Playwright's Chromium, about 150 MB. If you already have Google Chrome
or Microsoft Edge you can skip it and use `--channel chrome` or `--channel msedge`.)

Plain `pip install reddit-wiper` works too. To install from source:

```bash
git clone https://github.com/taruhakuzu/reddit-wiper.git
cd reddit-wiper
pipx install ".[browser]"
```

## Choose a mode

| | API mode (`reddit-wiper wipe`) | Browser mode (`reddit-wiper browser`) |
|---|---|---|
| Needs | Your own Reddit "script" app, **approved by Reddit** | Nothing; you log in in a browser window |
| Your password | Given to the tool (sent only to Reddit) | Never given to the tool |
| 2FA | Supported (`--otp`) | Supported (you log in normally) |
| Speed | Fast, uses Reddit's official API limits | Similar, with a polite delay between items |

**Reddit now requires approval for API access.** Since the
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy),
Reddit says you must *"request access and get explicit approval before accessing any Reddit data
through our API"*. A newly created app may not work until Reddit approves it, approval can take
time, and it can be refused. **If you can't get an approved app, use browser mode.**

## API mode: create your Reddit app

Each user creates **their own** app. This repository contains no client ID or secret, and never will.

1. Log in to Reddit and open <https://www.reddit.com/prefs/apps>.
2. Click **"are you a developer? create an app..."** (or **"create another app..."**).
3. Fill in the form:
   - **name**: anything, e.g. `my-wiper`
   - **type**: select **script** ("Script for personal use")
   - **description**: optional
   - **redirect uri**: `http://localhost:8080` (required by the form, not used)
4. Click **create app**. Read and accept Reddit's developer terms if asked.
5. Note two values:
   - the **client ID**: the short string just under "personal use script"
   - the **secret**: next to "secret"
6. **Request API access.** Follow the API access request link on that page or in the
   [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
   and describe your use honestly: *"a personal script that deletes my own comments and posts
   from my own account; non-commercial; no data is stored or shared"*. Wait for approval.
   Reddit changes this process from time to time; their pages are the authority.
7. Run the setup wizard:

   ```bash
   reddit-wiper setup
   ```

   It asks for the client ID, secret, username, whether you use 2FA, and whether to save your
   password (default: **no**, you'll be asked each run). It can test the login at the end.

**Two-factor authentication:** if your account uses 2FA, answer "yes" in setup or pass `--otp`.
You'll be asked for the current 6-digit code; the tool sends it the way PRAW expects
(`password:123456`). Reddit's login token lasts about one hour and **can't be renewed without a
new code**, so a very long run with 2FA may stop with a login error. Just run the same command
again with a fresh code: it resumes where it stopped.

Instead of the config file you can use environment variables: `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_USER_AGENT`, `REDDIT_2FA`.

## Browser mode

```bash
reddit-wiper browser             # dry run
reddit-wiper browser --execute   # really delete
```

1. A browser window opens on old.reddit.com's login page.
2. **You log in yourself** (including 2FA). Your password goes to Reddit only; the tool never sees it.
3. Come back to the terminal and press **Enter**.
4. The tool lists your items through your logged-in session, shows the preview, and (with
   `--execute`, after you type your username) overwrites and deletes them using the same requests
   old.reddit.com's own "edit" and "delete" buttons send. It waits 2 seconds between items by default.

**You log in only once.** The browser profile (with your Reddit login) is kept in reddit-wiper's
private data folder, so later runs start already logged in. This matters: logging in again and
again from a brand-new browser looks like a new device every time, and **Reddit may lock the
account and force a password reset**. The saved profile holds your Reddit session cookies, so
treat that folder like a password. Forget it any time with `reddit-wiper browser-logout`, or use
`--fresh-profile` for a throw-away profile that is deleted when the browser closes.

Dry run, confirmation, backups, filters, the data export and resuming all work exactly like API mode.

## Usage examples

```bash
# See what would be deleted (nothing is changed)
reddit-wiper wipe

# Show more of the preview list
reddit-wiper wipe --show 200

# First real test: delete only the single oldest item
reddit-wiper wipe --limit 1 --execute

# Really delete everything (asks you to type your username first)
reddit-wiper wipe --execute

# Only comments, only in two subreddits
reddit-wiper wipe --only comments -s AskReddit -s pics --execute

# Everything except one subreddit
reddit-wiper wipe -x python --execute

# Only items older than 2023, and keep anything with a score above 100
reddit-wiper wipe --before 2023-01-01 --max-score 100 --execute

# Include everything from your data export (gets past the 1000-item limit)
reddit-wiper wipe --export ~/Downloads/export_yourname_20260101.zip --execute

# Delete without overwriting first, and without a backup
reddit-wiper wipe --no-overwrite --no-backup --execute

# The same options work in browser mode
reddit-wiper browser --only posts --after 2020-01-01 --execute

# Progress, file locations, and forgetting progress
reddit-wiper status
reddit-wiper reset yourname
```

Run `reddit-wiper --help` or `reddit-wiper wipe --help` for every option.

| Option | Meaning |
|---|---|
| `--execute` | Really delete. Without it, nothing is changed. |
| `--overwrite / --no-overwrite` | Edit to random text before deleting (default: on). Link/image posts can't be edited and are just deleted. |
| `--export PATH` | Reddit data export ZIP or folder. |
| `--only all\|comments\|posts` | Which kind of item. |
| `-s, --subreddit` / `-x, --exclude-subreddit` | Include / exclude subreddits (repeatable). |
| `--after` / `--before YYYY-MM-DD` | Date range (UTC). |
| `--min-score` / `--max-score` | Score range. Items whose score is unknown are skipped when you use these. |
| `--no-backup` | Don't write the JSON/CSV backup. |
| `--passes N` | How many delete-and-rescan passes (default 5). |
| `--limit N` | Handle at most N items (oldest first) and don't re-scan. Good for a first test. |
| `--rescan` | Read Reddit again instead of reusing the scan saved in the last 24 hours. |
| `--delay`, `--edit-delay` | Seconds between items / between overwrite and delete. |

## Getting past the 1000-item limit: the data export

Reddit listings only return about the newest 1000 items of each kind. reddit-wiper checks many
listings (`new`, `hot`, `top` and `controversial` for every time range) and re-scans after each
deletion pass, which usually surfaces more. To be sure you get **everything**, use your data export:

1. Open <https://www.reddit.com/settings/data-request> (Settings, Privacy, "Request your data").
2. Choose the GDPR (or CCPA) request option and submit it.
3. Reddit sends you a message when it's ready (this can take days, sometimes weeks).
4. Download the ZIP. You don't need to unzip it.
5. Run with `--export path/to/the.zip` (or the extracted folder).

The tool reads only `comments.csv` and `posts.csv`. It looks each item up on Reddit to get its
current state and skips anything already deleted. The IP addresses in the export are ignored.

## Where files are stored

Nothing is written inside the project folder. Run `reddit-wiper status` to see the exact paths.

| What | Windows | macOS | Linux |
|---|---|---|---|
| Credentials | `%LOCALAPPDATA%\reddit-wiper\credentials.env` | `~/Library/Application Support/reddit-wiper/credentials.env` | `~/.config/reddit-wiper/credentials.env` |
| Backups, progress, saved scans, logs | `%LOCALAPPDATA%\reddit-wiper\{backups,state,scans,logs}` | `~/Library/Application Support/reddit-wiper/...` | `~/.local/share/reddit-wiper/...` |

The credentials file is created with owner-only permissions where the OS supports it. You can
move everything elsewhere with `REDDIT_WIPER_CONFIG_DIR` and `REDDIT_WIPER_DATA_DIR`.

**Backups and saved scans contain the text of your items.** Keep them private, or delete them when
you no longer need them (`reddit-wiper reset USERNAME` removes the saved scan and progress).

## Privacy and security

- **No telemetry, no analytics, no update checks.** The tool talks only to `reddit.com`
  (`oauth.reddit.com`/`www.reddit.com` in API mode, `old.reddit.com` in browser mode).
- Your client secret, password, 2FA codes, tokens and session keys are **never** printed or
  written to the log files. Logs are filtered for them as an extra safety net.
- The password is not saved unless you explicitly choose to in `setup`.
- See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Warnings

- **Deletion cannot be undone.** Run a dry run first, and keep the backup.
- **Copies may exist elsewhere.** Third-party archives (for example Arctic Shift, PullPush and
  older Pushshift dumps), search engine caches and the Wayback Machine may have copied your old
  content. Deleting it on Reddit does not delete those copies; you need to ask each of them for
  removal separately (most have a removal request form). Overwriting before deleting helps only
  with archives that re-scan Reddit.
- **Reddit's rules apply.** The tool respects Reddit's rate limits (PRAW's built-in handling plus
  exponential backoff on HTTP 429) and is meant only for your own account. You are responsible for
  using it in line with Reddit's
  [User Agreement](https://redditinc.com/policies/user-agreement) and
  [Developer Terms](https://redditinc.com/policies/developer-terms).
- Deleting a post leaves other people's comments under it. Your username is removed from your
  deleted items, but quotes of you in other people's replies stay.
- Messages, chats, votes, saved items and your account itself are **not** touched. To delete the
  account, use Reddit's settings after wiping.

## FAQ

**Is a dry run really harmless?** Yes. Without `--execute` the tool only reads from Reddit. The
only things it writes are a local log file and the saved scan.

**It found fewer items than I have.** That's the 1000-item listing limit. Use `--execute`
(re-scans pick up more after each pass) and/or `--export` with your data export.

**What happens if I press Ctrl+C or my computer sleeps?** Progress is saved after every item. Run
the same command again; finished items are skipped.

**Some items failed.** They're logged with the reason and retried next time you run the command.
Locked and archived items usually can't be edited, but can still be deleted.

**I get "Login failed" in API mode.** Check the client ID/secret, username and password. Make sure
the app type is **script** and that Reddit has approved your API access. With 2FA, use `--otp`.

**Browser mode says "rate limited" over and over.** Reddit limits how fast web pages can be loaded
much more tightly than the API. reddit-wiper pauses 2 seconds between pages and backs off when
Reddit says "slow down". If the limit doesn't clear, the scan stops early and the tool carries on
with the items it already found. Every scan is also saved for 24 hours, so the `--execute` run
after a dry run doesn't scan Reddit again (use `--rescan` to force a fresh scan, for example later
on to pick up what a cut-short scan missed). Don't run several copies at once.

**Why old.reddit.com in browser mode?** It's simpler and more stable than the redesign.

**Can I use this on someone else's account?** No. It only works on the account you log in with,
and you must type that username to confirm.

**Does it delete my account?** No. It deletes content. Delete the account in Reddit's settings if
you want to.

## Development

```bash
git clone https://github.com/taruhakuzu/reddit-wiper.git
cd reddit-wiper
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,browser]"
pytest
ruff check . && ruff format --check .
```

Tests mock Reddit completely and block network access; they never touch a real account. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). This project is not affiliated with or endorsed by Reddit, Inc.
