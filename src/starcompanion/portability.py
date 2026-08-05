"""Offline, allowlist-only settings and local language-pack portability."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .ini import BOM, LocalizationFile
from .install import normalize_channel, normalize_language
from .ownership import _store_lock
from .user_edits import MAX_ENTRIES, MAX_FILE_BYTES, UserEditStore
from .validate import Severity, validate_value


SETTINGS_SCHEMA = 1
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1_000
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_JSON_DEPTH = 64
PREFERENCE_KEYS = frozenset(
    {
        "default_channel",
        "default_language",
        "profile",
        "merge_mode",
        "theme",
        "ui_schema",
        "last_page",
        "link_live_hotfix",
    }
)


class PortabilityError(ValueError):
    pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bounded_bytes(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise PortabilityError(f"cannot read {label}: {exc}") from exc
    if len(payload) > limit:
        raise PortabilityError(f"{label} exceeds the size limit")
    return payload


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PortabilityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json(payload: bytes, label: str) -> object:
    try:
        text = payload.decode("utf-8")
        depth = 0
        in_string = False
        escaped = False
        for char in text:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
                if depth > MAX_JSON_DEPTH:
                    raise PortabilityError(f"invalid {label} JSON: nesting limit exceeded")
            elif char in "]}":
                depth = max(0, depth - 1)
        data = json.loads(text, object_pairs_hook=_strict_object)
    except PortabilityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PortabilityError(f"invalid {label} JSON: {exc}") from exc
    return data


def validate_preferences(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PortabilityError("preferences must be a JSON object")
    unknown = set(value) - PREFERENCE_KEYS
    if unknown:
        raise PortabilityError(
            f"preferences contain non-portable fields: {', '.join(sorted(unknown))}"
        )
    result = dict(value)
    if "default_channel" in result:
        if not isinstance(result["default_channel"], str):
            raise PortabilityError("default_channel must be text")
        result["default_channel"] = normalize_channel(result["default_channel"])
    if "default_language" in result:
        if not isinstance(result["default_language"], str):
            raise PortabilityError("default_language must be text")
        result["default_language"] = normalize_language(result["default_language"])
    if "profile" in result and (
        not isinstance(result["profile"], str)
        or not result["profile"]
        or len(result["profile"]) > 128
        or any(char in result["profile"] for char in "/\\\r\n\0")
    ):
        raise PortabilityError("profile must be a portable profile name, not a path")
    if result.get("merge_mode") not in (None, "merge", "overwrite"):
        raise PortabilityError("merge_mode must be merge or overwrite")
    if result.get("theme") not in (None, "light", "dark"):
        raise PortabilityError("theme must be light or dark")
    if "ui_schema" in result and (
        type(result["ui_schema"]) is not int or result["ui_schema"] != 1
    ):
        raise PortabilityError("ui_schema must be 1")
    if "last_page" in result and (
        not isinstance(result["last_page"], str)
        or not result["last_page"]
        or len(result["last_page"]) > 64
        or any(
            not (character.isascii() and (character.isalnum() or character in "-_"))
            for character in result["last_page"]
        )
    ):
        raise PortabilityError("last_page must be a portable interface page key")
    if "link_live_hotfix" in result and type(result["link_live_hotfix"]) is not bool:
        raise PortabilityError("link_live_hotfix must be true or false")
    return result


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _is_link_or_junction(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction and junction())


def _checked_descendant(root: Path, path: Path, label: str) -> Path:
    """Reject link-like components and resolved paths outside ``root``."""

    # Callers bind ``root`` during preview. Do not resolve it again here: a
    # later link/junction swap is precisely what this check must detect.
    base = Path(os.path.abspath(root))
    if _is_link_or_junction(base):
        raise PortabilityError(f"{label} cannot use a symbolic-link or junction root")
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise PortabilityError(f"{label} escapes the data root") from exc
    current = base
    for part in relative.parts:
        current /= part
        if _is_link_or_junction(current):
            raise PortabilityError(f"{label} cannot traverse a symbolic link or junction")
    resolved = candidate.resolve(strict=False)
    if resolved != base and base not in resolved.parents:
        raise PortabilityError(f"{label} escapes the data root")
    return candidate


def _atomic_bytes(path: Path, payload: bytes, *, root: Path | None = None) -> None:
    if root is not None:
        path = _checked_descendant(root, path, "settings write target")
    path.parent.mkdir(parents=True, exist_ok=True)
    if root is not None:
        _checked_descendant(root, path, "settings write target")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if root is not None:
            _checked_descendant(root, path, "settings write target")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass(frozen=True)
class PreferencesStore:
    root: Path

    @property
    def path(self) -> Path:
        return Path(self.root).resolve() / "preferences.json"

    def load(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        if self.path.is_symlink():
            raise PortabilityError("preferences cannot be a symbolic link")
        if self.path.stat().st_size > MAX_ENTRY_BYTES:
            raise PortabilityError("preferences exceed the size limit")
        return validate_preferences(
            _json(_bounded_bytes(self.path, MAX_ENTRY_BYTES, "preferences"), "preferences")
        )

    def save(self, preferences: Mapping[str, object]) -> None:
        _atomic_bytes(self.path, _canonical_json(validate_preferences(dict(preferences))))


@dataclass(frozen=True)
class LanguagePackStore:
    channel: str
    language: str
    root: Path

    @property
    def path(self) -> Path:
        return UserEditStore(self.channel, self.language, root=self.root).path.with_name(
            "language-pack.ini"
        )

    def load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        return _parse_ini(
            _bounded_bytes(self.path, MAX_FILE_BYTES, str(self.path)), str(self.path)
        )

    def save(self, values: Mapping[str, str]) -> None:
        materialized = _validate_ini_values(values, "language pack")
        body = "\n".join(f"{key}={materialized[key]}" for key in sorted(materialized))
        payload = (BOM + body + ("\n" if body else "")).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise PortabilityError("language pack exceeds the size limit")
        _atomic_bytes(self.path, payload)


def _validate_ini_values(values: Mapping[str, str], label: str) -> dict[str, str]:
    result = dict(values)
    if len(result) > MAX_ENTRIES:
        raise PortabilityError(f"{label} exceeds the entry limit")
    for key, value in result.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
            or key.strip() != key
            or any(char in key for char in "=\r\n\0")
            or not isinstance(value, str)
            or len(value) > 1024 * 1024
        ):
            raise PortabilityError(f"{label} contains an invalid localization entry")
        errors = [
            issue
            for issue in validate_value(value)
            if issue.severity is Severity.ERROR and issue.code != "unknown-tag"
        ]
        if errors:
            raise PortabilityError(f"{label} value for {key!r} is invalid: {errors[0]}")
    return result


def _parse_ini(payload: bytes, label: str) -> dict[str, str]:
    if len(payload) > MAX_FILE_BYTES:
        raise PortabilityError(f"{label} exceeds the size limit")
    try:
        text = payload.decode("utf-8-sig")
        if any(line and "=" not in line for line in text.splitlines()):
            raise PortabilityError(f"invalid {label}: every non-empty line must contain =")
        parsed = LocalizationFile.loads(text)
    except PortabilityError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise PortabilityError(f"invalid {label}: {exc}") from exc
    values: dict[str, str] = {}
    for entry in parsed.entries():
        if entry.key in values:
            raise PortabilityError(f"duplicate localization key {entry.key!r} in {label}")
        values[entry.key] = entry.value
    return _validate_ini_values(values, label)


def load_language_pack(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.is_file():
        raise PortabilityError(f"language pack does not exist: {path}")
    return _parse_ini(_bounded_bytes(path, MAX_FILE_BYTES, str(path)), str(path))


@dataclass(frozen=True)
class ExportEntry:
    archive_path: str
    source: Path | None
    payload: bytes
    kind: str
    channel: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class SettingsExportPlan:
    root: Path
    entries: tuple[ExportEntry, ...]


def plan_settings_export(root: Path) -> SettingsExportPlan:
    base = Path(root).resolve()
    preferences = PreferencesStore(base).load()
    entries = [
        ExportEntry("preferences.json", None, _canonical_json(preferences), "preferences")
    ]
    channels = base / "channels"
    if channels.is_dir():
        for path in sorted(channels.glob("*/*/*")):
            if not path.is_file() or path.is_symlink() or path.name not in {
                "user.ini",
                "language-pack.ini",
            }:
                continue
            try:
                relative = path.relative_to(channels)
                channel = normalize_channel(relative.parts[0])
                language = normalize_language(relative.parts[1])
            except (ValueError, IndexError):
                continue
            if base not in path.resolve().parents:
                raise PortabilityError("settings source escapes the data root")
            payload = _bounded_bytes(path, MAX_ENTRY_BYTES, str(path))
            _parse_ini(payload, str(path))
            entries.append(
                ExportEntry(
                    f"channels/{channel}/{language}/{path.name}",
                    path,
                    payload,
                    "user-overrides" if path.name == "user.ini" else "language-pack",
                    channel,
                    language,
                )
            )
    if len(entries) + 1 > MAX_ARCHIVE_ENTRIES:
        raise PortabilityError("settings export exceeds the entry limit")
    if sum(len(entry.payload) for entry in entries) > MAX_ARCHIVE_BYTES:
        raise PortabilityError("settings export exceeds the uncompressed size limit")
    return SettingsExportPlan(base, tuple(entries))


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def write_settings_archive(
    plan: SettingsExportPlan, destination: Path, *, overwrite: bool = False
) -> None:
    destination = Path(destination)
    if destination.exists() and not overwrite:
        raise PortabilityError(f"refusing to overwrite {destination}")
    for entry in plan.entries:
        if entry.source is not None and (
            not entry.source.is_file()
            or _sha256(_bounded_bytes(entry.source, MAX_ENTRY_BYTES, str(entry.source)))
            != _sha256(entry.payload)
        ):
            raise PortabilityError(f"settings changed after preview: {entry.archive_path}")
    files = [
        {
            "path": entry.archive_path,
            "kind": entry.kind,
            "channel": entry.channel,
            "language": entry.language,
            "size": len(entry.payload),
            "sha256": _sha256(entry.payload),
        }
        for entry in plan.entries
    ]
    manifest = _canonical_json(
        {"schema": SETTINGS_SCHEMA, "application": "StarCompanion", "files": files}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temp = Path(temporary)
    try:
        with zipfile.ZipFile(temp, "w", allowZip64=False) as archive:
            archive.writestr(_zip_info("manifest.json"), manifest)
            for entry in plan.entries:
                archive.writestr(_zip_info(entry.archive_path), entry.payload)
        if temp.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PortabilityError("settings archive exceeds the file size limit")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


@dataclass(frozen=True)
class ImportItem:
    archive_path: str
    target: Path
    payload: bytes
    kind: str
    channel: str | None
    language: str | None
    outcome: str
    expected_sha256: str | None


@dataclass(frozen=True)
class SettingsImportPlan:
    archive: Path
    archive_sha256: str
    root: Path
    items: tuple[ImportItem, ...]

    @property
    def changes(self) -> tuple[ImportItem, ...]:
        return tuple(item for item in self.items if item.outcome != "unchanged")


def _safe_archive_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(ord(char) < 32 for char in name)
    ):
        raise PortabilityError(f"unsafe settings archive path {name!r}")


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    _safe_archive_name(name)
    if info.flag_bits & 0x1:
        raise PortabilityError("encrypted settings archives are not supported")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise PortabilityError(f"unsupported settings compression for {name!r}")
    if info.file_size > MAX_ENTRY_BYTES:
        raise PortabilityError(f"settings entry {name!r} exceeds its size limit")
    if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
        raise PortabilityError(f"settings entry {name!r} exceeds compression-ratio limit")


def _target_for(base: Path, archive_path: str, kind: str) -> tuple[Path, str | None, str | None]:
    if archive_path == "preferences.json" and kind == "preferences":
        return _checked_descendant(
            base, base / "preferences.json", "settings restore target"
        ), None, None
    parts = PurePosixPath(archive_path).parts
    if len(parts) != 4 or parts[0] != "channels":
        raise PortabilityError(f"manifest path {archive_path!r} is not allowlisted")
    channel = normalize_channel(parts[1])
    language = normalize_language(parts[2])
    expected_name = {
        "user-overrides": "user.ini",
        "language-pack": "language-pack.ini",
    }.get(kind)
    if expected_name is None or parts[3] != expected_name:
        raise PortabilityError(f"manifest kind/path mismatch for {archive_path!r}")
    target = UserEditStore(channel, language, root=base).path.with_name(expected_name)
    return _checked_descendant(
        base, target, "settings restore target"
    ), channel, language


def plan_settings_import(archive_path: Path, root: Path) -> SettingsImportPlan:
    archive_path = Path(archive_path)
    base = Path(root).resolve()
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PortabilityError("settings archive is missing or exceeds the size limit")
    raw_archive = _bounded_bytes(archive_path, MAX_ARCHIVE_BYTES, "settings archive")
    try:
        with zipfile.ZipFile(io.BytesIO(raw_archive), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise PortabilityError("settings archive exceeds the entry limit")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PortabilityError("settings archive contains duplicate paths")
            for info in infos:
                _safe_member(info)
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise PortabilityError("settings archive exceeds the uncompressed size limit")
            if "manifest.json" not in names:
                raise PortabilityError("settings archive has no manifest.json")
            manifest = _json(archive.read("manifest.json"), "settings manifest")
            if not isinstance(manifest, dict) or set(manifest) != {
                "schema", "application", "files"
            }:
                raise PortabilityError("settings manifest has an invalid shape")
            if (
                manifest["schema"] != SETTINGS_SCHEMA
                or manifest["application"] != "StarCompanion"
                or not isinstance(manifest["files"], list)
            ):
                raise PortabilityError("unsupported settings manifest")
            declared: set[str] = set()
            targets: set[Path] = set()
            items: list[ImportItem] = []
            for record in manifest["files"]:
                if not isinstance(record, dict) or set(record) != {
                    "path", "kind", "channel", "language", "size", "sha256"
                }:
                    raise PortabilityError("settings manifest contains an invalid file record")
                name = record["path"]
                kind = record["kind"]
                if not isinstance(name, str) or not isinstance(kind, str) or name in declared:
                    raise PortabilityError("settings manifest contains duplicate/invalid paths")
                _safe_archive_name(name)
                declared.add(name)
                if name not in names or name == "manifest.json":
                    raise PortabilityError(f"manifest entry {name!r} is missing")
                payload = archive.read(name)
                if (
                    type(record["size"]) is not int
                    or record["size"] != len(payload)
                    or not isinstance(record["sha256"], str)
                    or record["sha256"] != _sha256(payload)
                ):
                    raise PortabilityError(f"settings entry {name!r} failed manifest verification")
                target, channel, language = _target_for(base, name, kind)
                normalized_target = _checked_descendant(
                    base, target, "settings restore target"
                ).resolve(strict=False)
                if normalized_target in targets:
                    raise PortabilityError("settings manifest maps multiple files to one target")
                targets.add(normalized_target)
                if record["channel"] != channel or record["language"] != language:
                    raise PortabilityError(f"settings scope mismatch for {name!r}")
                if kind == "preferences":
                    validate_preferences(_json(payload, "preferences"))
                else:
                    _parse_ini(payload, name)
                current = (
                    _bounded_bytes(target, MAX_ENTRY_BYTES, str(target))
                    if target.is_file()
                    else None
                )
                expected = _sha256(current) if current is not None else None
                outcome = (
                    "add" if current is None else "unchanged" if current == payload else "change"
                )
                items.append(
                    ImportItem(name, target, payload, kind, channel, language, outcome, expected)
                )
            if sum(item.kind == "preferences" for item in items) != 1:
                raise PortabilityError(
                    "settings manifest must declare exactly one preferences.json"
                )
            if set(names) != {"manifest.json", *declared}:
                raise PortabilityError("settings archive contains undeclared files")
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise PortabilityError(f"invalid settings archive: {exc}") from exc
    return SettingsImportPlan(
        archive_path,
        _sha256(raw_archive),
        base,
        tuple(sorted(items, key=lambda item: item.archive_path)),
    )


def apply_settings_import(plan: SettingsImportPlan, *, replace_existing: bool = False) -> None:
    if (
        _sha256(_bounded_bytes(plan.archive, MAX_ARCHIVE_BYTES, "settings archive"))
        != plan.archive_sha256
    ):
        raise PortabilityError("settings archive changed after preview")
    changed = [item for item in plan.items if item.outcome != "unchanged"]
    conflicts = [item for item in changed if item.outcome == "change"]
    if conflicts and not replace_existing:
        raise PortabilityError(
            "settings restore would replace existing files; authorize replacement"
        )
    lock = plan.root / ".settings-restore.lock"
    _checked_descendant(plan.root, lock, "settings restore lock")
    with _store_lock(lock):
        journal_path = plan.root / ".settings-restore-journal.json"
        recovery_root = plan.root / ".settings-restore-recovery"
        if journal_path.is_file():
            raise PortabilityError(
                "an interrupted settings restore requires `settings recover` before retrying"
            )
        if recovery_root.exists():
            _remove_recovery_tree(plan.root)
        for item in plan.items:
            _checked_descendant(plan.root, item.target, "settings restore target")
            current = (
                _bounded_bytes(item.target, MAX_ENTRY_BYTES, str(item.target))
                if item.target.is_file()
                else None
            )
            actual = _sha256(current) if current is not None else None
            if actual != item.expected_sha256:
                raise PortabilityError(f"settings changed after preview: {item.archive_path}")
        records = []
        try:
            for item in changed:
                _checked_descendant(plan.root, item.target, "settings restore target")
                before = (
                    _bounded_bytes(item.target, MAX_ENTRY_BYTES, str(item.target))
                    if item.target.is_file()
                    else None
                )
                if before is not None:
                    _atomic_bytes(
                        recovery_root / item.archive_path, before, root=plan.root
                    )
                records.append(
                    {
                        "archive_path": item.archive_path,
                        "kind": item.kind,
                        "had_before": before is not None,
                    }
                )
            _write_restore_journal(plan.root, "applying", records)
            for item in changed:
                _atomic_bytes(item.target, item.payload, root=plan.root)
        except BaseException:
            if journal_path.is_file():
                _recover_settings_locked(plan.root)
            elif recovery_root.exists():
                _remove_recovery_tree(plan.root)
            raise
        _write_restore_journal(plan.root, "complete", records)
        _finish_restore_cleanup(plan.root)


def _write_restore_journal(root: Path, stage: str, records: list[dict[str, object]]) -> None:
    _atomic_bytes(
        root / ".settings-restore-journal.json",
        _canonical_json({"schema": SETTINGS_SCHEMA, "stage": stage, "items": records}),
        root=root,
    )


def _read_restore_journal(root: Path) -> dict[str, object]:
    path = _checked_descendant(
        root, root / ".settings-restore-journal.json", "settings recovery journal"
    )
    if not path.is_file() or path.stat().st_size > MAX_ENTRY_BYTES:
        raise PortabilityError("settings recovery journal is missing or too large")
    data = _json(
        _bounded_bytes(path, MAX_ENTRY_BYTES, "settings recovery journal"),
        "settings recovery journal",
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "stage", "items"}
        or data["schema"] != SETTINGS_SCHEMA
        or data["stage"] not in {"applying", "complete"}
        or not isinstance(data["items"], list)
    ):
        raise PortabilityError("settings recovery journal has an invalid shape")
    for record in data["items"]:
        if (
            not isinstance(record, dict)
            or set(record) != {"archive_path", "kind", "had_before"}
            or not isinstance(record["archive_path"], str)
            or not isinstance(record["kind"], str)
            or type(record["had_before"]) is not bool
        ):
            raise PortabilityError("settings recovery journal has an invalid item")
        _safe_archive_name(record["archive_path"])
        _target_for(root, record["archive_path"], record["kind"])
    return data


def settings_recovery_status(root: Path) -> str | None:
    root = Path(root).resolve()
    journal = root / ".settings-restore-journal.json"
    if not journal.is_file():
        return None
    return str(_read_restore_journal(root)["stage"])


def _remove_recovery_tree(root: Path) -> None:
    recovery_root = root / ".settings-restore-recovery"
    if recovery_root.exists():
        _checked_descendant(root, recovery_root, "settings recovery directory")
        shutil.rmtree(recovery_root)


def _finish_restore_cleanup(root: Path) -> None:
    _remove_recovery_tree(root)
    _checked_descendant(
        root, root / ".settings-restore-journal.json", "settings recovery journal"
    )
    (root / ".settings-restore-journal.json").unlink(missing_ok=True)


def _recover_settings_locked(root: Path) -> str:
    journal = _read_restore_journal(root)
    if journal["stage"] == "applying":
        recovery_root = root / ".settings-restore-recovery"
        for record in reversed(journal["items"]):
            target, _channel, _language = _target_for(
                root, record["archive_path"], record["kind"]
            )
            if record["had_before"]:
                backup = _checked_descendant(
                    root,
                    recovery_root / record["archive_path"],
                    "settings recovery backup",
                )
                if not backup.is_file():
                    raise PortabilityError(
                        f"settings recovery backup is missing: {record['archive_path']}"
                    )
                _atomic_bytes(
                    target,
                    _bounded_bytes(backup, MAX_ENTRY_BYTES, "settings recovery backup"),
                    root=root,
                )
            else:
                _checked_descendant(root, target, "settings recovery target")
                target.unlink(missing_ok=True)
        _write_restore_journal(root, "complete", journal["items"])
        result = "rolled-back"
    else:
        result = "completed-cleanup"
    _finish_restore_cleanup(root)
    return result


def recover_settings_restore(root: Path) -> str:
    root = Path(root).resolve()
    with _store_lock(root / ".settings-restore.lock"):
        return _recover_settings_locked(root)


__all__ = [
    "LanguagePackStore",
    "PortabilityError",
    "PreferencesStore",
    "SettingsExportPlan",
    "SettingsImportPlan",
    "apply_settings_import",
    "load_language_pack",
    "plan_settings_export",
    "plan_settings_import",
    "recover_settings_restore",
    "settings_recovery_status",
    "validate_preferences",
    "write_settings_archive",
]
