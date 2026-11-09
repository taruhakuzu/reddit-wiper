# Contributing

Thanks for helping! Bug reports, fixes, docs and translations are all welcome.

## Setup

```bash
git clone https://github.com/taruhakuzu/reddit-wiper.git
cd reddit-wiper
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,browser]"
```

## Before opening a pull request

```bash
ruff format .
ruff check .
pytest
```

CI runs the same checks on Windows, macOS and Linux with Python 3.10 to 3.14.

## Ground rules

- **Tests never touch a real Reddit account.** Mock PRAW or use the `FakeClient` in
  `tests/conftest.py`. Network access is blocked during tests.
- **Never commit real data.** No usernames, comment text, exports, backups, logs, client IDs or
  secrets, not even in tests or issue reports. Use made-up values like `testuser`.
- **Safety first.** Anything destructive must stay behind `--execute` and the username confirmation.
- **No telemetry.** The tool must only talk to reddit.com.
- Use `pathlib` and `platformdirs` for paths; no OS-specific shell commands.
- User-facing text, code and comments are in English (README translations are welcome).
- Add a line to `CHANGELOG.md` under "Unreleased".
