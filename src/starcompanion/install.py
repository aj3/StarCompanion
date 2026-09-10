"""Find Star Citizen installations.

Most people do not know where their game lives, and asking them to type a path
to `global.ini` is the single biggest obstacle to using this tool. So look for
it instead: check the usual launcher locations, then every fixed drive.

Read-only. Nothing here writes, and directories that cannot be read are skipped
rather than raising.
"""

from __future__ import annotations

import json
import re
import stat
import string
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Release channels the launcher creates, most commonly used first.
CHANNELS = ("LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX")

# Where the RSI launcher installs by default, relative to a drive root.
_LAUNCHER_PATHS = (
    "Program Files/Roberts Space Industries/StarCitizen",
    "Roberts Space Industries/StarCitizen",
    "Games/Roberts Space Industries/StarCitizen",
    "RSI/StarCitizen",
    "StarCitizen",
)

ARCHIVE_NAME = "Data.p4k"
DEFAULT_LANGUAGE = "english"
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._()\-]{0,63}\Z")


def normalize_channel(value: str) -> str:
    channel = value.strip().upper()
    if channel not in CHANNELS:
        raise ValueError(
            f"unsupported game channel {value!r}; expected one of {', '.join(CHANNELS)}"
        )
    return channel


def normalize_language(value: str) -> str:
    language = value.strip().casefold()
    if not _SCOPE.fullmatch(language):
        raise ValueError(
            f"invalid localization language {value!r}; use letters, numbers, dot, "
            "dash, underscore, or parentheses"
        )
    return language


@dataclass(frozen=True)
class GameInstall:
    root: Path
    """The channel folder, e.g. ...\\StarCitizen\\LIVE."""
    channel: str
    version: str | None = None
    archive_mtime_ns: int = 0
    archive_size: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        if not self.archive_mtime_ns or not self.archive_size:
            try:
                metadata = self.archive.stat()
            except OSError:
                return
            if not self.archive_mtime_ns:
                object.__setattr__(self, "archive_mtime_ns", metadata.st_mtime_ns)
            if not self.archive_size:
                object.__setattr__(self, "archive_size", metadata.st_size)

    @property
    def archive(self) -> Path:
        return self.root / ARCHIVE_NAME

    def localization(self, language: str = DEFAULT_LANGUAGE) -> Path:
        """Where an override goes. Lowercase `data` matches what CIG ships."""
        return self.root / "data" / "Localization" / normalize_language(language) / "global.ini"

    def languages(self) -> tuple[str, ...]:
        """Installed localization languages discovered read-only from Data.p4k."""
        from .extract.p4k import P4KArchive, is_localization_entry

        with P4KArchive(self.archive, entry_filter=is_localization_entry) as archive:
            return tuple(archive.languages())

    @property
    def has_override(self) -> bool:
        return self.localization().is_file()

    @property
    def user_cfg(self) -> Path:
        return self.root / "USER.cfg"

    @property
    def language_configured(self) -> bool:
        """USER.cfg must set g_language or the override is ignored."""
        try:
            text = self.user_cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return self.configured_language is not None

    @property
    def configured_language(self) -> str | None:
        """Return the last valid ``g_language`` assignment in USER.cfg."""
        try:
            text = self.user_cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        selected = None
        for line in text.splitlines():
            match = re.match(r"^\s*g_language\s*=\s*([^\s;#]+)", line, re.IGNORECASE)
            if match:
                try:
                    selected = normalize_language(match.group(1))
                except ValueError:
                    continue
        return selected

    @property
    def label(self) -> str:
        version = f" ({self.version})" if self.version else ""
        return f"{self.channel}{version} — {self.root}"

    @property
    def freshness_evidence(self) -> str:
        """Explain deterministic selection without exposing file contents."""

        if not self.archive_mtime_ns:
            return f"Data.p4k timestamp unavailable; {self.archive_size:,} bytes"
        updated = datetime.fromtimestamp(
            self.archive_mtime_ns / 1_000_000_000,
            tz=timezone.utc,
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        return f"Data.p4k updated {updated}; {self.archive_size:,} bytes"

    def __str__(self) -> str:
        return self.label


def find_installs(
    roots: list[Path] | None = None,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> list[GameInstall]:
    """Every install found, LIVE first. Empty list if none.

    `roots` **replaces** the automatic drive scan rather than adding to it, so
    a caller can search a known location only -- which is also what makes this
    testable without picking up the developer's real install.
    """
    found: dict[Path, GameInstall] = {}

    for base in _candidate_bases(roots):
        if checkpoint is not None:
            checkpoint()
        direct = identify(base)
        if direct is not None and direct.root.resolve() == Path(base).resolve():
            found[direct.root.resolve()] = direct
        for channel in CHANNELS:
            if checkpoint is not None:
                checkpoint()
            root = base / channel
            install = _describe_install(root, channel)
            if install is None:
                continue
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved not in found:
                found[resolved] = install

    return sorted(found.values(), key=install_rank)


def find_default() -> GameInstall | None:
    """The one to preselect: LIVE if present, else the first found."""
    installs = find_installs()
    return installs[0] if installs else None


def install_rank(install: GameInstall) -> tuple[int, int, int, str]:
    """Prefer the normal channel order, then the freshest usable archive."""

    return (
        CHANNELS.index(install.channel),
        -install.archive_mtime_ns,
        -install.archive_size,
        str(install.root).casefold(),
    )


def selection_evidence(
    selected: GameInstall,
    candidates: Iterable[GameInstall],
) -> str:
    """Explain whether the selected install is the automatic channel winner."""

    peers = sorted(
        (item for item in candidates if item.channel == selected.channel),
        key=install_rank,
    )
    selected_key = str(selected.root).casefold()
    winner = next((item for item in peers if item.archive_size > 0), None)
    if winner is None:
        return f"Selected {selected.channel}; {selected.freshness_evidence}."
    if str(winner.root).casefold() != selected_key:
        return (
            f"Selected explicitly instead of the automatic freshest "
            f"{selected.channel} candidate; {selected.freshness_evidence}."
        )
    if len(peers) == 1:
        return f"Only discovered {selected.channel} candidate; {selected.freshness_evidence}."
    return (
        f"Automatically preferred as freshest of {len(peers):,} "
        f"{selected.channel} candidates by Data.p4k modified time; size and "
        f"folder path break ties. {selected.freshness_evidence}."
    )


def identify(path: Path) -> GameInstall | None:
    """Describe a folder the user picked, if it looks like an install.

    Accepts either the channel folder itself or something inside it, so a
    person who browses to `.../LIVE/data` still gets the right answer.
    """
    path = Path(path)
    candidates = [path, *path.parents] if path.exists() else list(path.parents)

    for candidate in candidates:
        if not (candidate / ARCHIVE_NAME).is_file():
            continue
        try:
            channel = normalize_channel(candidate.name)
        except ValueError:
            return None
        return _describe_install(candidate, channel)
    return None


def _describe_install(root: Path, channel: str) -> GameInstall | None:
    """Return one readable, non-empty regular archive candidate."""

    archive = Path(root) / ARCHIVE_NAME
    try:
        metadata = archive.stat(follow_symlinks=False)
    except OSError:
        return None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
    ):
        return None
    return GameInstall(
        root=Path(root),
        channel=channel,
        version=_read_version(Path(root)),
        archive_mtime_ns=metadata.st_mtime_ns,
        archive_size=metadata.st_size,
    )


def _candidate_bases(roots: list[Path] | None) -> list[Path]:
    if roots is not None:
        return [Path(root) for root in roots]

    return [
        drive / relative
        for drive in _drives()
        for relative in _LAUNCHER_PATHS
    ]


def _drives() -> list[Path]:
    """Fixed drives on Windows; root elsewhere."""
    drives = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:/")
        try:
            if drive.is_dir():
                drives.append(drive)
        except OSError:
            continue

    return drives or [Path("/")]


def _read_version(root: Path) -> str | None:
    """Build version from the launcher's manifest, if readable."""
    manifest = root / "build_manifest.id"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None

    section = data.get("Data") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return None

    branch = section.get("Branch")
    version = section.get("Version")
    return version or branch or None
