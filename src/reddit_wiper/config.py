"""Locations of config / data files and loading of credentials.

Credentials live in ``<user config dir>/reddit-wiper/credentials.env`` (KEY=VALUE lines) and
can be overridden by environment variables of the same name. Nothing is ever stored inside
the project directory.
"""

from __future__ import annotations

import contextlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from reddit_wiper import APP_NAME, __version__
from reddit_wiper.errors import ConfigError

ENV_KEYS = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
    "REDDIT_USER_AGENT",
    "REDDIT_2FA",
)


def config_dir() -> Path:
    override = os.environ.get("REDDIT_WIPER_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    override = os.environ.get("REDDIT_WIPER_DATA_DIR")
    return Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))


def credentials_file() -> Path:
    return config_dir() / "credentials.env"


def backups_dir() -> Path:
    return data_dir() / "backups"


def state_dir() -> Path:
    return data_dir() / "state"


def scans_dir() -> Path:
    return data_dir() / "scans"


def browser_profile_dir() -> Path:
    """Browser-mode profile. Holds your Reddit login cookies: private to your user account."""
    return data_dir() / "browser-profile"


def logs_dir() -> Path:
    return data_dir() / "logs"


def default_user_agent(username: str) -> str:
    return f"python:{APP_NAME}:v{__version__} (by /u/{username})"


@dataclass
class Credentials:
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    username: str = ""
    password: str = field(default="", repr=False)
    user_agent: str = ""
    two_factor: bool = False

    def require_api(self) -> None:
        missing = [
            name
            for name, value in (
                ("client id", self.client_id),
                ("client secret", self.client_secret),
                ("username", self.username),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing " + ", ".join(missing) + ". Run `reddit-wiper setup` or set the "
                "REDDIT_* environment variables."
            )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_credentials(path: Path | None = None) -> Credentials:
    values = parse_env_file(path or credentials_file())
    for key in ENV_KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    username = values.get("REDDIT_USERNAME", "")
    return Credentials(
        client_id=values.get("REDDIT_CLIENT_ID", ""),
        client_secret=values.get("REDDIT_CLIENT_SECRET", ""),
        username=username,
        password=values.get("REDDIT_PASSWORD", ""),
        user_agent=values.get("REDDIT_USER_AGENT") or default_user_agent(username or "unknown"),
        two_factor=values.get("REDDIT_2FA", "").lower() in {"1", "true", "yes"},
    )


def save_credentials(
    creds: Credentials, path: Path | None = None, store_password: bool = False
) -> Path:
    path = path or credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# reddit-wiper credentials. Keep this file private. Never commit it.",
        f"REDDIT_CLIENT_ID={creds.client_id}",
        f"REDDIT_CLIENT_SECRET={creds.client_secret}",
        f"REDDIT_USERNAME={creds.username}",
        f"REDDIT_USER_AGENT={creds.user_agent}",
        f"REDDIT_2FA={'true' if creds.two_factor else 'false'}",
    ]
    if store_password and creds.password:
        lines.append(f"REDDIT_PASSWORD={creds.password}")
    # Create with owner-only permissions where the OS supports it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with contextlib.suppress(OSError):  # e.g. some Windows filesystems
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path
