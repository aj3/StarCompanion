import json

import pytest

from starcompanion.gui.event_log import (
    EventLog,
    MAX_DETAIL,
    redact_event_detail,
    write_event_export,
)


def test_event_log_is_bounded_and_filterable():
    log = EventLog(max_events=3)
    for index in range(5):
        log.publish("info" if index % 2 else "warning", "test-event", index)

    assert [item.sequence for item in log.entries()] == [3, 4, 5]
    assert all(item.level == "warning" for item in log.entries("warning"))


def test_event_detail_redacts_identifiers_paths_tokens_and_controls(monkeypatch):
    monkeypatch.setenv("USERNAME", "PrivateUser")
    value = (
        "PrivateUser private@example.com C:\\Users\\PrivateUser\\secret.txt\n"
        "/home/PrivateUser/file 0123456789abcdef0123456789abcdef"
    )
    redacted = redact_event_detail(value)

    assert "PrivateUser" not in redacted
    assert "private@example.com" not in redacted
    assert "secret.txt" not in redacted
    assert "0123456789abcdef" not in redacted
    assert "\n" not in redacted
    assert len(redacted) <= MAX_DETAIL


def test_event_export_has_an_explicit_privacy_manifest_and_is_atomic(tmp_path):
    log = EventLog()
    log.publish("info", "application-started")
    payload = log.export_bytes()
    target = tmp_path / "events.json"
    write_event_export(target, payload)
    document = json.loads(target.read_text(encoding="utf-8"))

    assert document["privacy"]["game_strings"] == "excluded"
    assert document["privacy"]["raw_logs"] == "excluded"
    assert document["events"][0]["event"] == "application-started"
    assert not list(tmp_path.glob("*.tmp"))


def test_event_log_rejects_unbounded_or_untyped_inputs():
    with pytest.raises(ValueError):
        EventLog(max_events=501)
    log = EventLog()
    with pytest.raises(ValueError):
        log.publish("debug", "event")
    with pytest.raises(ValueError):
        log.publish("info", "Invalid event key")
