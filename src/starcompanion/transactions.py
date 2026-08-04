"""Target fingerprints and parent-owned apply/rollback recovery journals."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

JOURNAL_SCHEMA = 1
MAX_JOURNAL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FileFingerprint:
    exists: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "exists": self.exists,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: object) -> FileFingerprint:
        if not isinstance(data, dict) or not isinstance(data.get("exists"), bool):
            raise ValueError("invalid file fingerprint")
        exists = data["exists"]
        size = data.get("size", 0)
        mtime_ns = data.get("mtime_ns", 0)
        digest = data.get("sha256")
        if not isinstance(size, int) or size < 0:
            raise ValueError("invalid fingerprint size")
        if not isinstance(mtime_ns, int) or mtime_ns < 0:
            raise ValueError("invalid fingerprint timestamp")
        if digest is not None and (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("invalid fingerprint digest")
        if exists != (digest is not None):
            raise ValueError("fingerprint existence and digest disagree")
        return cls(exists, size, mtime_ns, digest)


def fingerprint(path: Path) -> FileFingerprint:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return FileFingerprint(False)
    if not path.is_file():
        raise OSError(f"fingerprint target is not a file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return FileFingerprint(True, stat.st_size, stat.st_mtime_ns, digest.hexdigest())


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TargetChangedError(RuntimeError):
    pass


class TransactionStage(Enum):
    STARTED = "started"
    BACKUP_CREATED = "backup-created"
    TARGET_REPLACED = "target-replaced"
    COMPLETE = "complete"


@dataclass(frozen=True)
class RecoveryReport:
    status: str
    message: str
    plan_id: str | None = None
    stage: TransactionStage | None = None
    backup: Path | None = None
    needs_attention: bool = False


class TransactionJournal:
    """A small journal owned by the parent process, never the archive helper."""

    def __init__(self, journal_path: Path, last_operation_path: Path):
        self.journal_path = journal_path
        self.last_operation_path = last_operation_path

    def begin(
        self,
        *,
        operation: str,
        plan_id: str,
        target: Path,
        before: FileFingerprint,
        after_sha256: str,
    ) -> None:
        self._write(
            {
                "schema_version": JOURNAL_SCHEMA,
                "operation": operation,
                "plan_id": plan_id,
                "target": str(target.resolve()),
                "before": before.to_dict(),
                "after_sha256": after_sha256,
                "stage": TransactionStage.STARTED.value,
                "backup": None,
            }
        )

    def record_backup(self, backup: Path) -> None:
        data = self._load()
        data["backup"] = str(backup.resolve())
        data["stage"] = TransactionStage.BACKUP_CREATED.value
        self._write(data)

    def record_replaced(self) -> None:
        data = self._load()
        data["stage"] = TransactionStage.TARGET_REPLACED.value
        self._write(data)

    def complete(self, *, final: FileFingerprint) -> None:
        data = self._load()
        data["stage"] = TransactionStage.COMPLETE.value
        data["final"] = final.to_dict()
        _atomic_json(self.last_operation_path, data)
        self.journal_path.unlink(missing_ok=True)

    def inspect(self, target: Path, *, resolve_safe: bool = False) -> RecoveryReport:
        if not self.journal_path.is_file():
            return RecoveryReport("clean", "no interrupted operation")
        try:
            data = self._load()
            expected = Path(data["target"])
            if expected.resolve() != target.resolve():
                return RecoveryReport(
                    "attention",
                    "pending journal belongs to a different target",
                    plan_id=data.get("plan_id"),
                    needs_attention=True,
                )
            stage = TransactionStage(data["stage"])
            before = FileFingerprint.from_dict(data["before"])
            after_sha256 = data["after_sha256"]
            if not isinstance(after_sha256, str) or len(after_sha256) != 64:
                raise ValueError("invalid expected final digest")
            current = fingerprint(target)
            backup = Path(data["backup"]) if data.get("backup") else None
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return RecoveryReport(
                "attention",
                f"recovery journal is invalid: {exc}",
                needs_attention=True,
            )

        if current == before:
            report = RecoveryReport(
                "not-applied",
                "interrupted operation did not replace the target",
                data["plan_id"],
                stage,
                backup,
            )
            if resolve_safe:
                self.journal_path.unlink(missing_ok=True)
            return report
        if current.exists and current.sha256 == after_sha256:
            report = RecoveryReport(
                "applied",
                "target replacement completed before interruption",
                data["plan_id"],
                stage,
                backup,
            )
            if resolve_safe:
                self.complete(final=current)
            return report
        return RecoveryReport(
            "attention",
            "target differs from both the previewed and intended states; no automatic recovery was attempted",
            data["plan_id"],
            stage,
            backup,
            True,
        )

    def last_operation(self) -> dict[str, object] | None:
        if not self.last_operation_path.is_file():
            return None
        try:
            if self.last_operation_path.stat().st_size > MAX_JOURNAL_BYTES:
                return None
            return json.loads(self.last_operation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _load(self) -> dict[str, object]:
        if self.journal_path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError("transaction journal exceeds its size limit")
        data = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != JOURNAL_SCHEMA:
            raise ValueError("unsupported transaction journal")
        return data

    def _write(self, data: dict[str, object]) -> None:
        _atomic_json(self.journal_path, data)


def _atomic_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "FileFingerprint",
    "RecoveryReport",
    "TargetChangedError",
    "TransactionJournal",
    "TransactionStage",
    "bytes_sha256",
    "fingerprint",
]
