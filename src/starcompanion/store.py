"""On-disk cache of contracts read from a game install.

Opening `Data.p4k` means reading a central directory of over a million entries:
around 30 seconds on a real install. Doing that on every launch would make the
app feel broken, so the result is cached and keyed by the game's build version
-- which changes on every patch, so a patched game is re-read automatically and
a stale cache can never be used silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import cache
from .install import GameInstall
from .model import ContractSet

APP_NAME = "StarCompanion"


def cache_dir() -> Path:
    """Per-user cache location, honouring the platform's convention."""
    base = os.environ.get("STARCOMPANION_CACHE")
    if base:
        return Path(base)

    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME / "cache"
    else:
        root = os.environ.get("XDG_CACHE_HOME")
        if root:
            return Path(root) / APP_NAME

    return Path.home() / ".cache" / APP_NAME


def cache_path(install: GameInstall) -> Path:
    """One file per channel and build, so a patch invalidates it by construction."""
    stamp = _safe(install.version or "unknown")
    return cache_dir() / f"contracts-{_safe(install.channel)}-{stamp}.json"


def load(install: GameInstall) -> ContractSet | None:
    """The cached contracts for this exact build, or None."""
    path = cache_path(install)
    if not path.is_file():
        return None
    try:
        return cache.load(path)
    except (OSError, ValueError):
        # A damaged or outdated cache is not worth reporting: re-reading the
        # game is always possible.
        return None


def save(install: GameInstall, contracts: ContractSet) -> Path:
    path = cache_path(install)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache.save(contracts, path, source=f"game:{install.channel}:{install.version}")
    return path


def clear() -> int:
    """Remove every cached read. Returns how many files were deleted."""
    directory = cache_dir()
    if not directory.is_dir():
        return 0

    removed = 0
    for path in directory.glob("contracts-*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)
