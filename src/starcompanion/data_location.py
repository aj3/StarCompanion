"""Validated default, custom, and beside-executable application data roots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .ownership import OwnershipConflictError, _store_lock
from .transactions import FileFingerprint, fingerprint

APP_NAME = "StarCompanion"
BOOTSTRAP_SCHEMA = 1
PORTABLE_MARKER = ".starcompanion-portable"
PORTABLE_MARKER_PAYLOAD = b"StarCompanion portable data v1\n"
MAX_FILES = 5_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024


class DataLocationError(ValueError):
    pass


@dataclass(frozen=True)
class DataLocation:
    root: Path
    mode: str
    warning: str | None = None


@dataclass(frozen=True)
class MigrationEntry:
    relative_path: str
    source: Path
    destination: Path
    size: int
    sha256: str
    outcome: str


@dataclass(frozen=True)
class DataMigrationPlan:
    source_root: Path
    destination_root: Path
    mode: str
    entries: tuple[MigrationEntry, ...]
    activation_path: Path
    activation_before: FileFingerprint
    marker: Path | None = None

    @property
    def changes(self) -> tuple[MigrationEntry, ...]:
        return tuple(item for item in self.entries if item.outcome == "copy")


def _platform_base() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME
    root = os.environ.get("XDG_DATA_HOME")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def default_data_root() -> Path:
    return _platform_base() / "data" if os.name == "nt" else _platform_base()


def bootstrap_path() -> Path:
    return _platform_base() / "data-location.json"


def executable_directory(executable: Path | None = None) -> Path:
    if executable is not None:
        return Path(executable).resolve().parent
    return Path(sys.executable).resolve().parent


def portable_marker_path(executable: Path | None = None) -> Path:
    return executable_directory(executable) / PORTABLE_MARKER


def portable_data_root(executable: Path | None = None) -> Path:
    return executable_directory(executable) / "StarCompanionData"


def _ordinary_file(path: Path, label: str) -> None:
    metadata = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise DataLocationError(f"{label} must be an ordinary local file")


def _path_present(path: Path) -> bool:
    """Detect ordinary files and dangling link/reparse entries."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _read_json(path: Path) -> dict[str, object]:
    _ordinary_file(path, "data-location configuration")
    if path.stat().st_size > 64 * 1024:
        raise DataLocationError("data-location configuration is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataLocationError(f"invalid data-location configuration: {exc}") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "mode", "root"}
        or value.get("schema_version") != BOOTSTRAP_SCHEMA
        or value.get("mode") != "custom"
        or not isinstance(value.get("root"), str)
    ):
        raise DataLocationError("invalid data-location configuration fields")
    return value


def _safe_root(path: Path) -> Path:
    selected = Path(path)
    if not selected.is_absolute():
        raise DataLocationError("data directory must be an absolute path")
    absolute = Path(os.path.abspath(selected))
    if absolute == Path(absolute.anchor):
        raise DataLocationError("a drive or filesystem root cannot be used as the data directory")
    for candidate in (absolute, *absolute.parents):
        if not candidate.exists():
            continue
        junction = getattr(candidate, "is_junction", None)
        if candidate.is_symlink() or bool(junction and junction()):
            raise DataLocationError("data directory cannot traverse a link or junction")
    return absolute


def synchronized_root_warning(path: Path) -> str | None:
    selected = str(Path(path)).casefold()
    synchronized = ("onedrive", "dropbox", "google drive", "icloud")
    if any(name in selected for name in synchronized):
        return (
            "This location appears synchronized. Concurrent cloud replacement can cause "
            "lock or revision conflicts; keep backups and avoid running two copies."
        )
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        root = os.environ.get(name)
        if root:
            try:
                Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
            except ValueError:
                continue
            return (
                "This location is inside OneDrive. Concurrent synchronization can cause "
                "lock or revision conflicts; a local data directory is recommended."
            )
    return None


def resolve_data_location(executable: Path | None = None) -> DataLocation:
    configured = os.environ.get("STARCOMPANION_DATA")
    if configured:
        root = _safe_root(Path(configured))
        return DataLocation(root, "environment", synchronized_root_warning(root))
    marker = portable_marker_path(executable)
    if _path_present(marker):
        try:
            _ordinary_file(marker, "portable-mode marker")
            if marker.read_bytes() != PORTABLE_MARKER_PAYLOAD:
                raise DataLocationError("portable-mode marker has invalid content")
            root = _safe_root(portable_data_root(executable))
            return DataLocation(root, "portable", synchronized_root_warning(root))
        except (OSError, DataLocationError) as exc:
            return DataLocation(default_data_root(), "default", str(exc))
    bootstrap = bootstrap_path()
    if _path_present(bootstrap):
        try:
            value = _read_json(bootstrap)
            root = _safe_root(Path(str(value["root"])))
            return DataLocation(root, "custom", synchronized_root_warning(root))
        except DataLocationError as exc:
            return DataLocation(default_data_root(), "default", str(exc))
    root = default_data_root()
    return DataLocation(root, "default", synchronized_root_warning(root))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_file(relative: Path) -> bool:
    if relative.parts == ("preferences.json",) or relative.parts == ("ui-layout.json",):
        return True
    return (
        len(relative.parts) >= 3
        and relative.parts[0] == "channels"
        and relative.suffix.casefold() in {".ini", ".json"}
    )


def plan_data_migration(
    source_root: Path,
    destination_root: Path,
    *,
    mode: str = "custom",
    executable: Path | None = None,
) -> DataMigrationPlan:
    source = _safe_root(source_root)
    destination = _safe_root(destination_root)
    if source == destination or source in destination.parents or destination in source.parents:
        raise DataLocationError("source and destination data roots must be separate")
    if mode not in {"custom", "portable"}:
        raise DataLocationError("data location mode must be custom or portable")
    marker = portable_marker_path(executable) if mode == "portable" else None
    if mode == "portable" and destination != _safe_root(portable_data_root(executable)):
        raise DataLocationError("portable data must be beside the selected executable")
    activation = marker if marker is not None else bootstrap_path()
    if _path_present(activation):
        _ordinary_file(activation, "data-location activation file")
        if marker is not None and activation.read_bytes() != PORTABLE_MARKER_PAYLOAD:
            raise DataLocationError("portable-mode marker has invalid content")
        if marker is None:
            configured = _read_json(activation)
            configured_root = _safe_root(Path(str(configured["root"])))
            if configured_root not in {source, destination}:
                raise DataLocationError(
                    "existing data-location configuration targets another root"
                )
    activation_before = fingerprint(activation)
    entries = []
    total = 0
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            metadata = path.lstat()
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            attributes = getattr(metadata, "st_file_attributes", 0)
            junction = getattr(path, "is_junction", None)
            redirected = (
                stat.S_ISLNK(metadata.st_mode)
                or bool(attributes & reparse)
                or bool(junction and junction())
            )
            if redirected and path.is_dir():
                raise DataLocationError("migration source contains a link or junction")
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if (
                redirected
                or not stat.S_ISREG(metadata.st_mode)
                or not _portable_file(relative)
            ):
                if _portable_file(relative):
                    raise DataLocationError("migration source contains a link or non-file entry")
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise DataLocationError(f"migration file exceeds the size limit: {relative}")
            total += size
            if len(entries) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise DataLocationError("data migration exceeds its file or byte limit")
            digest = _sha256(path)
            target = destination / relative
            outcome = "copy"
            if _path_present(target):
                _ordinary_file(target, "migration destination")
                if _sha256(target) != digest:
                    raise DataLocationError(f"destination conflict requires manual review: {relative}")
                outcome = "unchanged"
            entries.append(
                MigrationEntry(relative.as_posix(), path, target, size, digest, outcome)
            )
    return DataMigrationPlan(
        source,
        destination,
        mode,
        tuple(entries),
        activation,
        activation_before,
        marker,
    )


def _checked_write_target(root: Path, path: Path) -> Path:
    safe_root = _safe_root(root)
    target = Path(os.path.abspath(path))
    try:
        relative = target.relative_to(safe_root)
    except ValueError as exc:
        raise DataLocationError("migration target escaped its reviewed root") from exc
    current = safe_root
    for part in relative.parts[:-1]:
        current /= part
        if not _path_present(current):
            continue
        metadata = current.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        junction = getattr(current, "is_junction", None)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(attributes & reparse)
            or bool(junction and junction())
            or not current.is_dir()
        ):
            raise DataLocationError("migration target traverses a link or non-directory")
    if _path_present(target):
        _ordinary_file(target, "migration destination")
    return target


def _atomic_bytes(path: Path, payload: bytes, *, root: Path) -> None:
    path = _checked_write_target(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _checked_write_target(root, path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_data_migration(plan: DataMigrationPlan, *, confirmed: bool) -> None:
    """Copy validated state and switch future launches; never delete the source."""

    if not confirmed:
        raise DataLocationError("data migration requires confirmation")
    lock_path = plan.source_root.parent / ".starcompanion-data-migration.lock"
    try:
        with _store_lock(lock_path):
            current = plan_data_migration(
                plan.source_root,
                plan.destination_root,
                mode=plan.mode,
                executable=(
                    plan.marker.parent / "StarCompanion.exe"
                    if plan.marker
                    else None
                ),
            )
            if current != plan:
                raise DataLocationError("data migration inputs changed after preview")
            for item in plan.changes:
                payload = item.source.read_bytes()
                if (
                    len(payload) != item.size
                    or hashlib.sha256(payload).hexdigest() != item.sha256
                ):
                    raise DataLocationError(
                        f"migration source changed: {item.relative_path}"
                    )
                _atomic_bytes(item.destination, payload, root=plan.destination_root)
            if fingerprint(plan.activation_path) != plan.activation_before:
                raise DataLocationError(
                    "data-location activation file changed after preview"
                )
            if plan.mode == "portable":
                assert plan.marker is not None
                if plan.activation_before.sha256 != hashlib.sha256(
                    PORTABLE_MARKER_PAYLOAD
                ).hexdigest():
                    _atomic_bytes(
                        plan.marker,
                        PORTABLE_MARKER_PAYLOAD,
                        root=plan.marker.parent,
                    )
            else:
                payload = (
                    json.dumps(
                        {
                            "schema_version": BOOTSTRAP_SCHEMA,
                            "mode": "custom",
                            "root": str(plan.destination_root),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                bootstrap = bootstrap_path()
                _atomic_bytes(bootstrap, payload, root=bootstrap.parent)
    except OwnershipConflictError as exc:
        raise DataLocationError("another data migration is currently active") from exc


__all__ = [
    "DataLocation",
    "DataLocationError",
    "DataMigrationPlan",
    "MigrationEntry",
    "apply_data_migration",
    "default_data_root",
    "plan_data_migration",
    "portable_data_root",
    "resolve_data_location",
    "synchronized_root_warning",
]
