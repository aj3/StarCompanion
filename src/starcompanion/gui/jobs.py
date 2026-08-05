"""Qt ownership for cancellable headless operations."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..tasks import CancellationToken, OperationCancelled, ProgressReporter

Operation = Callable[[CancellationToken, ProgressReporter], object]


class _OperationWorker(QObject):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    done = Signal()

    def __init__(self, operation: Operation, token: CancellationToken):
        super().__init__()
        self.operation = operation
        self.token = token

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.token, self.progress.emit)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)
        finally:
            self.done.emit()


class QtOperationJob(QObject):
    """One operation, one worker, and one owned QThread."""

    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, operation: Operation, parent: QObject | None = None):
        super().__init__(parent)
        self.token = CancellationToken()
        self._thread = QThread()
        self._worker = _OperationWorker(operation, self.token)
        self._worker.moveToThread(self._thread)
        self._stopped = False

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.emit)
        self._worker.succeeded.connect(self.succeeded.emit)
        self._worker.failed.connect(self.failed.emit)
        self._worker.cancelled.connect(self.cancelled.emit)
        self._worker.done.connect(self._worker.deleteLater)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_finished)

    @property
    def is_running(self) -> bool:
        return not self._stopped and self._thread.isRunning()

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        # Direct thread-safe flag update: a queued worker slot could not run
        # while the worker is busy indexing the archive.
        self.token.cancel()

    def wait(self, timeout_ms: int | None = None) -> bool:
        if self._stopped:
            return True
        if timeout_ms is None:
            return self._thread.wait()
        return self._thread.wait(timeout_ms)

    def shutdown(self, timeout_ms: int | None = None) -> bool:
        self.cancel()
        # shutdown() may immediately block the GUI thread in wait(), so do not
        # depend on a queued done -> quit delivery back through that thread.
        self._thread.quit()
        return self.wait(timeout_ms)

    @Slot()
    def _thread_finished(self) -> None:
        self._stopped = True
        # Queue the stopped thread before consumers queue deletion of this
        # owning job from ``finished``.  Reversing that order leaves a narrow
        # teardown race when one Qt test/window follows another immediately.
        self._thread.deleteLater()
        self.finished.emit()


__all__ = ["Operation", "QtOperationJob"]
