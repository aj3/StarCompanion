"""On-disk cache of contracts read from a game install.

Opening `Data.p4k` means reading a central directory of over a million entries:
around 30 seconds on a real install. Doing that on every launch would make the
app feel broken, so the result is cached and keyed by the game's build version
-- which changes on every patch, so a patched game is re-read automatically and
a stale cache can never be used silently.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import cache
from .install import GameInstall, normalize_language
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


def cache_path(install: GameInstall, language: str = "english") -> Path | None:
    """A path unique to the install, language, build, and current archive.

    Launcher manifests are not guaranteed to exist. The P4K metadata keeps an
    unversioned install from sharing a permanent ``unknown`` cache, and also
    invalidates a stale manifest after an archive replacement.
    """
    language = normalize_language(language)
    fingerprint = archive_fingerprint(install)
    if fingerprint is None:
        return None
    location = hashlib.sha256(
        os.path.normcase(str(install.root.resolve())).encode("utf-8")
    ).hexdigest()[:12]
    version = _safe(install.version or "no-manifest")
    return cache_dir() / (
        f"contracts-{_safe(install.channel)}-{_safe(language)}-"
        f"{version}-{location}-{fingerprint}.json"
    )


def load(install: GameInstall, language: str = "english") -> ContractSet | None:
    """The cached contracts for this exact build, or None."""
    path = cache_path(install, language)
    if path is None:
        return None
    if not path.is_file():
        return None
    try:
        return cache.load(path)
    except (OSError, ValueError):
        # A damaged or outdated cache is not worth reporting: re-reading the
        # game is always possible.
        return None


def save(
    install: GameInstall,
    contracts: ContractSet,
    language: str = "english",
) -> Path | None:
    language = normalize_language(language)
    path = cache_path(install, language)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    cache.save(
        contracts,
        path,
        source=f"game:{install.channel}:{install.version}:{language}:{path.stem}",
    )
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


def archive_fingerprint(install: GameInstall) -> str | None:
    """Cheap identity for cache invalidation; never reads the multi-GB body."""
    try:
        stat = install.archive.stat()
    except OSError:
        return None
    return f"{stat.st_size:x}-{stat.st_mtime_ns:x}"
