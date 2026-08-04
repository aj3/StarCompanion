import pytest

from starcompanion.tasks import (
    CancellationToken,
    OperationCancelled,
    OperationStage,
    ProgressEvent,
)


def test_cancellation_is_thread_safe_and_cooperative():
    token = CancellationToken()
    token.checkpoint()
    token.cancel()

    assert token.is_cancelled
    with pytest.raises(OperationCancelled):
        token.checkpoint()


def test_progress_fraction_tracks_central_directory_position():
    start = ProgressEvent(OperationStage.INDEX_ARCHIVE, "index", 0, 100)
    middle = ProgressEvent(OperationStage.INDEX_ARCHIVE, "index", 50, 100)
    end = ProgressEvent(OperationStage.INDEX_ARCHIVE, "index", 100, 100)

    assert 0 <= start.fraction < middle.fraction < end.fraction < 1
    assert ProgressEvent(OperationStage.COMPLETE, "done", 1, 1).fraction == 1
