import pytest

from starcompanion.ini import BOM, LocalizationFile
from starcompanion.inject import (
    InjectionPlan,
    MergeMode,
    UnconfirmedWriteError,
    ValidationFailedError,
    apply,
    backup,
    build_operation_plan,
    plan,
    restore,
)
from starcompanion.transactions import (
    TargetChangedError,
    TransactionJournal,
    bytes_sha256,
    fingerprint,
)

STOCK = BOM + "Foo=original\nBar,P=also original\nUntouched=leave me\n"


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "global.ini"
    path.write_bytes(STOCK.encode("utf-8"))
    return path


def test_plan_classifies_each_key():
    f = LocalizationFile.loads(STOCK)
    result = plan(f, {"Foo": "changed", "Bar": "also original", "Missing": "nowhere"})

    assert result.updated == ["Foo"]
    assert result.unchanged == ["Bar"]
    assert result.skipped == ["Missing"]
    assert result.is_valid


def test_plan_and_apply_support_explicit_removals(target, tmp_path):
    preview = plan(
        LocalizationFile.load(target),
        {},
        removals={"Foo", "Missing"},
        allowed_removals={"Foo", "Missing"},
    )
    assert preview.removed == ["Foo"]
    assert preview.unchanged == ["Missing"]

    result = apply(
        target,
        {},
        removals={"Foo"},
        allowed_removals={"Foo"},
        confirmed=True,
        backup_dir=tmp_path / "backups",
    )
    assert result.removed == ["Foo"]
    assert LocalizationFile.load(target).get("Foo") is None


def test_untrusted_key_removal_is_blocked():
    preview = plan(LocalizationFile.loads(STOCK), {}, removals={"Foo"})
    assert not preview.is_valid
    assert preview.removed == []
    assert preview.errors[0][1].code == "unauthorized-removal"


def test_unified_plan_blocks_ambiguous_duplicate_keys():
    duplicate = LocalizationFile.loads(BOM + "Foo=one\nFoo=two\n")
    result, _desired = build_operation_plan(duplicate, duplicate, {"Foo": "new"})
    assert not result.is_valid
    assert any(issue.code == "duplicate-key" for _key, issue in result.errors)


def test_operation_plan_round_trips_and_detects_tampering(target, tmp_path):
    preview = plan(LocalizationFile.load(target), {"Foo": "changed"})
    preview.bind(
        channel="LIVE",
        language="english",
        mode=MergeMode.MERGE,
        baseline_source="override",
        target=target,
        target_fingerprint=fingerprint(target),
        baseline_sha256=bytes_sha256(target.read_bytes()),
        desired_sha256="a" * 64,
        source_report={
            "precedence": ["stock", "generated", "user"],
            "entries": {
                "Foo": {
                    "winner": "user:LIVE:english",
                    "winner_kind": "user",
                    "conflicted": True,
                }
            },
        },
    )
    path = tmp_path / "plan.json"
    preview.save(path)
    loaded = InjectionPlan.load(path)
    assert loaded.plan_id == preview.plan_id
    assert loaded.sources["Foo"]["winner_kind"] == "user"
    assert loaded.summary() == preview.summary()

    data = loaded.to_dict()
    data["outcomes"]["change"].append("tampered")
    with pytest.raises(ValueError, match="identity"):
        InjectionPlan.from_dict(data)


def test_apply_refuses_when_target_changed_after_preview(target):
    expected = fingerprint(target)
    target.write_bytes((BOM + "Foo=external\n").encode("utf-8"))
    with pytest.raises(TargetChangedError, match="changed after preview"):
        apply(
            target,
            {"Foo": "ours"},
            confirmed=True,
            expected_fingerprint=expected,
        )
    assert LocalizationFile.load(target).get("Foo") == "external"


def test_apply_rejects_parent_symlink_swap_for_bound_plan(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    target = install / "global.ini"
    target.write_bytes(STOCK.encode("utf-8"))
    preview = plan(LocalizationFile.load(target), {"Foo": "changed"})
    preview.bind(
        channel="LIVE",
        language="english",
        mode=MergeMode.MERGE,
        baseline_source="override",
        target=target,
        target_fingerprint=fingerprint(target),
        baseline_sha256=bytes_sha256(target.read_bytes()),
        desired_sha256="a" * 64,
    )

    moved = tmp_path / "moved-install"
    install.rename(moved)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "global.ini").write_bytes(STOCK.encode("utf-8"))
    try:
        install.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(TargetChangedError, match="reviewed operation-plan target"):
        apply(target, {"Foo": "changed"}, confirmed=True, operation_plan=preview)
    assert (outside / "global.ini").read_bytes() == STOCK.encode("utf-8")


def test_crash_before_replace_is_recovered_without_touching_target(
    target, tmp_path, monkeypatch
):
    import starcompanion.inject as injection

    state = TransactionJournal(tmp_path / "journal.json", tmp_path / "last.json")
    before = target.read_bytes()

    def fail_save(*_args, **_kwargs):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(injection, "_atomic_save", fail_save)
    with pytest.raises(OSError, match="before replace"):
        apply(
            target,
            {"Foo": "changed"},
            confirmed=True,
            backup_dir=tmp_path / "backups",
            journal=state,
        )
    assert target.read_bytes() == before
    report = state.inspect(target, resolve_safe=True)
    assert report.status == "not-applied"
    assert not state.journal_path.exists()


def test_crash_after_atomic_replace_is_finalized_from_fingerprint(
    target, tmp_path, monkeypatch
):
    state = TransactionJournal(tmp_path / "journal.json", tmp_path / "last.json")

    def crash_after_replace():
        raise OSError("simulated crash after replace")

    monkeypatch.setattr(state, "record_replaced", crash_after_replace)
    with pytest.raises(OSError, match="after replace"):
        apply(
            target,
            {"Foo": "changed"},
            confirmed=True,
            backup_dir=tmp_path / "backups",
            journal=state,
        )
    assert LocalizationFile.load(target).get("Foo") == "changed"
    report = state.inspect(target, resolve_safe=True)
    assert report.status == "applied"
    assert state.last_operation()["stage"] == "complete"


def test_only_explicitly_allowed_missing_keys_can_be_added():
    f = LocalizationFile.loads(BOM + "Foo=original\n")
    result = plan(
        f,
        {"Authored": "my text", "Unknown": "not authorized"},
        allowed_additions={"Authored"},
    )

    assert result.added == ["Authored"]
    assert result.skipped == ["Unknown"]


def test_confirmed_apply_adds_only_authorized_missing_keys(target, tmp_path):
    result = apply(
        target,
        {"Authored": "my text", "Unknown": "not authorized"},
        confirmed=True,
        allowed_additions={"Authored"},
        backup_dir=tmp_path / "backups",
    )

    written = LocalizationFile.load(target)
    assert result.added == ["Authored"]
    assert result.skipped == ["Unknown"]
    assert written.get("Authored") == "my text"
    assert written.get("Unknown") is None


def test_plan_touches_no_files(target):
    before = target.read_bytes()
    plan(LocalizationFile.load(target), {"Foo": "changed"})
    assert target.read_bytes() == before


def test_plan_flags_invalid_values():
    result = plan(LocalizationFile.loads(STOCK), {"Foo": "has a\nreal newline"})
    assert not result.is_valid
    assert result.errors[0][0] == "Foo"


def test_plan_preserves_trusted_stock_placeholder_but_rejects_new_tag():
    source = LocalizationFile.loads(BOM + "Foo=Available in <years>\n")

    preserved = plan(source, {"Foo": "Available in <years> soon"})
    introduced = plan(source, {"Foo": "Available in <script> soon"})

    assert preserved.is_valid
    assert not introduced.is_valid
    assert introduced.errors[0][1].code == "unknown-tag"


def test_apply_requires_confirmation(target):
    with pytest.raises(UnconfirmedWriteError):
        apply(target, {"Foo": "changed"}, confirmed=False)
    assert target.read_bytes() == STOCK.encode("utf-8")


def test_apply_writes_and_preserves_untouched_lines(target):
    apply(target, {"Foo": "changed"}, confirmed=True)

    result = LocalizationFile.load(target)
    assert result.get("Foo") == "changed"
    assert result.get("Untouched") == "leave me"
    assert target.read_bytes().startswith(BOM.encode("utf-8"))


def test_apply_writes_through_plural_suffix(target):
    apply(target, {"Bar": "new value"}, confirmed=True)
    assert LocalizationFile.load(target).get("Bar,P") == "new value"


def test_apply_refuses_invalid_values(target):
    with pytest.raises(ValidationFailedError):
        apply(target, {"Foo": "bad\nvalue"}, confirmed=True)
    assert LocalizationFile.load(target).get("Foo") == "original"


def test_apply_creates_a_backup(target, tmp_path):
    backups = tmp_path / "backups"
    apply(target, {"Foo": "changed"}, confirmed=True, backup_dir=backups)

    saved = list(backups.iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == STOCK.encode("utf-8")


def test_overwrite_mode_discards_prior_edits(target, tmp_path):
    stock = tmp_path / "stock.ini"
    stock.write_bytes(STOCK.encode("utf-8"))
    target.write_bytes((BOM + "Foo=someone elses pack\nBar,P=x\nUntouched=y\n").encode("utf-8"))

    apply(target, {"Bar": "ours"}, confirmed=True, mode=MergeMode.OVERWRITE, stock_path=stock)

    result = LocalizationFile.load(target)
    assert result.get("Foo") == "original"
    assert result.get("Bar") == "ours"


def test_merge_mode_keeps_prior_edits(target):
    target.write_bytes((BOM + "Foo=someone elses pack\nBar,P=x\nUntouched=y\n").encode("utf-8"))

    apply(target, {"Bar": "ours"}, confirmed=True, mode=MergeMode.MERGE)

    result = LocalizationFile.load(target)
    assert result.get("Foo") == "someone elses pack"
    assert result.get("Bar") == "ours"


def test_overwrite_mode_needs_stock_path(target):
    with pytest.raises(ValueError):
        apply(target, {"Foo": "x"}, confirmed=True, mode=MergeMode.OVERWRITE)


def test_restore_reverts_a_write(target, tmp_path):
    backups = tmp_path / "backups"
    saved = backup(target, backups)
    apply(target, {"Foo": "changed"}, confirmed=True, backup_dir=backups)

    restore(saved, target)
    assert target.read_bytes() == STOCK.encode("utf-8")


def test_restore_refuses_changed_target_or_backup(target, tmp_path, monkeypatch):
    import starcompanion.inject as injection

    saved = backup(target, tmp_path / "backups")
    expected_target = fingerprint(target)
    expected_backup = fingerprint(saved)
    target.write_text(BOM + "Foo=external\n", encoding="utf-8")
    with pytest.raises(TargetChangedError, match="target changed"):
        restore(
            saved,
            target,
            expected_backup_fingerprint=expected_backup,
            expected_target_fingerprint=expected_target,
        )
    assert LocalizationFile.load(target).get("Foo") == "external"

    target.write_bytes(STOCK.encode("utf-8"))
    expected_target = fingerprint(target)
    expected_backup = fingerprint(saved)
    real_fingerprint = injection.fingerprint
    backup_calls = 0

    def change_backup_during_read(path):
        nonlocal backup_calls
        if path == saved:
            backup_calls += 1
            if backup_calls == 2:
                saved.write_bytes(b"changed backup")
        return real_fingerprint(path)

    monkeypatch.setattr(injection, "fingerprint", change_backup_during_read)
    with pytest.raises(TargetChangedError, match="while it was being read"):
        restore(
            saved,
            target,
            expected_backup_fingerprint=expected_backup,
            expected_target_fingerprint=expected_target,
        )
    assert target.read_bytes() == STOCK.encode("utf-8")


def test_writing_nothing_leaves_file_byte_identical(target):
    apply(target, {}, confirmed=True)
    assert target.read_bytes() == STOCK.encode("utf-8")


def test_prepared_source_can_create_a_missing_override(tmp_path):
    target = tmp_path / "data" / "Localization" / "english" / "global.ini"
    source = LocalizationFile.loads(STOCK)

    result = apply(target, {"Foo": "changed"}, confirmed=True, source=source)

    assert result.updated == ["Foo"]
    written = LocalizationFile.load(target)
    assert written.get("Foo") == "changed"
    assert written.get("Untouched") == "leave me"
    assert source.get("Foo") == "original"


def test_noop_does_not_create_backup(target, tmp_path):
    backups = tmp_path / "backups"
    apply(target, {"Foo": "original"}, confirmed=True, backup_dir=backups)
    assert not backups.exists()


def test_backup_names_do_not_collide(target, tmp_path, monkeypatch):
    import starcompanion.inject as injection

    class FrozenDateTime:
        @classmethod
        def now(cls):
            return cls()

        def strftime(self, _format):
            return "20260803-120000-000000"

    monkeypatch.setattr(injection, "datetime", FrozenDateTime)
    first = backup(target, tmp_path / "backups")
    second = backup(target, tmp_path / "backups")

    assert first != second
    assert first.read_bytes() == second.read_bytes() == STOCK.encode("utf-8")


def test_failed_atomic_replace_leaves_original_intact(target, tmp_path, monkeypatch):
    import starcompanion.inject as injection

    before = target.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(injection.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        apply(
            target,
            {"Foo": "changed"},
            confirmed=True,
            backup_dir=tmp_path / "backups",
        )

    assert target.read_bytes() == before
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))
