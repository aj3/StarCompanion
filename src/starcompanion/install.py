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
import string
from dataclasses import dataclass
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "channel", normalize_channel(self.channel))

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

    def __str__(self) -> str:
        return self.label


def find_installs(roots: list[Path] | None = None) -> list[GameInstall]:
    """Every install found, LIVE first. Empty list if none.

    `roots` **replaces** the automatic drive scan rather than adding to it, so
    a caller can search a known location only -- which is also what makes this
    testable without picking up the developer's real install.
    """
    found: dict[Path, GameInstall] = {}

    for base in _candidate_bases(roots):
        direct = identify(base)
        if direct is not None and direct.root.resolve() == Path(base).resolve():
            found[direct.root.resolve()] = direct
        for channel in CHANNELS:
            root = base / channel
            if not (root / ARCHIVE_NAME).is_file():
                continue
            resolved = root.resolve()
            if resolved not in found:
                found[resolved] = GameInstall(
                    root=root, channel=channel, version=_read_version(root)
                )

    return sorted(found.values(), key=lambda i: (CHANNELS.index(i.channel), str(i.root)))


def find_default() -> GameInstall | None:
    """The one to preselect: LIVE if present, else the first found."""
    installs = find_installs()
    return installs[0] if installs else None


def identify(path: Path) -> GameInstall | None:
    """Describe a folder the user picked, if it looks like an install.

    Accepts either the channel folder itself or something inside it, so a
    person who browses to `.../LIVE/data` still gets the right answer.
    """
    path = Path(path)
    candidates = [path, *path.parents] if path.exists() else list(path.parents)

    for candidate in candidates:
        if (candidate / ARCHIVE_NAME).is_file():
            try:
                channel = normalize_channel(candidate.name)
            except ValueError:
                return None
            return GameInstall(
                root=candidate,
                channel=channel,
                version=_read_version(candidate),
            )
    return None


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
