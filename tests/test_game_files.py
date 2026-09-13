from pathlib import Path

import pytest

from starcompanion.game_files import (
    GameFileError,
    apply_game_file_plan,
    plan_language_activation,
    plan_restore_stock,
)
from starcompanion.inject import UnconfirmedWriteError
from starcompanion.install import GameInstall
from starcompanion.transactions import TargetChangedError, TransactionJournal, fingerprint


def game(tmp_path: Path) -> GameInstall:
    root = tmp_path / "LIVE"
    root.mkdir()
    (root / "Data.p4k").write_bytes(b"archive")
    return GameInstall(root, "LIVE")


def journal(tmp_path: Path) -> TransactionJournal:
    return TransactionJournal(tmp_path / "journal.json", tmp_path / "last.json")


def test_language_plan_preserves_utf8_bom_crlf_comments_and_unrelated_lines(tmp_path):
    install = game(tmp_path)
    install.user_cfg.write_bytes(
        b"\xef\xbb\xbfcon_debug = 1\r\ng_language = english ; keep\r\n"
    )

    plan = plan_language_activation(install, "german_(germany)")

    assert plan.encoding == "utf-8-bom"
    assert plan.newline == "CRLF"
    assert plan.after == (
        b"\xef\xbb\xbfcon_debug = 1\r\n"
        b"g_language = german_(germany) ; keep\r\n"
    )


def test_language_plan_changes_only_the_last_effective_assignment(tmp_path):
    install = game(tmp_path)
    install.user_cfg.write_bytes(
        b"g_language = french\nfoo = 1\ng_language=english # active\n"
    )
    plan = plan_language_activation(install, "spanish_(latin_america)")

    assert plan.after.decode("utf-8") == (
        "g_language = french\nfoo = 1\n"
        "g_language=spanish_(latin_america) # active\n"
    )


def test_language_apply_is_confirmed_backed_up_atomic_and_journaled(tmp_path):
    install = game(tmp_path)
    install.user_cfg.write_bytes(b"foo = 1\r\n")
    plan = plan_language_activation(install, "english")
    state = journal(tmp_path)

    with pytest.raises(UnconfirmedWriteError):
        apply_game_file_plan(
            plan, confirmed=False, backup_dir=tmp_path / "backups", journal=state
        )
    result = apply_game_file_plan(
        plan, confirmed=True, backup_dir=tmp_path / "backups", journal=state
    )

    assert result.backup is not None
    assert result.backup.read_bytes() == b"foo = 1\r\n"
    assert install.user_cfg.read_bytes() == b"foo = 1\r\ng_language = english\r\n"
    assert state.last_operation()["operation"] == "activate-language"


def test_external_change_after_preview_refuses_language_write(tmp_path):
    install = game(tmp_path)
    install.user_cfg.write_text("g_language = english\n", encoding="utf-8")
    plan = plan_language_activation(install, "french")
    install.user_cfg.write_text("external = 1\n", encoding="utf-8")

    with pytest.raises(TargetChangedError):
        apply_game_file_plan(
            plan,
            confirmed=True,
            backup_dir=tmp_path / "backups",
            journal=journal(tmp_path),
        )


def test_restore_stock_removes_only_override_after_backup(tmp_path):
    install = game(tmp_path)
    target = install.localization("english")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"custom")
    plan = plan_restore_stock(install, "english")
    state = journal(tmp_path)
    result = apply_game_file_plan(
        plan, confirmed=True, backup_dir=tmp_path / "backups", journal=state
    )

    assert not target.exists()
    assert result.backup is not None and result.backup.read_bytes() == b"custom"
    assert not result.final.exists
    assert state.last_operation()["operation"] == "restore-stock"


def test_missing_override_restore_is_a_noop(tmp_path):
    install = game(tmp_path)
    plan = plan_restore_stock(install, "english")
    result = apply_game_file_plan(
        plan,
        confirmed=True,
        backup_dir=tmp_path / "backups",
        journal=journal(tmp_path),
    )
    assert not plan.changed and result.backup is None
    assert not (tmp_path / "backups").exists()


def test_restore_stock_rejects_redirected_language_directory(tmp_path):
    install = game(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    localization = install.root / "data" / "Localization"
    localization.mkdir(parents=True)
    try:
        (localization / "english").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this Windows host")

    with pytest.raises(GameFileError, match="link or junction"):
        plan_restore_stock(install, "english")


def test_non_utf_user_cfg_fails_closed(tmp_path):
    install = game(tmp_path)
    install.user_cfg.write_bytes(b"\x81")
    with pytest.raises(GameFileError, match="encoding"):
        plan_language_activation(install, "english")


def test_journal_recognizes_crash_after_intentional_removal(tmp_path):
    install = game(tmp_path)
    target = install.localization("english")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"custom")
    state = journal(tmp_path)
    before = fingerprint(target)
    state.begin(
        operation="restore-stock",
        plan_id="a" * 64,
        target=target,
        before=before,
        after_sha256=None,
    )
    target.unlink()

    report = state.inspect(target, resolve_safe=True)
    assert report.status == "applied"
    assert not state.journal_path.exists()
