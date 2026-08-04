from pathlib import Path

from starcompanion.transactions import TransactionJournal, bytes_sha256, fingerprint


def journal(tmp_path):
    return TransactionJournal(tmp_path / "apply-journal.json", tmp_path / "last.json")


def test_fingerprint_distinguishes_missing_content_and_external_touch(tmp_path):
    target = tmp_path / "global.ini"
    assert not fingerprint(target).exists
    target.write_bytes(b"one")
    first = fingerprint(target)
    target.write_bytes(b"two")
    second = fingerprint(target)
    assert first.sha256 != second.sha256
    assert first != second


def test_started_transaction_with_unchanged_target_recovers_as_not_applied(tmp_path):
    target = tmp_path / "global.ini"
    target.write_bytes(b"before")
    state = journal(tmp_path)
    state.begin(
        operation="apply",
        plan_id="a" * 64,
        target=target,
        before=fingerprint(target),
        after_sha256=bytes_sha256(b"after"),
    )
    report = state.inspect(target, resolve_safe=True)
    assert report.status == "not-applied"
    assert not state.journal_path.exists()


def test_recovery_finalizes_an_atomic_replace_that_completed_before_crash(tmp_path):
    target = tmp_path / "global.ini"
    target.write_bytes(b"before")
    state = journal(tmp_path)
    state.begin(
        operation="apply",
        plan_id="b" * 64,
        target=target,
        before=fingerprint(target),
        after_sha256=bytes_sha256(b"after"),
    )
    target.write_bytes(b"after")
    report = state.inspect(target, resolve_safe=True)
    assert report.status == "applied"
    assert not state.journal_path.exists()
    assert state.last_operation()["stage"] == "complete"


def test_recovery_never_overwrites_an_unrecognized_external_state(tmp_path):
    target = tmp_path / "global.ini"
    target.write_bytes(b"before")
    state = journal(tmp_path)
    state.begin(
        operation="apply",
        plan_id="c" * 64,
        target=target,
        before=fingerprint(target),
        after_sha256=bytes_sha256(b"after"),
    )
    target.write_bytes(b"external")
    report = state.inspect(target, resolve_safe=True)
    assert report.status == "attention"
    assert report.needs_attention
    assert target.read_bytes() == b"external"
    assert state.journal_path.exists()


def test_journal_for_another_target_is_not_followed(tmp_path):
    first = tmp_path / "one.ini"
    second = tmp_path / "two.ini"
    first.write_bytes(b"before")
    second.write_bytes(b"second")
    state = journal(tmp_path)
    state.begin(
        operation="apply",
        plan_id="d" * 64,
        target=first,
        before=fingerprint(first),
        after_sha256=bytes_sha256(b"after"),
    )
    report = state.inspect(second, resolve_safe=True)
    assert report.needs_attention
    assert second.read_bytes() == b"second"
    assert state.journal_path.exists()
