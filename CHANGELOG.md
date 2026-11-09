# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-09

### Added
- API mode (`reddit-wiper wipe`) using your own Reddit "script" app via PRAW, with 2FA support.
- Browser mode (`reddit-wiper browser`) through your own logged-in old.reddit.com session
  (Playwright, optional `[browser]` extra). The tool never sees your password.
- Browser mode keeps its login in a private profile (`browser-logout` to forget it,
  `--fresh-profile` to opt out), so you don't log in every run.
- Interactive `setup` wizard. Credentials are stored in the user config folder, never in the project.
- Scanning of new, hot, top and controversial listings (all time ranges), de-duplicated by ID,
  with re-scans after each delete pass to get past the ~1000-item listing limit.
- Reading the Reddit data export (`--export`, ZIP or folder) to reach every item ever posted.
- Overwrite-then-delete (`--overwrite`, on by default); link/image posts are deleted directly.
- Dry run by default; `--execute` plus typing your username is required to delete.
- Filters: subreddit include/exclude, date range, score range, comments/posts only.
- `--limit N` to handle only the N oldest items (no re-scan), for a careful first run.
- Scan results are saved for 24 hours, so `--execute` after a dry run doesn't scan Reddit again
  (`--rescan` forces a fresh scan). If rate limits cut a scan short, the items already found are
  kept and used.
- JSON + CSV backup before deleting (`--no-backup` to disable).
- Resumable progress file, rate-limit handling with exponential backoff, per-item error handling,
  progress bar, console + file logging with secret redaction, and a summary report.
