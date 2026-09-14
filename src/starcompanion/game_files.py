"""Reviewed, backup-first mutations for small Star Citizen control files."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from . import inject
from .install import GameInstall, normalize_language
from .transactions import (
    FileFingerprint,
    TargetChangedError,
    TransactionJournal,
    bytes_sha256,
    fingerprint,
)

MAX_USER_CFG_BYTES = 1024 * 1024
_LANGUAGE_LINE = re.compile(
    r"^(\s*g_language\s*=\s*)([^\s;#]*)(.*?)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)


class GameFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameFilePlan:
    operation: str
    target: Path
    reviewed_target: Path
    before: FileFingerprint
    after: bytes | None
    plan_id: str
    summary: str
    encoding: str | None = None
    newline: str | None = None

    @property
    def changed(self) -> bool:
        if self.after is None:
            return self.before.exists
        return self.before.sha256 != bytes_sha256(self.after)


@dataclass(frozen=True)
class GameFileResult:
    backup: Path | None
    final: FileFingerprint


def _target(install: GameInstall, relative: str | Path) -> Path:
    root = Path(os.path.abspath(install.root))
    target = Path(os.path.abspath(root / relative))
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise GameFileError(
            "game control-file target escaped the selected install"
        ) from exc
    current = root
    relative_parent = target.parent.relative_to(root)
    for part in (".", *relative_parent.parts):
        if part != ".":
            current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        junction = getattr(current, "is_junction", None)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(attributes & reparse)
            or bool(junction and junction())
        ):
            raise GameFileError("game control-file path traverses a link or junction")
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise GameFileError(
            "game control-file target escaped the selected install"
        ) from exc
    return target


def _ordinary_or_missing(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise GameFileError("game control file must be an ordinary local file")


def _read_small(path: Path) -> bytes:
    _ordinary_or_missing(path)
    if not path.exists():
        return b""
    if path.stat().st_size > MAX_USER_CFG_BYTES:
        raise GameFileError("USER.cfg exceeds the 1 MiB safety limit")
    with path.open("rb") as stream:
        payload = stream.read(MAX_USER_CFG_BYTES + 1)
    if len(payload) > MAX_USER_CFG_BYTES:
        raise GameFileError("USER.cfg changed beyond the 1 MiB safety limit")
    return payload


def _decode(payload: bytes) -> tuple[str, str, bytes]:
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload[3:].decode("utf-8"), "utf-8", b"\xef\xbb\xbf"
    if payload.startswith(b"\xff\xfe"):
        return payload[2:].decode("utf-16-le"), "utf-16-le", b"\xff\xfe"
    if payload.startswith(b"\xfe\xff"):
        return payload[2:].decode("utf-16-be"), "utf-16-be", b"\xfe\xff"
    try:
        return payload.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError as exc:
        raise GameFileError(
            "USER.cfg encoding is not UTF-8 or BOM-marked UTF-16; nothing was changed"
        ) from exc


def _preferred_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf or lf or cr:
        return max(((crlf, "\r\n"), (lf, "\n"), (cr, "\r")))[1]
    return "\r\n"


def _plan_id(
    operation: str,
    target: Path,
    before: FileFingerprint,
    after: bytes | None,
) -> str:
    digest = hashlib.sha256()
    for value in (
        operation,
        str(target),
        before.sha256 or "missing",
        bytes_sha256(after) if after is not None else "missing",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def plan_language_activation(
    install: GameInstall,
    language: str,
) -> GameFilePlan:
    """Preview changing only the effective g_language assignment."""

    selected = normalize_language(language)
    target = _target(install, "USER.cfg")
    payload = _read_small(target)
    before = fingerprint(target)
    text, encoding, bom = _decode(payload)
    newline = _preferred_newline(text)
    lines = text.splitlines(keepends=True)
    matching = [index for index, line in enumerate(lines) if _LANGUAGE_LINE.match(line)]
    if matching:
        index = matching[-1]
        match = _LANGUAGE_LINE.match(lines[index])
        assert match is not None
        lines[index] = f"{match.group(1)}{selected}{match.group(3)}{match.group(4) or ''}"
    else:
        if text and not text.endswith(("\r", "\n")):
            lines.append(newline)
        lines.append(f"g_language = {selected}{newline}")
    rendered = "".join(lines)
    after = bom + rendered.encode(encoding)
    return GameFilePlan(
        "activate-language",
        target,
        target.resolve(strict=False),
        before,
        after,
        _plan_id("activate-language", target, before, after),
        f"Set the effective g_language value to {selected}; preserve all other USER.cfg content.",
        encoding + ("-bom" if bom else ""),
        {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[newline],
    )


def plan_restore_stock(
    install: GameInstall,
    language: str,
) -> GameFilePlan:
    """Preview removal of only the selected loose localization override."""

    selected = normalize_language(language)
    target = _target(
        install,
        Path("data") / "Localization" / selected / "global.ini",
    )
    _ordinary_or_missing(target)
    before = fingerprint(target)
    return GameFilePlan(
        "restore-stock",
        target,
        target.resolve(strict=False),
        before,
        None,
        _plan_id("restore-stock", target, before, None),
        "Remove the selected loose global.ini so the game loads its stock archive data.",
    )


def apply_game_file_plan(
    plan: GameFilePlan,
    *,
    confirmed: bool,
    backup_dir: Path,
    journal: TransactionJournal,
    backup_retention: int = inject.DEFAULT_BACKUP_RETENTION,
) -> GameFileResult:
    """Apply one reviewed plan under the existing target-scoped lock."""

    if not confirmed:
        raise inject.UnconfirmedWriteError("game control-file change requires confirmation")
    if not plan.changed:
        return GameFileResult(None, plan.before)
    expected_target = plan.reviewed_target
    if plan.target.resolve(strict=False) != expected_target:
        raise TargetChangedError("game control-file path changed after preview")
    _ordinary_or_missing(plan.target)
    if fingerprint(plan.target) != plan.before:
        raise TargetChangedError("game control file changed after preview")
    recovery = journal.inspect(plan.target, resolve_safe=True)
    if recovery.needs_attention or journal.journal_path.exists():
        raise GameFileError(f"recovery must be reviewed first: {recovery.message}")
    after_sha = bytes_sha256(plan.after) if plan.after is not None else None
    destination = Path(backup_dir)
    retention = inject._validate_retention(backup_retention)
    backup = None
    with inject._backup_scope_lock(destination, plan.target):
        _ordinary_or_missing(plan.target)
        if (
            plan.target.resolve(strict=False) != expected_target
            or fingerprint(plan.target) != plan.before
        ):
            raise TargetChangedError("game control file changed before replacement")
        journal.begin(
            operation=plan.operation,
            plan_id=plan.plan_id,
            target=plan.target,
            before=plan.before,
            after_sha256=after_sha,
        )
        if plan.before.exists:
            backup = inject._copy_backup_unlocked(plan.target, destination)
            journal.record_backup(backup)
        if (
            plan.target.resolve(strict=False) != expected_target
            or fingerprint(plan.target) != plan.before
        ):
            raise TargetChangedError("game control file changed while backup was created")
        if plan.after is None:
            plan.target.unlink()
        else:
            inject._atomic_write(
                plan.target,
                plan.after,
                expected_resolved=expected_target,
            )
        journal.record_replaced()
        final = fingerprint(plan.target)
        if (
            (after_sha is None and final.exists)
            or (after_sha is not None and final.sha256 != after_sha)
        ):
            raise OSError("game control-file result did not match the reviewed plan")
        journal.complete(final=final)
        try:
            inject._prune_backups_unlocked(
                destination,
                plan.target,
                retention,
                protected=(backup,) if backup is not None else (),
            )
        except (OSError, inject.BackupSafetyError):
            pass
    return GameFileResult(backup, final)


__all__ = [
    "GameFileError",
    "GameFilePlan",
    "GameFileResult",
    "MAX_USER_CFG_BYTES",
    "apply_game_file_plan",
    "plan_language_activation",
    "plan_restore_stock",
]
