"""Console + file logging with secret redaction."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from reddit_wiper import config

log = logging.getLogger("reddit_wiper")

_SECRETS: set[str] = set()
# Generic patterns that look like tokens / auth headers, in case something slips through.
_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)((?:access|refresh)_token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]+"),
    re.compile(
        r"(?i)((?:password|passwd|client_secret|modhash|uh)['\"]?\s*[:=]\s*['\"]?)[^\s'\",&]+"
    ),
]


def register_secret(value: str | None) -> None:
    """Make sure ``value`` never appears in any log output."""
    if value and len(value) >= 4:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    for pattern in _PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        if record.exc_info and not record.exc_text:
            # Pre-render the traceback so secrets inside exception messages are redacted too.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


def setup_logging(console: Console, verbose: bool = False, log_file: Path | None = None) -> Path:
    """Configure logging. Returns the path of the log file."""
    if log_file is None:
        log_dir = config.logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"reddit-wiper-{datetime.now():%Y%m%d-%H%M%S}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)

    redactor = RedactingFilter()

    console_handler = RichHandler(
        console=console, show_path=False, show_time=False, markup=False, rich_tracebacks=False
    )
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.addFilter(redactor)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.addFilter(redactor)

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Third-party loggers can include request details at DEBUG; keep them quiet.
    for noisy in ("prawcore", "praw", "urllib3", "requests", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
