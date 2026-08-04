"""Shared progress and cooperative-cancellation contracts for long operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Event
from typing import Callable, TypeAlias


class OperationCancelled(RuntimeError):
    """Raised at a safe checkpoint after the caller requests cancellation."""


class OperationStage(Enum):
    OPEN_ARCHIVE = "open-archive"
    INDEX_ARCHIVE = "index-archive"
    READ_LOCALIZATION = "read-localization"
    VALIDATE_LOCALIZATION = "validate-localization"
    PARSE_CONTRACTS = "parse-contracts"
    READ_DATACORE = "read-datacore"
    PARSE_DATACORE = "parse-datacore"
    APPLY_ENHANCEMENTS = "apply-enhancements"
    PREVIEW_CHANGES = "preview-changes"
    COMPLETE = "complete"


@dataclass(frozen=True)
class ProgressEvent:
    stage: OperationStage
    message: str
    current: int | None = None
    total: int | None = None

    @property
    def fraction(self) -> float:
        """Stable overall progress estimate suitable for a progress bar."""
        bounds = {
            OperationStage.OPEN_ARCHIVE: (0.00, 0.03),
            OperationStage.INDEX_ARCHIVE: (0.03, 0.76),
            OperationStage.READ_LOCALIZATION: (0.76, 0.80),
            OperationStage.VALIDATE_LOCALIZATION: (0.80, 0.82),
            OperationStage.READ_DATACORE: (0.82, 0.90),
            OperationStage.PARSE_DATACORE: (0.90, 0.96),
            OperationStage.PARSE_CONTRACTS: (0.96, 0.98),
            OperationStage.APPLY_ENHANCEMENTS: (0.98, 0.99),
            OperationStage.PREVIEW_CHANGES: (0.98, 0.995),
            OperationStage.COMPLETE: (1.00, 1.00),
        }
        start, end = bounds[self.stage]
        if self.current is None or not self.total:
            return start
        position = min(1.0, max(0.0, self.current / self.total))
        return start + ((end - start) * position)


ProgressReporter: TypeAlias = Callable[[ProgressEvent], None]


class CancellationToken:
    """Thread-safe cancellation flag checked only at known-safe boundaries."""

    def __init__(self, event=None) -> None:
        # multiprocessing.Event and threading.Event share the small interface
        # used here. Injection keeps the token usable inside a spawned helper.
        self._cancelled = event or Event()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def checkpoint(self) -> None:
        if self.is_cancelled:
            raise OperationCancelled("operation cancelled")


def report(
    reporter: ProgressReporter | None,
    stage: OperationStage,
    message: str,
    current: int | None = None,
    total: int | None = None,
) -> None:
    if reporter is not None:
        reporter(ProgressEvent(stage, message, current, total))


__all__ = [
    "CancellationToken",
    "OperationCancelled",
    "OperationStage",
    "ProgressEvent",
    "ProgressReporter",
    "report",
]
