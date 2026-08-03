"""Merge rendered strings into a stock global.ini.

Writes are deliberately awkward to trigger: `plan()` is pure and returns what
would change, and `apply()` refuses to run unless the caller passes
``confirmed=True`` after showing that plan to a human.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .ini import LocalizationFile
from .validate import Issue, Severity, validate_value


# Files that mark a directory as a real Star Citizen install rather than a
# scratch copy. Matched case-insensitively against directory entries.
GAME_MARKERS = ("data.p4k", "bin64", "usergame.cfg")


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
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Keys with no home in the target file -- silently dropped by a naive merge."""
    errors: list[tuple[str, Issue]] = field(default_factory=list)
    warnings: list[tuple[str, Issue]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"{len(self.updated)} updated",
            f"{len(self.unchanged)} unchanged",
            f"{len(self.skipped)} skipped",
        ]
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS")
        return ", ".join(parts)


def plan(target: LocalizationFile, replacements: dict[str, str]) -> InjectionPlan:
    """Work out what would change. Pure -- touches no files."""
    result = InjectionPlan()

    for key, value in replacements.items():
        for issue in validate_value(value):
            bucket = result.errors if issue.severity is Severity.ERROR else result.warnings
            bucket.append((key, issue))

        resolved = target.resolve_key(key)
        if resolved is None:
            result.skipped.append(key)
        elif target.get(resolved) == value:
            result.unchanged.append(key)
        else:
            result.updated.append(key)

    return result


def backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, destination)
    return destination


def apply(
    target_path: Path,
    replacements: dict[str, str],
    *,
    confirmed: bool,
    mode: MergeMode = MergeMode.MERGE,
    stock_path: Path | None = None,
    backup_dir: Path | None = None,
) -> InjectionPlan:
    """Write `replacements` into the file at `target_path`.

    Raises unless `confirmed` is True, the plan validates, and OVERWRITE mode
    was given a pristine `stock_path` to rebuild from.
    """
    if not confirmed:
        raise UnconfirmedWriteError(
            "Refusing to write without explicit confirmation. Show plan() to the user first."
        )

    if mode is MergeMode.OVERWRITE and stock_path is None:
        raise ValueError("OVERWRITE mode needs stock_path -- a pristine, unmodified global.ini")

    source_path = stock_path if mode is MergeMode.OVERWRITE else target_path
    target = LocalizationFile.load(source_path)

    result = plan(target, replacements)
    if not result.is_valid:
        raise ValidationFailedError(result.errors)

    if target_path.exists():
        backup(target_path, backup_dir or target_path.parent / "backups")

    for key in result.updated:
        target.set(key, replacements[key])

    target.save(target_path)
    return result


def restore(backup_path: Path, target_path: Path) -> None:
    shutil.copy2(backup_path, target_path)
