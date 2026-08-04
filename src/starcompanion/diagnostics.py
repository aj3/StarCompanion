"""Privacy-preserving diagnostics suitable for sharing with support."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .install import GameInstall
from .extract.p4k import P4KError
from .portability import LanguagePackStore, PreferencesStore
from .store import archive_fingerprint
from .user_edits import UserEditStore, data_dir, load_ini


DIAGNOSTICS_SCHEMA = 1


class DiagnosticsError(ValueError):
    pass


def _safe_version(value: str | None) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return "unknown" if not value else "redacted"


def _file_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"present": False, "bytes": 0, "entries": 0, "status": "absent"}
    try:
        entries = len(load_ini(path))
        return {
            "present": True,
            "bytes": path.stat().st_size,
            "entries": entries,
            "status": "valid",
        }
    except (OSError, ValueError):
        return {
            "present": True,
            "bytes": 0,
            "entries": 0,
            "status": "invalid",
        }


def _scope_summaries(root: Path) -> list[dict[str, object]]:
    scopes: list[dict[str, object]] = []
    channels = root / "channels"
    if not channels.is_dir():
        return scopes
    for user_path in sorted(channels.glob("*/*/user.ini")):
        try:
            relative = user_path.relative_to(channels)
            store = UserEditStore(relative.parts[0], relative.parts[1], root=root)
            pack = LanguagePackStore(relative.parts[0], relative.parts[1], root)
        except (ValueError, IndexError):
            continue
        scopes.append(
            {
                "channel": store.channel.upper(),
                "language": store.language.casefold(),
                "user_overrides": _file_summary(store.path),
                "language_pack": _file_summary(pack.path),
            }
        )
    for pack_path in sorted(channels.glob("*/*/language-pack.ini")):
        if any(
            item["channel"] == pack_path.parents[1].name.upper()
            and item["language"] == pack_path.parent.name.casefold()
            for item in scopes
        ):
            continue
        try:
            pack = LanguagePackStore(
                pack_path.parents[1].name, pack_path.parent.name, root
            )
        except ValueError:
            continue
        scopes.append(
            {
                "channel": pack.channel.upper(),
                "language": pack.language.casefold(),
                "user_overrides": _file_summary(
                    UserEditStore(pack.channel, pack.language, root=root).path
                ),
                "language_pack": _file_summary(pack.path),
            }
        )
    return sorted(scopes, key=lambda item: (item["channel"], item["language"]))


def build_diagnostics(
    installs: Iterable[GameInstall] = (), *, root: Path | None = None
) -> dict[str, object]:
    base = Path(root or data_dir()).resolve()
    games: list[dict[str, object]] = []
    for game in installs:
        try:
            languages = list(game.languages())
            archive_status = "readable"
        except (OSError, ValueError, P4KError):
            languages = []
            archive_status = "unreadable"
        try:
            archive_bytes = game.archive.stat().st_size
        except OSError:
            archive_bytes = 0
        games.append(
            {
                "channel": game.channel,
                "version": _safe_version(game.version),
                "archive_status": archive_status,
                "archive_bytes": archive_bytes,
                "archive_fingerprint": archive_fingerprint(game),
                "languages": languages,
                "configured_language": game.configured_language,
                "override_languages": [
                    language for language in languages if game.localization(language).is_file()
                ],
            }
        )
    try:
        preference_names = sorted(PreferencesStore(base).load())
        preferences_status = "valid"
    except (OSError, ValueError):
        preference_names = []
        preferences_status = "invalid"
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "application": {"name": "StarCompanion", "version": "0.1.0"},
        "runtime": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "installs": sorted(games, key=lambda item: item["channel"]),
        "portable_data": {
            "preferences_status": preferences_status,
            "preference_names": preference_names,
            "scopes": _scope_summaries(base),
        },
        "privacy": {
            "absolute_paths": "redacted",
            "usernames": "redacted",
            "log_content": "excluded",
            "ownership": "excluded",
            "game_strings": "excluded",
            "user_values": "excluded",
        },
        "network": {
            "runtime_mode": "offline",
            "automatic_updates": False,
            "automatic_telemetry": False,
        },
    }


def render_diagnostics(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_diagnostics(
    report: dict[str, object], destination: Path, *, overwrite: bool = False
) -> None:
    destination = Path(destination)
    if destination.exists() and not overwrite:
        raise DiagnosticsError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(render_diagnostics(report))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


__all__ = ["DiagnosticsError", "build_diagnostics", "render_diagnostics", "write_diagnostics"]
