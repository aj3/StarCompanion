import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.gui.jobs import QtOperationJob


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def spin_until(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()
    assert predicate(), "Qt job did not finish before timeout"


def test_job_delivers_result_and_keeps_event_loop_responsive(qapp):
    results = []
    ticks = []

    def operation(token, reporter):
        for _ in range(10):
            token.checkpoint()
            time.sleep(0.005)
        return 42

    job = QtOperationJob(operation)
    job.succeeded.connect(results.append)
    QTimer.singleShot(0, lambda: ticks.append("tick"))
    job.start()

    spin_until(qapp, lambda: not job.is_running and bool(results))
    assert results == [42]
    assert ticks == ["tick"]


def test_cancel_reaches_running_worker_and_joins_cleanly(qapp):
    cancelled = []

    def operation(token, reporter):
        while True:
            token.checkpoint()
            time.sleep(0.001)

    job = QtOperationJob(operation)
    job.cancelled.connect(lambda: cancelled.append(True))
    job.start()
    QTimer.singleShot(10, job.cancel)

    spin_until(qapp, lambda: not job.is_running and bool(cancelled))
    assert job.wait(100)


def test_shutdown_cancels_and_waits_without_terminating_thread(qapp):
    def operation(token, reporter):
        while True:
            token.checkpoint()
            time.sleep(0.001)

    job = QtOperationJob(operation)
    job.start()

    assert job.shutdown(1000)
    qapp.processEvents()
    assert not job.is_running
