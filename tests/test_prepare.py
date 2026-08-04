from pathlib import Path

import p4kbuilder as B
import pytest

from starcompanion.ini import BOM, LocalizationFile
from starcompanion.inject import MergeMode, UnconfirmedWriteError
from starcompanion.install import GameInstall
from starcompanion.prepare import BaselineSource, prepare_localization
from starcompanion.transactions import TargetChangedError


STOCK = BOM + "Foo=stock\nUntouched=keep\n"


def make_install(tmp_path: Path) -> GameInstall:
    root = tmp_path / "StarCitizen" / "LIVE"
    root.mkdir(parents=True)
    (root / "Data.p4k").write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", STOCK.encode("utf-8"))
        .add("Data/Localization/french/global.ini", (BOM + "Foo=fr\n").encode("utf-8"))
        .build()
    )
    return GameInstall(root=root, channel="LIVE", version="test-build")


def test_clean_install_prepares_from_archive_without_writing(tmp_path):
    install = make_install(tmp_path)
    with prepare_localization(install) as prepared:

        assert prepared.source is BaselineSource.ARCHIVE
        assert prepared.target == install.localization()
        assert not prepared.target.exists()
        assert prepared.preview({"Foo": "changed"}).updated == ["Foo"]


def test_clean_install_requires_confirmation_then_creates_override(tmp_path):
    install = make_install(tmp_path)
    with prepare_localization(install) as prepared:

        with pytest.raises(UnconfirmedWriteError):
            prepared.commit({"Foo": "changed"}, confirmed=False)
        assert not prepared.target.exists()

        prepared.commit({"Foo": "changed"}, confirmed=True)
    written = LocalizationFile.load(prepared.target)
    assert written.get("Foo") == "changed"
    assert written.get("Untouched") == "keep"
    backups = list((prepared.target.parent / "backups").glob("*.ini"))
    assert len(backups) == 1
    assert LocalizationFile.load(backups[0]).get("Foo") == "stock"


def test_merge_prepares_from_existing_override_without_opening_archive(tmp_path):
    install = make_install(tmp_path)
    install.localization().parent.mkdir(parents=True)
    install.localization().write_text(BOM + "Foo=custom\nUntouched=mine\n", encoding="utf-8")
    install.archive.write_bytes(b"deliberately invalid because it must not be opened")

    with prepare_localization(install, mode=MergeMode.MERGE) as prepared:

        assert prepared.source is BaselineSource.OVERRIDE
        assert prepared.baseline().get("Untouched") == "mine"


def test_prepared_merge_rejects_external_change_before_plan(tmp_path):
    install = make_install(tmp_path)
    target = install.localization()
    target.parent.mkdir(parents=True)
    target.write_text(BOM + "Foo=before\n", encoding="utf-8")
    with prepare_localization(install, mode=MergeMode.MERGE) as prepared:
        target.write_text(BOM + "Foo=external\n", encoding="utf-8")
        with pytest.raises(TargetChangedError, match="during preparation"):
            prepared.operation_plan({"Foo": "ours"})


def test_prepare_rejects_target_change_while_copying(tmp_path, monkeypatch):
    import starcompanion.prepare as preparation

    install = make_install(tmp_path)
    target = install.localization()
    target.parent.mkdir(parents=True)
    target.write_text(BOM + "Foo=before\n", encoding="utf-8")
    original_copy = preparation._copy_override

    def copy_then_change(*args, **kwargs):
        original_copy(*args, **kwargs)
        target.write_text(BOM + "Foo=external\n", encoding="utf-8")

    monkeypatch.setattr(preparation, "_copy_override", copy_then_change)
    with pytest.raises(TargetChangedError, match="being copied"):
        prepare_localization(install, mode=MergeMode.MERGE)


def test_overwrite_prepares_pristine_archive_even_when_override_exists(tmp_path):
    install = make_install(tmp_path)
    install.localization().parent.mkdir(parents=True)
    install.localization().write_text(BOM + "Foo=custom\n", encoding="utf-8")

    with prepare_localization(install, mode=MergeMode.OVERWRITE) as prepared:

        assert prepared.source is BaselineSource.ARCHIVE
        assert prepared.baseline().get("Foo") == "stock"


def test_preparation_reports_available_languages(tmp_path):
    install = make_install(tmp_path)
    with pytest.raises(KeyError, match="english, french"):
        prepare_localization(install, language="german")
