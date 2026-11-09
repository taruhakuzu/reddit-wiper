"""Progress file so an interrupted run can resume where it stopped."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
STATE_VERSION = 1


def state_path(directory: Path, username: str) -> Path:
    safe = "".join(ch for ch in username.lower() if ch.isalnum() or ch in "-_") or "unknown"
    return directory / f"{safe}.json"


@dataclass
class State:
    path: Path
    username: str
    done: dict[str, str] = field(default_factory=dict)  # fullname -> "deleted" | "gone"
    edited: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)  # fullname -> last error

    @classmethod
    def load(cls, path: Path, username: str) -> State:
        state = cls(path=path, username=username)
        if not path.is_file():
            return state
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            backup = path.with_suffix(".corrupt.json")
            log.warning("State file is unreadable (%s); moving it to %s", exc, backup.name)
            os.replace(path, backup)
            return state
        state.done = dict(data.get("done", {}))
        state.edited = set(data.get("edited", []))
        state.failed = dict(data.get("failed", {}))
        return state

    def is_done(self, fullname: str) -> bool:
        return fullname in self.done

    def mark_edited(self, fullname: str) -> None:
        self.edited.add(fullname)
        self.save()

    def mark_done(self, fullname: str, how: str = "deleted") -> None:
        self.done[fullname] = how
        self.failed.pop(fullname, None)
        self.save()

    def mark_failed(self, fullname: str, error: str) -> None:
        self.failed[fullname] = error[:300]
        self.save()

    def save(self) -> None:
        """Atomic write: a crash mid-write never leaves a half-written state file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "done": self.done,
            "edited": sorted(self.edited),
            "failed": self.failed,
        }
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.done.clear()
        self.edited.clear()
        self.failed.clear()
        self.path.unlink(missing_ok=True)
