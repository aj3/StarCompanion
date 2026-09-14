"""Bounded, redacted, in-memory application events."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

MAX_EVENTS = 500
MAX_DETAIL_INPUT = 4096
MAX_DETAIL = 240
MAX_EXPORT_BYTES = 256 * 1024
LEVELS = ("info", "warning", "error")
_EVENT_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\r\n\t|]+")
_UNIX_PATH = re.compile(r"(?<![\w.])/(?:home|Users|tmp|var|opt)/[^\r\n\t|]+")
_TOKEN = re.compile(r"\b[A-Fa-f0-9]{32,}\b")


@dataclass(frozen=True)
class AppEvent:
    sequence: int
    timestamp: str
    level: str
    event: str
    detail: str


def redact_event_detail(value: object) -> str:
    """Remove common identifiers before an event enters memory."""

    text = str(value)[:MAX_DETAIL_INPUT]
    text = " ".join(text.replace("\x00", " ").split())
    candidates = {
        os.environ.get("USERNAME", ""),
        os.environ.get("USER", ""),
        str(Path.home()),
        os.environ.get("USERPROFILE", ""),
    }
    for candidate in sorted((item for item in candidates if len(item) >= 3), key=len, reverse=True):
        text = re.sub(re.escape(candidate), "[redacted]", text, flags=re.IGNORECASE)
    text = _EMAIL.sub("[email]", text)
    text = _WINDOWS_PATH.sub("[path]", text)
    text = _UNIX_PATH.sub("[path]", text)
    text = _TOKEN.sub("[token]", text)
    return text[:MAX_DETAIL]


class EventLog(QObject):
    """Keep a fixed-size event ring; raw logs and exception text never enter it."""

    changed = Signal()

    def __init__(self, max_events: int = MAX_EVENTS, parent: QObject | None = None):
        super().__init__(parent)
        if not 1 <= max_events <= MAX_EVENTS:
            raise ValueError(f"event capacity must be between 1 and {MAX_EVENTS}")
        self._events: deque[AppEvent] = deque(maxlen=max_events)
        self._sequence = 0

    def publish(self, level: str, event: str, detail: object = "") -> AppEvent:
        if level not in LEVELS:
            raise ValueError("event level must be info, warning, or error")
        if not _EVENT_KEY.fullmatch(event):
            raise ValueError("event key must be a short lowercase identifier")
        self._sequence += 1
        item = AppEvent(
            self._sequence,
            datetime.now(UTC).isoformat(timespec="seconds"),
            level,
            event,
            redact_event_detail(detail),
        )
        self._events.append(item)
        self.changed.emit()
        return item

    def entries(self, level: str | None = None) -> tuple[AppEvent, ...]:
        if level not in (None, *LEVELS):
            raise ValueError("unknown event filter")
        return tuple(item for item in self._events if level is None or item.level == level)

    def clear(self) -> None:
        self._events.clear()
        self.changed.emit()

    def export_bytes(self, level: str | None = None) -> bytes:
        payload = {
            "schema_version": 1,
            "privacy": {
                "bounded": True,
                "paths": "redacted",
                "usernames": "redacted",
                "game_strings": "excluded",
                "raw_logs": "excluded",
                "ownership": "excluded",
            },
            "events": [asdict(item) for item in self.entries(level)],
        }
        rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(rendered) > MAX_EXPORT_BYTES:
            raise ValueError("redacted event export exceeds its byte limit")
        return rendered


def write_event_export(destination: Path, payload: bytes) -> None:
    """Atomically write a previously reviewed, bounded export."""

    if len(payload) > MAX_EXPORT_BYTES:
        raise ValueError("redacted event export exceeds its byte limit")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "AppEvent",
    "EventLog",
    "LEVELS",
    "MAX_DETAIL",
    "MAX_EVENTS",
    "redact_event_detail",
    "write_event_export",
]
