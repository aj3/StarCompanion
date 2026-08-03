import pytest

from starcompanion.ini import BOM, LocalizationFile
from starcompanion.inject import (
    MergeMode,
    UnconfirmedWriteError,
    ValidationFailedError,
    apply,
    backup,
    plan,
    restore,
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


def test_plan_touches_no_files(target):
    before = target.read_bytes()
    plan(LocalizationFile.load(target), {"Foo": "changed"})
    assert target.read_bytes() == before


def test_plan_flags_invalid_values():
    result = plan(LocalizationFile.loads(STOCK), {"Foo": "has a\nreal newline"})
    assert not result.is_valid
    assert result.errors[0][0] == "Foo"


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


def test_writing_nothing_leaves_file_byte_identical(target):
    apply(target, {}, confirmed=True)
    assert target.read_bytes() == STOCK.encode("utf-8")
