import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from starcompanion.ini import BOM
from starcompanion.user_edits import (
    ConflictChoice,
    EditCommand,
    EditSession,
    EmptyUserModelError,
    NothingToRedoError,
    NothingToUndoError,
    KeyResolution,
    ReconciliationChoice,
    UserEditError,
    UserEditStore,
    load_ini,
    plan_import,
    user_ini_path,
)


def store(tmp_path, channel="LIVE", language="english"):
    return UserEditStore(channel, language, root=tmp_path / "data")


def test_user_ini_is_scoped_by_channel_and_language(tmp_path):
    live = store(tmp_path, "LIVE", "english")
    ptu = store(tmp_path, "PTU", "english")
    french = store(tmp_path, "LIVE", "french_(france)")

    live.save({"Key": "live"})
    ptu.save({"Key": "ptu"})

    assert live.load() == {"Key": "live"}
    assert ptu.load() == {"Key": "ptu"}
    assert french.load() == {}
    assert live.path != ptu.path != french.path
    with pytest.raises(ValueError, match="unsupported game channel"):
        store(tmp_path, "PRIVATE", "english").load()


def test_scope_rejects_traversal_and_absolute_components(tmp_path):
    for channel, language in (("../LIVE", "english"), ("LIVE", "../english")):
        with pytest.raises(UserEditError):
            user_ini_path(channel, language, root=tmp_path)


def test_save_is_canonical_bom_lf_sorted_and_only_contains_user_values(tmp_path):
    target = store(tmp_path)
    target.save({"Zulu": "last", "Alpha": r"first\nsecond"})
    assert target.path.read_bytes().decode("utf-8") == (
        BOM + r"Alpha=first\nsecond" + "\nZulu=last\n"
    )


def test_save_rejects_malformed_keys_and_values(tmp_path):
    target = store(tmp_path)
    with pytest.raises(UserEditError):
        target.save({"Bad=Key": "value"})
    with pytest.raises(UserEditError):
        target.save({"Key": "real\nnewline"})


def test_context_dependent_cig_placeholder_is_preserved_for_later_stock_validation(
    tmp_path,
):
    target = store(tmp_path)
    target.save({"Key": "Wait <years>"})
    assert target.load() == {"Key": "Wait <years>"}


def test_unexpected_empty_model_cannot_erase_existing_edits(tmp_path):
    target = store(tmp_path)
    target.save({"Key": "safe"})
    before = target.path.read_bytes()
    with pytest.raises(EmptyUserModelError):
        target.save({})
    assert target.path.read_bytes() == before


def test_set_remove_undo_redo_persist_across_sessions(tmp_path):
    target = store(tmp_path)
    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Key", "one"))
    session.execute(EditCommand.set(session.values, "Key", "two"))
    assert target.load() == {"Key": "two"}

    reopened = EditSession(target)
    assert reopened.can_undo
    assert reopened.undo().label == "set Key"
    assert target.load() == {"Key": "one"}
    assert EditSession(target).redo().label == "set Key"
    assert target.load() == {"Key": "two"}

    reopened = EditSession(target)
    reopened.execute(EditCommand.remove(reopened.values, "Key"), allow_empty=True)
    assert target.load() == {}
    reopened.undo()
    assert target.load() == {"Key": "two"}


def test_new_command_after_undo_discards_redo_branch(tmp_path):
    session = EditSession(store(tmp_path))
    session.execute(EditCommand.set(session.values, "A", "1"))
    session.execute(EditCommand.set(session.values, "B", "2"))
    session.undo()
    session.execute(EditCommand.set(session.values, "C", "3"))
    assert not session.can_redo
    assert session.values == {"A": "1", "C": "3"}


def test_undo_redo_empty_states_are_explicit(tmp_path):
    session = EditSession(store(tmp_path))
    with pytest.raises(NothingToUndoError):
        session.undo()
    session.execute(EditCommand.set(session.values, "A", "1"))
    with pytest.raises(NothingToRedoError):
        session.redo()


def test_stale_or_damaged_history_never_overwrites_user_ini(tmp_path):
    target = store(tmp_path)
    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Key", "journal value"))
    target.save({"Key": "external edit"})

    reopened = EditSession(target)
    assert reopened.values == {"Key": "external edit"}
    assert not reopened.history_recovered
    assert not reopened.can_undo

    target.history_path.write_text("not json", encoding="utf-8")
    reopened = EditSession(target)
    assert reopened.values == {"Key": "external edit"}
    assert not reopened.can_undo


def test_incoherent_history_with_matching_digest_is_discarded(tmp_path):
    target = store(tmp_path)
    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Key", "safe"))
    data = json.loads(target.history_path.read_text(encoding="utf-8"))
    data["commands"][0]["changes"][0]["after"] = "different"
    target.history_path.write_text(json.dumps(data), encoding="utf-8")

    reopened = EditSession(target)
    assert reopened.values == {"Key": "safe"}
    assert not reopened.history_recovered
    assert not reopened.can_undo


def test_external_edit_session_cannot_overwrite_newer_values(tmp_path):
    target = store(tmp_path)
    first = EditSession(target)
    second = EditSession(target)
    first.execute(EditCommand.set(first.values, "A", "first"))
    with pytest.raises(UserEditError, match="changed outside"):
        second.execute(EditCommand.set(second.values, "B", "stale"))
    assert target.load() == {"A": "first"}


def test_history_write_failure_keeps_live_session_aligned_with_committed_ini(
    tmp_path, monkeypatch
):
    target = store(tmp_path)
    session = EditSession(target)
    real_write = __import__(
        "starcompanion.user_edits", fromlist=["_atomic_write"]
    )._atomic_write

    def fail_history(path, text):
        if path.name == "history.json":
            raise OSError("simulated journal failure")
        return real_write(path, text)

    monkeypatch.setattr("starcompanion.user_edits._atomic_write", fail_history)
    with pytest.raises(OSError, match="journal"):
        session.execute(EditCommand.set(session.values, "Key", "committed"))
    assert target.load() == {"Key": "committed"}
    assert session.values == {"Key": "committed"}
    assert not session.can_undo


@pytest.mark.parametrize(
    ("choice", "expected", "change_count"),
    [
        (ConflictChoice.ERROR, "old", 1),
        (ConflictChoice.KEEP, "old", 1),
        (ConflictChoice.INCOMING, "new", 2),
    ],
)
def test_import_plan_tracks_conflicts_and_explicit_choices(
    tmp_path, choice, expected, change_count
):
    current = {"Conflict": "old", "Same": "same"}
    incoming = {"Conflict": "new", "Same": "same", "Added": "value"}
    plan = plan_import(current, incoming, choice=choice)
    assert plan.conflicts == ("Conflict",)
    assert plan.unchanged == ("Same",)
    assert len(plan.changes) == change_count

    if choice is ConflictChoice.ERROR:
        with pytest.raises(UserEditError):
            EditSession(store(tmp_path)).import_plan(plan)
        return

    target = store(tmp_path)
    target.save(current)
    session = EditSession(target)
    session.import_plan(plan)
    assert session.values["Conflict"] == expected
    assert session.values["Added"] == "value"
    session.undo()
    assert session.values == current


def test_import_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "duplicate.ini"
    path.write_text(BOM + "Key=one\nKey=two\n", encoding="utf-8")
    with pytest.raises(UserEditError, match="duplicate"):
        load_ini(path)


@pytest.mark.parametrize(
    ("choice", "custom", "expected", "origin"),
    [
        (ReconciliationChoice.KEEP, None, "current", "authored"),
        (ReconciliationChoice.IMPORT, None, "incoming", "imported"),
        (ReconciliationChoice.APPEND, None, "currentincoming", "derived"),
        (ReconciliationChoice.PREPEND, None, "incomingcurrent", "derived"),
        (ReconciliationChoice.CUSTOM, "reviewed", "reviewed", "authored"),
    ],
)
def test_per_key_reconciliation_is_explicit_and_tracks_origin(
    tmp_path, choice, custom, expected, origin
):
    target = store(tmp_path)
    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Conflict", "current"))
    plan = plan_import(
        session.values,
        {"Conflict": "incoming"},
        resolutions={"Conflict": KeyResolution(choice, custom)},
    )
    assert not plan.unresolved_conflicts
    session.import_plan(plan)
    assert session.values["Conflict"] == expected
    assert session.origins["Conflict"] == origin


def test_per_key_reconciliation_rejects_unresolved_typo_and_invalid_custom(tmp_path):
    current = {"Conflict": "current"}
    incoming = {"Conflict": "incoming"}
    unresolved = plan_import(current, incoming)
    assert unresolved.unresolved_conflicts == ("Conflict",)
    with pytest.raises(UserEditError, match="unresolved conflicts"):
        EditSession(store(tmp_path)).import_plan(unresolved)
    with pytest.raises(UserEditError, match="non-conflicting"):
        plan_import(
            current,
            incoming,
            resolutions={"Typo": KeyResolution(ReconciliationChoice.KEEP)},
        )
    with pytest.raises(UserEditError, match="requires an explicit value"):
        plan_import(
            current,
            incoming,
            resolutions={"Conflict": KeyResolution(ReconciliationChoice.CUSTOM)},
        )


def test_user_ini_snapshots_are_capped_and_noop_saves_do_not_create_one(tmp_path):
    target = UserEditStore("LIVE", root=tmp_path / "data", snapshot_retention=2)
    target.save({"Key": "one"})
    target.save({"Key": "one"})
    assert not target.snapshots()
    target.save({"Key": "two"})
    target.save({"Key": "three"})
    target.save({"Key": "four"})
    assert len(target.snapshots()) == 2
    assert [load_ini(path)["Key"] for path in target.snapshots()] == ["three", "two"]


def test_same_timestamp_user_snapshots_never_overwrite_each_other(
    tmp_path,
    monkeypatch,
):
    class FrozenDateTime:
        @staticmethod
        def now(_timezone):
            return datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

    nonces = iter(("1" * 32, "2" * 32))
    monkeypatch.setattr("starcompanion.user_edits.datetime", FrozenDateTime)
    monkeypatch.setattr(
        "starcompanion.user_edits.secrets.token_hex",
        lambda _length: next(nonces),
    )
    target = UserEditStore("LIVE", root=tmp_path / "data", snapshot_retention=3)
    target.save({"Key": "one"})
    target.save({"Key": "two"})
    target.save({"Key": "three"})

    snapshots = target.snapshots()
    assert len(snapshots) == 2
    assert snapshots[0].name != snapshots[1].name
    assert {load_ini(path)["Key"] for path in snapshots} == {"one", "two"}


def test_snapshot_cleanup_failure_does_not_turn_verified_save_into_false_failure(
    tmp_path,
    monkeypatch,
):
    target = UserEditStore("LIVE", root=tmp_path / "data", snapshot_retention=1)
    target.save({"Key": "one"})
    target.save({"Key": "two"})
    original_unlink = Path.unlink

    def deny_snapshot_cleanup(path, *args, **kwargs):
        if (
            path.parent == target.snapshot_dir
            and path.name.startswith("user.")
            and path.suffix == ".ini"
        ):
            raise PermissionError(13, "permission denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_snapshot_cleanup)

    target.save({"Key": "three"})

    assert target.load() == {"Key": "three"}
    assert "cleanup was skipped" in target.last_snapshot_warning


def test_origin_damage_and_digest_mismatch_fail_closed(tmp_path):
    target = store(tmp_path)
    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Key", "authored"))
    assert target.load_origins() == {"Key": "authored"}

    target.origins_path.write_text(
        target.origins_path.read_text(encoding="utf-8").replace(
            '"origins": {', '"origins": {}, "origins": {'
        ),
        encoding="utf-8",
    )
    assert target.load_origins() == {}

    session = EditSession(target)
    session.execute(EditCommand.set(session.values, "Key", "authored again"))
    target.path.write_text(BOM + "Key=external\n", encoding="utf-8")
    assert target.load_origins() == {}


def test_origin_write_failure_leaves_user_ini_unchanged(tmp_path, monkeypatch):
    target = store(tmp_path)
    target.save({"Key": "before"}, origins={"Key": "authored"})
    before = target.path.read_bytes()
    original_write = __import__(
        "starcompanion.user_edits", fromlist=["_atomic_write"]
    )._atomic_write

    def fail_origins(path, text):
        if path == target.origins_path:
            raise OSError("simulated origin write failure")
        return original_write(path, text)

    monkeypatch.setattr("starcompanion.user_edits._atomic_write", fail_origins)

    with pytest.raises(OSError, match="origin write failure"):
        target.save({"Key": "after"}, origins={"Key": "authored"})

    assert target.path.read_bytes() == before
    assert target.load() == {"Key": "before"}
    assert target.load_origins() == {"Key": "authored"}


def test_export_is_atomic_and_round_trips(tmp_path):
    target = store(tmp_path)
    target.save({"Key": "value"})
    exported = tmp_path / "outside" / "portable.ini"
    target.export(exported)
    assert load_ini(exported) == {"Key": "value"}


def test_failed_atomic_replace_keeps_previous_user_ini(tmp_path, monkeypatch):
    target = store(tmp_path)
    target.save({"Key": "before"})
    before = target.path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("starcompanion.user_edits.os.replace", fail_replace)
    with pytest.raises(OSError):
        target.save({"Key": "after"})
    assert target.path.read_bytes() == before
    assert not list(target.path.parent.glob(".user.ini.*.tmp"))


def test_storage_size_limit_is_checked_before_replacing_user_ini(
    tmp_path, monkeypatch
):
    target = store(tmp_path)
    target.save({"Key": "before"})
    before = target.path.read_bytes()
    monkeypatch.setattr("starcompanion.user_edits.MAX_FILE_BYTES", 8)
    with pytest.raises(UserEditError, match="storage limit"):
        target.save({"Key": "a longer replacement"})
    assert target.path.read_bytes() == before


def test_oversize_history_is_safely_trimmed_without_losing_user_value(
    tmp_path, monkeypatch
):
    target = store(tmp_path)
    session = EditSession(target)
    monkeypatch.setattr("starcompanion.user_edits.MAX_HISTORY_FILE_BYTES", 200)
    session.execute(EditCommand.set(session.values, "Key", "value" * 30))
    assert target.load() == {"Key": "value" * 30}
    assert not session.can_undo
    assert target.history_path.stat().st_size <= 200
