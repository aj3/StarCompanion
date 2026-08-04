"""Merge rendered strings into a stock global.ini.

Writes are deliberately awkward to trigger: `plan()` is pure and returns what
would change, and `apply()` refuses to run unless the caller passes
``confirmed=True`` after showing that plan to a human.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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


def backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = _backup_destination(path, backup_dir)
    shutil.copy2(path, destination)
    return destination


def _backup_destination(path: Path, backup_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"{path.stem}.{stamp}-{counter}{path.suffix}"
        counter += 1
    return destination


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
) -> InjectionPlan:
    """Write `replacements` into the file at `target_path`.

    Raises unless `confirmed` is True, the plan validates, and OVERWRITE mode
    was given a pristine `stock_path` to rebuild from.
    """
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
    if journal is not None:
        journal.begin(
            operation="apply",
            plan_id=active_plan.plan_id,
            target=target_path,
            before=current_fingerprint,
            after_sha256=desired_sha256,
        )

    destination_dir = backup_dir or target_path.parent / "backups"
    if target_path.exists():
        backup_path = backup(target_path, destination_dir)
    else:
        # A first apply has no loose file to copy. Save the pristine prepared
        # baseline so the same Undo action still removes all enhancements.
        destination_dir.mkdir(parents=True, exist_ok=True)
        backup_path = _backup_destination(target_path, destination_dir)
        _atomic_write(
            backup_path,
            baseline_data,
        )
    active_plan.backup = str(backup_path.resolve())
    if journal is not None:
        journal.record_backup(backup_path)

    # Close the preview/write race as far as an atomic filesystem API permits:
    # the exact target is fingerprinted again after backup and immediately
    # before the sibling temporary file is replaced.
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
