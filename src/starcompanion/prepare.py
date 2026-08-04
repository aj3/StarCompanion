"""Prepare a channel's localization baseline without writing to the game.

The normal Star Citizen install has no loose ``global.ini``. The stock file
lives inside ``Data.p4k`` and the loose path is an override created by tools
like this one. This module gives every interface one read-only preparation
step that works both before and after that override exists.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .extract.p4k import P4KArchive, is_localization_entry
from .helper_artifacts import HelperArtifacts
from .ini import LocalizationFile
from .inject import InjectionPlan, MergeMode, apply, build_operation_plan, plan
from .install import DEFAULT_LANGUAGE, GameInstall
from .transactions import FileFingerprint, TargetChangedError, bytes_sha256, fingerprint
from .tasks import (
    CancellationToken,
    OperationStage,
    ProgressReporter,
    report,
)


class BaselineSource(Enum):
    ARCHIVE = "archive"
    OVERRIDE = "override"


@dataclass(frozen=True)
class PreparedLocalization:
    """An immutable, read-only baseline ready for preview and confirmed apply."""

    install: GameInstall
    language: str
    baseline_path: Path
    source: BaselineSource
    mode: MergeMode = MergeMode.MERGE
    prepared_target_fingerprint: FileFingerprint | None = None
    owns_baseline: bool = True
    artifacts: HelperArtifacts | None = None
    integrity_warnings: tuple[str, ...] = ()

    @property
    def target(self) -> Path:
        return self.install.localization(self.language)

    def baseline(self) -> LocalizationFile:
        """Return a fresh copy so preview and apply cannot share mutation."""
        with self.baseline_path.open("rb") as stream:
            return LocalizationFile.load_stream(stream)

    def cleanup(self) -> None:
        if self.artifacts is not None:
            self.artifacts.cleanup()
        elif self.owns_baseline:
            self.baseline_path.unlink(missing_ok=True)

    def __enter__(self) -> PreparedLocalization:
        return self

    def __exit__(self, *_exc_info) -> None:
        self.cleanup()

    def preview(
        self,
        replacements: dict[str, str],
        *,
        allowed_additions: set[str] | frozenset[str] = frozenset(),
    ) -> InjectionPlan:
        return plan(
            self.baseline(),
            replacements,
            allowed_additions=allowed_additions,
        )

    def operation_plan(
        self,
        replacements: dict[str, str],
        *,
        allowed_additions: set[str] | frozenset[str] = frozenset(),
        removals: set[str] | frozenset[str] = frozenset(),
        allowed_removals: set[str] | frozenset[str] = frozenset(),
        source_report: dict[str, object] | None = None,
    ) -> InjectionPlan:
        current_target_fingerprint = fingerprint(self.target)
        if (
            self.prepared_target_fingerprint is not None
            and current_target_fingerprint != self.prepared_target_fingerprint
        ):
            raise TargetChangedError(
                "target changed during preparation; prepare a fresh preview"
            )
        baseline = self.baseline()
        baseline_data = baseline.dumps().encode("utf-8")
        effective_before = (
            LocalizationFile.load(self.target)
            if self.target.is_file()
            else LocalizationFile.loads(baseline.dumps())
        )
        result, desired_data = build_operation_plan(
            baseline,
            effective_before,
            replacements,
            allowed_additions=allowed_additions,
            removals=removals,
            allowed_removals=allowed_removals,
        )
        return result.bind(
            channel=self.install.channel,
            language=self.language,
            mode=self.mode,
            baseline_source=self.source.value,
            target=self.target,
            target_fingerprint=current_target_fingerprint,
            baseline_sha256=bytes_sha256(baseline_data),
            desired_sha256=bytes_sha256(desired_data),
            source_report=source_report,
        )

    def commit(
        self,
        replacements: dict[str, str],
        *,
        confirmed: bool,
        backup_dir: Path | None = None,
        allowed_additions: set[str] | frozenset[str] = frozenset(),
    ) -> InjectionPlan:
        return apply(
            self.target,
            replacements,
            confirmed=confirmed,
            source=self.baseline(),
            backup_dir=backup_dir,
            allowed_additions=allowed_additions,
        )


def prepare_localization(
    install: GameInstall,
    *,
    language: str = DEFAULT_LANGUAGE,
    mode: MergeMode = MergeMode.MERGE,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
    temporary_path: Path | None = None,
) -> PreparedLocalization:
    """Read the correct baseline for a merge or overwrite operation.

    MERGE preserves an existing loose override. A clean install, or an
    explicit OVERWRITE operation, starts from the pristine archive copy.
    Nothing is created on disk during preparation.
    """
    if token is not None:
        token.checkpoint()
    target = install.localization(language)
    prepared_target_fingerprint = fingerprint(target)
    if temporary_path is None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="starcompanion-baseline-",
            suffix=".ini",
        )
    else:
        temporary_name = str(temporary_path)
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
    baseline_path = Path(temporary_name)
    integrity_warnings: list[str] = []
    try:
        with os.fdopen(descriptor, "wb") as output:
            if mode is MergeMode.MERGE and target.is_file():
                source = BaselineSource.OVERRIDE
                _copy_override(target, output, token=token, reporter=reporter)
            else:
                source = BaselineSource.ARCHIVE
                stream_stock_localization(
                    install.archive,
                    output.write,
                    language,
                    token=token,
                    reporter=reporter,
                    integrity_warning=integrity_warnings.append,
                )
            output.flush()
            os.fsync(output.fileno())

        if fingerprint(target) != prepared_target_fingerprint:
            raise TargetChangedError(
                "target changed while its baseline was being copied; retry preview"
            )

        report(reporter, OperationStage.VALIDATE_LOCALIZATION, "Checking localization…")
        with baseline_path.open("rb") as stream:
            LocalizationFile.load_stream(stream)
        if token is not None:
            token.checkpoint()
        return PreparedLocalization(
            install=install,
            language=language,
            baseline_path=baseline_path,
            source=source,
            mode=mode,
            prepared_target_fingerprint=prepared_target_fingerprint,
            integrity_warnings=tuple(integrity_warnings),
        )
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        baseline_path.unlink(missing_ok=True)
        raise


def read_stock_localization(
    archive_path: Path,
    language: str = DEFAULT_LANGUAGE,
    *,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
) -> bytes:
    """Compatibility helper; streaming callers should use the function below."""
    chunks: list[bytes] = []
    stream_stock_localization(
        archive_path,
        chunks.append,
        language,
        token=token,
        reporter=reporter,
    )
    return b"".join(chunks)


def stream_stock_localization(
    archive_path: Path,
    consumer,
    language: str = DEFAULT_LANGUAGE,
    *,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
    integrity_warning: Callable[[str], None] | None = None,
) -> int:
    """Stream one pristine localization table without assembling its bytes."""
    checkpoint = token.checkpoint if token is not None else None
    if checkpoint is not None:
        checkpoint()
    report(reporter, OperationStage.OPEN_ARCHIVE, "Opening Data.p4k…")

    def indexing(current: int, total: int) -> None:
        report(
            reporter,
            OperationStage.INDEX_ARCHIVE,
            f"Indexing Data.p4k… {current:,} of {total:,} entries",
            current,
            total,
        )

    phase_bounds = {
        "read": (0, 450),
        "decrypt": (450, 650),
        "decompress": (650, 1000),
    }

    def entry_progress(phase: str, current: int, total: int) -> None:
        start, end = phase_bounds[phase]
        position = min(1.0, max(0.0, current / total)) if total else 1.0
        combined = round(start + ((end - start) * position))
        action = {
            "read": "Reading",
            "decrypt": "Decrypting",
            "decompress": "Decompressing",
        }[phase]
        report(
            reporter,
            OperationStage.READ_LOCALIZATION,
            f"{action} {language} localization… {current:,} of {total:,} bytes",
            combined,
            1000,
        )

    with P4KArchive(
        archive_path,
        progress=indexing,
        entry_progress=entry_progress,
        checkpoint=checkpoint,
        entry_filter=is_localization_entry,
    ) as archive:
        report(
            reporter,
            OperationStage.READ_LOCALIZATION,
            f"Reading {language} localization…",
        )
        written = archive.stream_localization(consumer, language)
        if integrity_warning is not None:
            for warning in archive.integrity_warnings:
                integrity_warning(warning)

    if checkpoint is not None:
        checkpoint()
    return written


def _copy_override(
    source: Path,
    output,
    *,
    token: CancellationToken | None,
    reporter: ProgressReporter | None,
) -> None:
    total = source.stat().st_size
    completed = 0
    report(reporter, OperationStage.READ_LOCALIZATION, "Reading the existing override…", 0, total)
    with source.open("rb") as stream:
        while True:
            if token is not None:
                token.checkpoint()
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            output.write(chunk)
            completed += len(chunk)
            report(
                reporter,
                OperationStage.READ_LOCALIZATION,
                f"Reading the existing override… {completed:,} of {total:,} bytes",
                completed,
                total,
            )


__all__ = [
    "BaselineSource",
    "PreparedLocalization",
    "prepare_localization",
    "read_stock_localization",
    "stream_stock_localization",
]
