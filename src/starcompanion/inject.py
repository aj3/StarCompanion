"""Merge rendered strings into a stock global.ini.

Writes are deliberately awkward to trigger: `plan()` is pure and returns what
would change, and `apply()` refuses to run unless the caller passes
``confirmed=True`` after showing that plan to a human.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat as stat_module
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .ini import LocalizationFile
from .transactions import (
    FileFingerprint,
    TargetChangedError,
    TransactionJournal,
    bytes_sha256,
    fingerprint,
)
from .validate import Issue, Severity, validate_value


# Files that mark a directory as a real Star Citizen install rather than a
# scratch copy. Matched case-insensitively against directory entries.
GAME_MARKERS = ("data.p4k", "bin64", "usergame.cfg")
MAX_OPERATION_PLAN_BYTES = 64 * 1024 * 1024
DEFAULT_BACKUP_RETENTION = 20
MAX_BACKUP_RETENTION = 200
BACKUP_LOCK_TIMEOUT_SECONDS = 10.0


def looks_like_game_install(target: Path) -> Path | None:
    """The install root, if `target` sits inside one.

    Used to demand an extra confirmation before writing somewhere that matters.
    """
    for parent in target.resolve().parents:
        try:
            entries = {child.name.casefold() for child in parent.iterdir()}
        except OSError:
            continue
        if any(marker in entries for marker in GAME_MARKERS):
            return parent
    return None


class MergeMode(Enum):
    MERGE = "merge"
    """Only touch keys we have replacements for; leave everything else alone."""

    OVERWRITE = "overwrite"
    """Rebuild from a pristine stock file, discarding any other pack's edits."""


class UnconfirmedWriteError(RuntimeError):
    pass


class ValidationFailedError(RuntimeError):
    def __init__(self, failures: list[tuple[str, Issue]]):
        super().__init__(f"{len(failures)} value(s) failed validation")
        self.failures = failures


class BackupSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupPruneResult:
    deleted: tuple[Path, ...]
    skipped: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RollbackResult:
    recovery_backup: Path | None
    final_fingerprint: FileFingerprint
    diagnostics: tuple[str, ...] = ()


@dataclass
class InjectionPlan:
    """Versioned operation plan shared by source merge, preview, and apply."""

    schema_version: int = 1
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Keys with no home in the target file -- silently dropped by a naive merge."""
    errors: list[tuple[str, Issue]] = field(default_factory=list)
    warnings: list[tuple[str, Issue]] = field(default_factory=list)
    channel: str | None = None
    language: str | None = None
    mode: str | None = None
    baseline_source: str | None = None
    target: str | None = None
    target_fingerprint: FileFingerprint | None = None
    baseline_sha256: str | None = None
    desired_sha256: str | None = None
    source_precedence: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, object]] = field(default_factory=dict)
    plan_id: str | None = None
    backup: str | None = None
    transaction_status: str | None = None
    diagnostics: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"{len(self.added)} added",
            f"{len(self.updated)} updated",
            f"{len(self.removed)} removed",
            f"{len(self.unchanged)} unchanged",
            f"{len(self.skipped)} skipped",
        ]
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS")
        return ", ".join(parts)

    def bind(
        self,
        *,
        channel: str,
        language: str,
        mode: MergeMode,
        baseline_source: str,
        target: Path,
        target_fingerprint: FileFingerprint,
        baseline_sha256: str,
        desired_sha256: str,
        source_report: dict[str, object] | None = None,
    ) -> InjectionPlan:
        self.channel = channel
        self.language = language
        self.mode = mode.value
        self.baseline_source = baseline_source
        self.target = str(target.resolve())
        self.target_fingerprint = target_fingerprint
        self.baseline_sha256 = baseline_sha256
        self.desired_sha256 = desired_sha256
        if source_report:
            precedence = source_report.get("precedence", [])
            entries = source_report.get("entries", {})
            if not isinstance(precedence, list) or not all(
                isinstance(item, str) for item in precedence
            ):
                raise ValueError("source report precedence must be a string list")
            if not isinstance(entries, dict):
                raise ValueError("source report entries must be an object")
            self.source_precedence = list(precedence)
            relevant = set(self.added + self.updated + self.removed + self.unchanged)
            self.sources = {
                key: _source_summary(entries[key])
                for key in sorted(relevant & set(entries))
            }
        self.plan_id = self.compute_id()
        return self

    def compute_id(self) -> str:
        encoded = json.dumps(
            self.to_dict(include_runtime=False, include_plan_id=False),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(
        self, *, include_runtime: bool = True, include_plan_id: bool = True
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "schema_version": self.schema_version,
            "channel": self.channel,
            "language": self.language,
            "mode": self.mode,
            "baseline_source": self.baseline_source,
            "target": self.target,
            "target_fingerprint": (
                self.target_fingerprint.to_dict() if self.target_fingerprint else None
            ),
            "baseline_sha256": self.baseline_sha256,
            "desired_sha256": self.desired_sha256,
            "outcomes": {
                "add": list(self.added),
                "change": list(self.updated),
                "remove": list(self.removed),
                "unchanged": list(self.unchanged),
                "skipped": list(self.skipped),
            },
            "validation": {
                "errors": [_issue_record(key, issue) for key, issue in self.errors],
                "warnings": [_issue_record(key, issue) for key, issue in self.warnings],
            },
            "source_precedence": list(self.source_precedence),
            "sources": self.sources,
        }
        if include_plan_id:
            data["plan_id"] = self.plan_id
        if include_runtime:
            data["runtime"] = {
                "backup": self.backup,
                "transaction_status": self.transaction_status,
                "diagnostics": list(self.diagnostics),
            }
        return data

    @classmethod
    def from_dict(cls, data: object) -> InjectionPlan:
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported operation-plan schema")
        outcomes = data.get("outcomes")
        validation = data.get("validation")
        if not isinstance(outcomes, dict) or not isinstance(validation, dict):
            raise ValueError("operation plan lacks outcomes or validation")
        plan = cls(
            added=_string_list(outcomes.get("add"), "add"),
            updated=_string_list(outcomes.get("change"), "change"),
            removed=_string_list(outcomes.get("remove"), "remove"),
            unchanged=_string_list(outcomes.get("unchanged"), "unchanged"),
            skipped=_string_list(outcomes.get("skipped"), "skipped"),
            errors=_issue_list(validation.get("errors"), "errors"),
            warnings=_issue_list(validation.get("warnings"), "warnings"),
            channel=_optional_string(data.get("channel"), "channel"),
            language=_optional_string(data.get("language"), "language"),
            mode=_optional_string(data.get("mode"), "mode"),
            baseline_source=_optional_string(
                data.get("baseline_source"), "baseline_source"
            ),
            target=_optional_string(data.get("target"), "target"),
            target_fingerprint=(
                FileFingerprint.from_dict(data["target_fingerprint"])
                if data.get("target_fingerprint") is not None
                else None
            ),
            baseline_sha256=_optional_digest(data.get("baseline_sha256")),
            desired_sha256=_optional_digest(data.get("desired_sha256")),
            source_precedence=_string_list(
                data.get("source_precedence", []), "source_precedence"
            ),
            sources=(dict(data["sources"]) if isinstance(data.get("sources"), dict) else {}),
        )
        runtime = data.get("runtime", {})
        if isinstance(runtime, dict):
            plan.backup = _optional_string(runtime.get("backup"), "backup")
            plan.transaction_status = _optional_string(
                runtime.get("transaction_status"), "transaction_status"
            )
            plan.diagnostics = _string_list(
                runtime.get("diagnostics", []), "diagnostics"
            )
        supplied_id = data.get("plan_id")
        if supplied_id is not None:
            supplied_id = _optional_digest(supplied_id)
            if supplied_id != plan.compute_id():
                raise ValueError("operation plan identity does not match its contents")
            plan.plan_id = supplied_id
        return plan

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def loads(cls, text: str) -> InjectionPlan:
        return cls.from_dict(json.loads(text))

    def save(self, path: Path) -> None:
        _atomic_write(path, self.dumps().encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> InjectionPlan:
        if path.stat().st_size > MAX_OPERATION_PLAN_BYTES:
            raise ValueError("operation plan exceeds its size limit")
        return cls.loads(path.read_text(encoding="utf-8"))


def plan(
    target: LocalizationFile,
    replacements: dict[str, str],
    *,
    allowed_additions: set[str] | frozenset[str] = frozenset(),
    removals: set[str] | frozenset[str] = frozenset(),
    allowed_removals: set[str] | frozenset[str] = frozenset(),
) -> InjectionPlan:
    """Work out what would change. Pure -- touches no files."""
    result = InjectionPlan()

    for key, value in replacements.items():
        resolved = target.resolve_key(key)
        trusted_source = target.get(resolved) if resolved is not None else ""
        for issue in validate_value(value, trusted_source=trusted_source or ""):
            bucket = result.errors if issue.severity is Severity.ERROR else result.warnings
            bucket.append((key, issue))

        if resolved is None:
            if key in allowed_additions:
                result.added.append(key)
            else:
                result.skipped.append(key)
        elif target.get(resolved) == value:
            result.unchanged.append(key)
        else:
            result.updated.append(key)

    for key in sorted(removals):
        if key not in allowed_removals:
            result.errors.append(
                (
                    key,
                    Issue(
                        Severity.ERROR,
                        "unauthorized-removal",
                        "key removal was not authorized by user-added source metadata",
                    ),
                )
            )
        elif key in replacements:
            result.errors.append(
                (
                    key,
                    Issue(
                        Severity.ERROR,
                        "conflicting-operation",
                        "key cannot be replaced and removed in the same operation",
                    ),
                )
            )
        elif target.resolve_key(key) is None:
            result.unchanged.append(key)
        else:
            result.removed.append(key)

    return result


def build_operation_plan(
    baseline: LocalizationFile,
    effective_before: LocalizationFile,
    replacements: dict[str, str],
    *,
    allowed_additions: set[str] | frozenset[str] = frozenset(),
    removals: set[str] | frozenset[str] = frozenset(),
    allowed_removals: set[str] | frozenset[str] = frozenset(),
) -> tuple[InjectionPlan, bytes]:
    """Build the exact resulting file and classify it against effective input.

    This differs from :func:`plan` only for overwrite operations: the baseline
    may be pristine stock while ``effective_before`` is a populated override.
    Comparing both complete semantic tables makes discarded override keys show
    up as removals instead of disappearing implicitly.
    """

    validation = plan(
        baseline,
        replacements,
        allowed_additions=allowed_additions,
        removals=removals,
        allowed_removals=allowed_removals,
    )
    desired = LocalizationFile.loads(baseline.dumps())
    for key in validation.updated:
        desired.set(key, replacements[key])
    for key in validation.added:
        desired.add(key, replacements[key])
    for key in validation.removed:
        desired.remove(key)

    before_values = {entry.key: entry.value for entry in effective_before.entries()}
    desired_values = {entry.key: entry.value for entry in desired.entries()}
    result = InjectionPlan(
        skipped=list(validation.skipped),
        errors=list(validation.errors),
        warnings=list(validation.warnings),
    )
    for label, localization in (
        ("effective target", effective_before),
        ("prepared baseline", baseline),
    ):
        seen: set[str] = set()
        for entry in localization.entries():
            if entry.key in seen:
                result.errors.append(
                    (
                        entry.key,
                        Issue(
                            Severity.ERROR,
                            "duplicate-key",
                            f"{label} contains a duplicate localization key",
                        ),
                    )
                )
            seen.add(entry.key)
    for key in sorted(set(before_values) | set(desired_values)):
        if key not in before_values:
            result.added.append(key)
        elif key not in desired_values:
            result.removed.append(key)
        elif before_values[key] != desired_values[key]:
            result.updated.append(key)

    changed = set(result.added + result.updated + result.removed)
    for requested in sorted(set(replacements) | set(removals)):
        actual = effective_before.resolve_key(requested) or desired.resolve_key(requested)
        if (actual or requested) not in changed and requested not in result.skipped:
            result.unchanged.append(requested)
    return result, desired.dumps().encode("utf-8")


def _linked_or_reparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat_module.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _ordinary_file_stat(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise BackupSafetyError(f"{label} could not be inspected: {exc}") from exc
    if _linked_or_reparse(metadata) or not stat_module.S_ISREG(metadata.st_mode):
        raise BackupSafetyError(f"{label} must be an ordinary local file")
    return metadata


def _safe_backup_directory(path: Path, *, create: bool) -> os.stat_result | None:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return None
        path.mkdir(parents=True, exist_ok=True)
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise BackupSafetyError(f"backup directory could not be inspected: {exc}") from exc
    if _linked_or_reparse(metadata) or not stat_module.S_ISDIR(metadata.st_mode):
        raise BackupSafetyError("backup directory must be an ordinary local directory")
    return metadata


def _backup_pattern(target: Path) -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(target.stem)}\."
        rf"(?P<stamp>\d{{8}}-\d{{6}})"
        rf"(?:-(?P<fraction>\d{{6}}))?"
        rf"(?:-(?P<counter>\d+))?"
        rf"{re.escape(target.suffix)}\Z"
    )


def _backup_entries(
    backup_dir: Path,
    target: Path,
) -> list[tuple[Path, tuple[int, int, int, int]]]:
    if _safe_backup_directory(backup_dir, create=False) is None:
        return []
    pattern = _backup_pattern(target)
    result: list[tuple[Path, tuple[int, int, int, int]]] = []
    try:
        entries = list(os.scandir(backup_dir))
    except OSError as exc:
        raise BackupSafetyError(f"backup directory could not be read: {exc}") from exc
    for entry in entries:
        if not pattern.fullmatch(entry.name):
            continue
        try:
            # Windows DirEntry.stat() may report zero device/inode values even
            # when os.stat(path) exposes the stable file identity.
            metadata = os.stat(entry.path, follow_symlinks=False)
        except OSError:
            continue
        if _linked_or_reparse(metadata) or not stat_module.S_ISREG(metadata.st_mode):
            continue
        result.append((Path(entry.path), _identity(metadata)))
    def newest_first(item: tuple[Path, tuple[int, int, int, int]]):
        match = pattern.fullmatch(item[0].name)
        assert match is not None
        return (
            match.group("stamp"),
            int(match.group("fraction") or -1),
            int(match.group("counter") or 0),
        )

    result.sort(key=newest_first, reverse=True)
    return result


def safe_backups(backup_dir: Path, target: Path) -> tuple[Path, ...]:
    """List only timestamped, ordinary files directly in one backup scope."""

    return tuple(path for path, _metadata in _backup_entries(Path(backup_dir), target))


def _validate_retention(keep: int) -> int:
    if type(keep) is not int or not 1 <= keep <= MAX_BACKUP_RETENTION:
        raise ValueError(
            f"backup retention must be between 1 and {MAX_BACKUP_RETENTION}"
        )
    return keep


@contextmanager
def _backup_scope_lock(backup_dir: Path, target: Path):
    """Serialize target backup, replace, journal, and retention operations."""

    _safe_backup_directory(backup_dir, create=True)
    lock_id = hashlib.sha256(
        f"{backup_dir.resolve()}\0{target.resolve(strict=False)}".encode("utf-8")
    ).hexdigest()[:16]
    lock_path = backup_dir.parent / f".starcompanion-backup-{lock_id}.lock"
    try:
        existing = os.stat(lock_path, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        _linked_or_reparse(existing) or not stat_module.S_ISREG(existing.st_mode)
    ):
        raise BackupSafetyError("backup operation lock is not an ordinary file")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BackupSafetyError(f"backup operation lock could not be opened: {exc}") from exc
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    locked = False
    try:
        opened = os.fstat(stream.fileno())
        linked = os.stat(lock_path, follow_symlinks=False)
        if (
            _linked_or_reparse(linked)
            or not stat_module.S_ISREG(linked.st_mode)
            or opened.st_dev != linked.st_dev
            or opened.st_ino != linked.st_ino
        ):
            raise BackupSafetyError("backup operation lock changed while opening")
        if opened.st_size == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + BACKUP_LOCK_TIMEOUT_SECONDS
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise BackupSafetyError(
                        "backup scope is busy; another operation is still running"
                    )
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


def _backup_destination(path: Path, backup_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"{path.stem}.{stamp}-{counter}{path.suffix}"
        counter += 1
    return destination


def _copy_backup_unlocked(path: Path, backup_dir: Path) -> Path:
    before = _ordinary_file_stat(path, "backup source")
    destination = _backup_destination(path, backup_dir)
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise BackupSafetyError("backup source changed while opening")
        data = source.read()
    after = _ordinary_file_stat(path, "backup source")
    if _identity(after) != _identity(before):
        raise BackupSafetyError("backup source changed while it was being copied")
    _atomic_write(destination, data, expected_resolved=destination.resolve(strict=False))
    try:
        os.chmod(destination, stat_module.S_IMODE(before.st_mode))
    except OSError:
        pass
    return destination


def _bytes_backup_unlocked(path: Path, backup_dir: Path, data: bytes) -> Path:
    destination = _backup_destination(path, backup_dir)
    _atomic_write(destination, data, expected_resolved=destination.resolve(strict=False))
    return destination


def backup(path: Path, backup_dir: Path) -> Path:
    path = Path(path)
    backup_dir = Path(backup_dir)
    with _backup_scope_lock(backup_dir, path):
        return _copy_backup_unlocked(path, backup_dir)


def _prune_backups_unlocked(
    backup_dir: Path,
    target: Path,
    keep: int,
    *,
    protected: tuple[Path, ...] = (),
) -> BackupPruneResult:
    keep = _validate_retention(keep)
    entries = _backup_entries(backup_dir, target)
    protected_resolved = {path.resolve(strict=False) for path in protected}
    ordered = [
        item for item in entries if item[0].resolve(strict=False) in protected_resolved
    ] + [
        item for item in entries if item[0].resolve(strict=False) not in protected_resolved
    ]
    retained = {path for path, _metadata in ordered[:keep]}
    deleted: list[Path] = []
    skipped: list[Path] = []
    for path, identity in entries:
        if path in retained:
            continue
        try:
            current = os.stat(path, follow_symlinks=False)
            if (
                _linked_or_reparse(current)
                or not stat_module.S_ISREG(current.st_mode)
                or _identity(current) != identity
            ):
                skipped.append(path)
                continue
            path.unlink()
            deleted.append(path)
        except FileNotFoundError:
            continue
        except OSError:
            skipped.append(path)
    return BackupPruneResult(tuple(deleted), tuple(skipped))


def prune_backups(
    backup_dir: Path,
    target: Path,
    keep: int = DEFAULT_BACKUP_RETENTION,
    *,
    protected: tuple[Path, ...] = (),
) -> BackupPruneResult:
    keep = _validate_retention(keep)
    backup_dir = Path(backup_dir)
    target = Path(target)
    if _safe_backup_directory(backup_dir, create=False) is None:
        return BackupPruneResult(())
    with _backup_scope_lock(backup_dir, target):
        return _prune_backups_unlocked(
            backup_dir,
            target,
            keep,
            protected=protected,
        )


def apply(
    target_path: Path,
    replacements: dict[str, str],
    *,
    confirmed: bool,
    mode: MergeMode = MergeMode.MERGE,
    stock_path: Path | None = None,
    backup_dir: Path | None = None,
    source: LocalizationFile | None = None,
    allowed_additions: set[str] | frozenset[str] = frozenset(),
    removals: set[str] | frozenset[str] = frozenset(),
    allowed_removals: set[str] | frozenset[str] = frozenset(),
    expected_fingerprint: FileFingerprint | None = None,
    operation_plan: InjectionPlan | None = None,
    journal: TransactionJournal | None = None,
    backup_retention: int = DEFAULT_BACKUP_RETENTION,
) -> InjectionPlan:
    """Write `replacements` into the file at `target_path`.

    Raises unless `confirmed` is True, the plan validates, and OVERWRITE mode
    was given a pristine `stock_path` to rebuild from.
    """
    backup_retention = _validate_retention(backup_retention)
    if not confirmed:
        raise UnconfirmedWriteError(
            "Refusing to write without explicit confirmation. Show plan() to the user first."
        )

    if mode is MergeMode.OVERWRITE and stock_path is None and source is None:
        raise ValueError("OVERWRITE mode needs stock_path -- a pristine, unmodified global.ini")

    reviewed_target = target_path.resolve(strict=False)
    if operation_plan is not None and operation_plan.target is not None:
        if reviewed_target != Path(operation_plan.target):
            raise TargetChangedError(
                "target path no longer resolves to the reviewed operation-plan target"
            )

    current_fingerprint = fingerprint(target_path)
    if expected_fingerprint is not None and current_fingerprint != expected_fingerprint:
        raise TargetChangedError(
            "target changed after preview; prepare and review a new plan before applying"
        )

    if source is not None:
        # Work on a copy: callers may reuse their prepared baseline for preview.
        target = LocalizationFile.loads(source.dumps())
    else:
        source_path = stock_path if mode is MergeMode.OVERWRITE else target_path
        target = LocalizationFile.load(source_path)

    result = plan(
        target,
        replacements,
        allowed_additions=allowed_additions,
        removals=removals,
        allowed_removals=allowed_removals,
    )
    if not result.is_valid:
        raise ValidationFailedError(result.errors)

    # A no-op should not create directories, backups, or rewrite an identical
    # file. This is especially important on a clean install.
    operation_changes = bool(
        operation_plan
        and (operation_plan.added or operation_plan.updated or operation_plan.removed)
    )
    if not result.updated and not result.added and not result.removed and not operation_changes:
        return result
    baseline_data = target.dumps().encode("utf-8")

    for key in result.updated:
        target.set(key, replacements[key])
    for key in result.added:
        if not target.add(key, replacements[key]):
            raise OSError(f"authorized localization addition collided for {key}")
    for key in result.removed:
        if not target.remove(key):
            raise OSError(f"planned localization removal disappeared for {key}")

    desired_data = target.dumps().encode("utf-8")
    desired_sha256 = bytes_sha256(desired_data)
    if operation_plan is not None and operation_plan.desired_sha256 != desired_sha256:
        raise TargetChangedError(
            "prepared output no longer matches the reviewed operation plan"
        )
    active_plan = operation_plan or result
    active_plan.plan_id = active_plan.plan_id or active_plan.compute_id()
    destination_dir = Path(backup_dir or target_path.parent / "backups")
    with _backup_scope_lock(destination_dir, target_path):
        if fingerprint(target_path) != current_fingerprint:
            raise TargetChangedError(
                "target changed while waiting for the backup scope; nothing was replaced"
            )
        if target_path.resolve(strict=False) != reviewed_target:
            raise TargetChangedError(
                "target path changed while waiting for the backup scope; nothing was replaced"
            )
        if journal is not None:
            journal.begin(
                operation="apply",
                plan_id=active_plan.plan_id,
                target=target_path,
                before=current_fingerprint,
                after_sha256=desired_sha256,
            )

        if target_path.exists():
            backup_path = _copy_backup_unlocked(target_path, destination_dir)
        else:
            # A first apply has no loose file to copy. Save the pristine prepared
            # baseline so the same Undo action still removes all enhancements.
            backup_path = _bytes_backup_unlocked(
                target_path,
                destination_dir,
                baseline_data,
            )
        active_plan.backup = str(backup_path.resolve())
        if journal is not None:
            journal.record_backup(backup_path)

        if fingerprint(target_path) != current_fingerprint:
            raise TargetChangedError(
                "target changed while the backup was being created; nothing was replaced"
            )
        if target_path.resolve(strict=False) != reviewed_target:
            raise TargetChangedError(
                "target path changed while the backup was being created; nothing was replaced"
            )

        _atomic_save(
            target,
            target_path,
            replacements,
            [*result.updated, *result.added],
            result.removed,
            data=desired_data,
            expected_resolved=reviewed_target,
        )
        if journal is not None:
            journal.record_replaced()
        final_fingerprint = fingerprint(target_path)
        if final_fingerprint.sha256 != desired_sha256:
            raise OSError("installed target fingerprint does not match the operation plan")
        active_plan.transaction_status = "complete"
        active_plan.diagnostics.append("target fingerprint verified after atomic replace")
        if journal is not None:
            journal.complete(final=final_fingerprint)
        try:
            pruned = _prune_backups_unlocked(
                destination_dir,
                target_path,
                backup_retention,
                protected=(backup_path,),
            )
        except (OSError, ValueError, BackupSafetyError) as exc:
            active_plan.diagnostics.append(
                f"backup retention cleanup skipped after verified write: {exc}"
            )
        else:
            if pruned.deleted:
                active_plan.diagnostics.append(
                    f"pruned {len(pruned.deleted)} older backup(s); retained "
                    f"the newest {backup_retention}"
                )
            if pruned.skipped:
                active_plan.diagnostics.append(
                    f"retention skipped {len(pruned.skipped)} backup(s) whose identity changed"
                )
    result.backup = active_plan.backup
    result.transaction_status = active_plan.transaction_status
    result.diagnostics = list(active_plan.diagnostics)
    return result


def restore(
    backup_path: Path,
    target_path: Path,
    *,
    expected_backup_fingerprint: FileFingerprint | None = None,
    expected_target_fingerprint: FileFingerprint | None = None,
) -> None:
    _ordinary_file_stat(backup_path, "selected backup")
    reviewed_backup = backup_path.resolve(strict=False)
    reviewed_target = target_path.resolve(strict=False)
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    backup_fingerprint = fingerprint(backup_path)
    if (
        expected_backup_fingerprint is not None
        and backup_fingerprint != expected_backup_fingerprint
    ):
        raise TargetChangedError("selected backup changed before rollback")
    if (
        expected_target_fingerprint is not None
        and fingerprint(target_path) != expected_target_fingerprint
    ):
        raise TargetChangedError("target changed before rollback")
    data = backup_path.read_bytes()
    if backup_path.resolve(strict=False) != reviewed_backup:
        raise TargetChangedError("selected backup path changed while it was being read")
    if fingerprint(backup_path) != backup_fingerprint:
        raise TargetChangedError("selected backup changed while it was being read")
    if (
        expected_target_fingerprint is not None
        and fingerprint(target_path) != expected_target_fingerprint
    ):
        raise TargetChangedError("target changed while rollback was being prepared")
    _atomic_write(target_path, data, expected_resolved=reviewed_target)


def rollback(
    backup_path: Path,
    target_path: Path,
    *,
    confirmed: bool,
    backup_dir: Path | None = None,
    expected_backup_fingerprint: FileFingerprint | None = None,
    expected_target_fingerprint: FileFingerprint | None = None,
    journal: TransactionJournal | None = None,
    backup_retention: int = DEFAULT_BACKUP_RETENTION,
) -> RollbackResult:
    """Restore one reviewed, target-scoped backup under the apply lock."""

    backup_retention = _validate_retention(backup_retention)
    if not confirmed:
        raise UnconfirmedWriteError("refusing rollback without explicit confirmation")
    backup_path = Path(backup_path)
    target_path = Path(target_path)
    directory = Path(backup_dir or target_path.parent / "backups")
    reviewed_backup = backup_path.resolve(strict=False)
    reviewed_target = target_path.resolve(strict=False)
    if reviewed_backup.parent != directory.resolve(strict=False):
        raise BackupSafetyError("selected backup is outside the active backup scope")
    recognized = {path.resolve(strict=False) for path in safe_backups(directory, target_path)}
    if reviewed_backup not in recognized:
        raise BackupSafetyError(
            "selected backup is not a recognized ordinary timestamped restore point"
        )
    backup_fingerprint = fingerprint(backup_path)
    if (
        expected_backup_fingerprint is not None
        and backup_fingerprint != expected_backup_fingerprint
    ):
        raise TargetChangedError("selected backup changed before rollback")
    before = fingerprint(target_path)
    if expected_target_fingerprint is not None and before != expected_target_fingerprint:
        raise TargetChangedError("target changed before rollback")
    rollback_id = bytes_sha256(
        f"rollback\0{reviewed_target}\0{before.sha256}\0{backup_fingerprint.sha256}".encode(
            "utf-8"
        )
    )
    diagnostics: list[str] = []
    with _backup_scope_lock(directory, target_path):
        if backup_path.resolve(strict=False) != reviewed_backup:
            raise TargetChangedError("selected backup path changed before rollback")
        if target_path.resolve(strict=False) != reviewed_target:
            raise TargetChangedError("target path changed before rollback")
        if fingerprint(backup_path) != backup_fingerprint:
            raise TargetChangedError("selected backup changed before rollback")
        if fingerprint(target_path) != before:
            raise TargetChangedError("target changed while waiting for rollback")
        if journal is not None:
            journal.begin(
                operation="rollback",
                plan_id=rollback_id,
                target=target_path,
                before=before,
                after_sha256=backup_fingerprint.sha256,
            )
        recovery_backup = (
            _copy_backup_unlocked(target_path, directory) if before.exists else None
        )
        if journal is not None and recovery_backup is not None:
            journal.record_backup(recovery_backup)
        if fingerprint(backup_path) != backup_fingerprint:
            raise TargetChangedError(
                "selected backup changed while the recovery backup was created"
            )
        if fingerprint(target_path) != before:
            raise TargetChangedError(
                "target changed while the recovery backup was created"
            )
        restore(
            backup_path,
            target_path,
            expected_backup_fingerprint=backup_fingerprint,
            expected_target_fingerprint=before,
        )
        if journal is not None:
            journal.record_replaced()
        final = fingerprint(target_path)
        if final.sha256 != backup_fingerprint.sha256:
            raise OSError("restored target fingerprint does not match the reviewed backup")
        if journal is not None:
            journal.complete(final=final)
        diagnostics.append("restored target fingerprint verified after atomic replace")
        protected = (recovery_backup,) if recovery_backup is not None else (backup_path,)
        try:
            pruned = _prune_backups_unlocked(
                directory,
                target_path,
                backup_retention,
                protected=protected,
            )
        except (OSError, ValueError, BackupSafetyError) as exc:
            diagnostics.append(
                f"backup retention cleanup skipped after verified rollback: {exc}"
            )
        else:
            if pruned.deleted:
                diagnostics.append(f"pruned {len(pruned.deleted)} older backup(s)")
            if pruned.skipped:
                diagnostics.append(
                    f"retention skipped {len(pruned.skipped)} backup(s) whose identity changed"
                )
    return RollbackResult(recovery_backup, final, tuple(diagnostics))


def _source_summary(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError("source report entry must be an object")
    winner = data.get("winner")
    winner_kind = data.get("winner_kind")
    conflicted = data.get("conflicted")
    if not isinstance(winner, str) or not isinstance(winner_kind, str):
        raise ValueError("source report entry lacks a winner")
    if not isinstance(conflicted, bool):
        raise ValueError("source report conflict status must be boolean")
    return {
        "winner": winner,
        "winner_kind": winner_kind,
        "conflicted": conflicted,
    }


def _issue_record(key: str, issue: Issue) -> dict[str, object]:
    return {
        "key": key,
        "severity": issue.severity.value,
        "code": issue.code,
        "message": issue.message,
        "offset": issue.offset,
    }


def _issue_list(data: object, label: str) -> list[tuple[str, Issue]]:
    if not isinstance(data, list):
        raise ValueError(f"operation plan {label} must be a list")
    result: list[tuple[str, Issue]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ValueError(f"invalid operation plan {label} entry")
        try:
            issue = Issue(
                Severity(item["severity"]),
                str(item["code"]),
                str(item["message"]),
                item.get("offset"),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid operation plan {label} issue") from exc
        result.append((item["key"], issue))
    return result


def _string_list(data: object, label: str) -> list[str]:
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"operation plan {label} must be a string list")
    if len(data) != len(set(data)):
        raise ValueError(f"operation plan {label} contains duplicates")
    return list(data)


def _optional_string(data: object, label: str) -> str | None:
    if data is None:
        return None
    if not isinstance(data, str):
        raise ValueError(f"operation plan {label} must be text")
    return data


def _optional_digest(data: object) -> str | None:
    if data is None:
        return None
    if not isinstance(data, str) or len(data) != 64 or any(
        char not in "0123456789abcdef" for char in data
    ):
        raise ValueError("operation plan digest is invalid")
    return data


def _atomic_save(
    localization: LocalizationFile,
    target_path: Path,
    replacements: dict[str, str],
    updated: list[str],
    removed: list[str] | None = None,
    *,
    data: bytes | None = None,
    expected_resolved: Path | None = None,
) -> None:
    data = data if data is not None else localization.dumps().encode("utf-8")

    # Verify the exact bytes that will be installed before touching the target.
    check = LocalizationFile.loads(data.decode("utf-8"))
    for key in updated:
        if check.get(key) != replacements[key]:
            raise OSError(f"post-render verification failed for {key}")
    for key in removed or ():
        if check.get(key) is not None:
            raise OSError(f"post-render removal verification failed for {key}")

    _atomic_write(target_path, data, expected_resolved=expected_resolved)


def _atomic_write(
    path: Path, data: bytes, *, expected_resolved: Path | None = None
) -> None:
    """Flush a sibling temporary file, then replace ``path`` atomically."""
    if expected_resolved is not None and path.resolve(strict=False) != expected_resolved:
        raise TargetChangedError("target path changed before atomic write")
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected_resolved is not None and path.resolve(strict=False) != expected_resolved:
        raise TargetChangedError("target path changed before atomic write")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        if expected_resolved is not None and path.resolve(strict=False) != expected_resolved:
            raise TargetChangedError("target path changed before atomic replace")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
