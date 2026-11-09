import io
import logging
import sys

import pytest
from rich.console import Console

from reddit_wiper import config
from reddit_wiper.errors import ConfigError
from reddit_wiper.logging_utils import redact, register_secret, setup_logging


def test_dirs_follow_overrides(isolated_dirs):
    assert config.config_dir() == isolated_dirs / "config"
    assert config.backups_dir() == isolated_dirs / "data" / "backups"


def test_save_and_load_roundtrip():
    creds = config.Credentials(
        client_id="cid",
        client_secret="csecret",
        username="bob",
        password="pw",
        user_agent="ua",
        two_factor=True,
    )
    path = config.save_credentials(creds)
    loaded = config.load_credentials()
    assert loaded.client_id == "cid" and loaded.client_secret == "csecret"
    assert loaded.username == "bob" and loaded.two_factor
    assert loaded.password == ""  # not stored unless asked
    assert "pw" not in path.read_text()
    if sys.platform != "win32":
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_store_password_opt_in():
    creds = config.Credentials(client_id="c", client_secret="s", username="u", password="pw")
    config.save_credentials(creds, store_password=True)
    assert config.load_credentials().password == "pw"


def test_env_overrides_file(monkeypatch):
    config.save_credentials(config.Credentials(client_id="file", client_secret="s", username="u"))
    monkeypatch.setenv("REDDIT_CLIENT_ID", "env")
    assert config.load_credentials().client_id == "env"


def test_parse_env_file_quotes_and_comments(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# c\nA=\"quoted value\"\nB='x'\n\nbroken line\nC=1=2\n")
    assert config.parse_env_file(p) == {"A": "quoted value", "B": "x", "C": "1=2"}


def test_require_api():
    with pytest.raises(ConfigError):
        config.Credentials(client_id="x").require_api()
    config.Credentials(client_id="x", client_secret="y", username="z").require_api()


def test_credentials_repr_hides_secrets():
    text = repr(config.Credentials(client_secret="topsecret", password="hunter22"))
    assert "topsecret" not in text and "hunter22" not in text


def test_redaction_in_log_file(tmp_path):
    register_secret("SuperSecret99")
    log_file = setup_logging(Console(file=io.StringIO()), log_file=tmp_path / "t.log")
    logging.getLogger("reddit_wiper.test").info("password is %s", "SuperSecret99")
    logging.getLogger("reddit_wiper.test").info("Authorization: bearer abc.def-123")
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "SuperSecret99" not in content and "abc.def-123" not in content
    assert content.count("***") == 2


def test_redact_patterns():
    assert redact("client_secret=abcd1234") == "client_secret=***"
    assert redact('"access_token": "tok"') == '"access_token": "***"'
