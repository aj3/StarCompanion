import hashlib
import json
import multiprocessing
import os
import zipfile
from pathlib import Path

import pytest

import starcompanion.portability as portability
from starcompanion.portability import (
    LanguagePackStore,
    PortabilityError,
    PreferencesStore,
    apply_settings_import,
    plan_settings_export,
    plan_settings_import,
    write_settings_archive,
)
from starcompanion.user_edits import UserEditStore


def _crash_settings_restore(archive: str, root: str) -> None:
    import starcompanion.portability as module

    plan = module.plan_settings_import(Path(archive), Path(root))
    real_atomic = module._atomic_bytes
    target_writes = 0

    def crash_on_second_target(path, payload, **kwargs):
        nonlocal target_writes
        path = Path(path)
        if (
            ".settings-restore-recovery" not in path.parts
            and path.name not in {".settings-restore-journal.json"}
        ):
            target_writes += 1
            if target_writes == 2:
                os._exit(92)
        return real_atomic(path, payload, **kwargs)

    module._atomic_bytes = crash_on_second_target
    module.apply_settings_import(plan, replace_existing=True)


def _manifest_record(path, kind, payload, channel=None, language=None):
    return {
        "path": path,
        "kind": kind,
        "channel": channel,
        "language": language,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _raw_archive(path: Path, entries: list[tuple[str, bytes, str, str | None, str | None]]):
    manifest = {
        "schema": 1,
        "application": "StarCompanion",
        "files": [
            _manifest_record(name, kind, payload, channel, language)
            for name, payload, kind, channel, language in entries
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, payload, _kind, _channel, _language in entries:
            archive.writestr(name, payload)
    return path


def test_preferences_and_language_packs_are_strictly_scoped(tmp_path):
    data = tmp_path / "data"
    preferences = PreferencesStore(data)
    preferences.save(
        {
            "default_channel": "ptu",
            "default_language": "German_(Germany)",
            "merge_mode": "merge",
            "theme": "dark",
            "link_live_hotfix": False,
        }
    )
    assert preferences.load()["default_channel"] == "PTU"
    assert preferences.load()["default_language"] == "german_(germany)"
    with pytest.raises(PortabilityError, match="non-portable"):
        preferences.save({"last_install_path": "C:/private/game"})

    live_en = LanguagePackStore("LIVE", "english", data)
    live_fr = LanguagePackStore("LIVE", "french", data)
    ptu_en = LanguagePackStore("PTU", "english", data)
    live_en.save({"Mission_Title": "English local pack"})
    live_fr.save({"Mission_Title": "Paquet français"})
    ptu_en.save({"Mission_Title": "PTU English"})
    assert live_en.load()["Mission_Title"] == "English local pack"
    assert live_fr.load()["Mission_Title"] == "Paquet français"
    assert ptu_en.load()["Mission_Title"] == "PTU English"
    assert len({live_en.path, live_fr.path, ptu_en.path}) == 3


def test_settings_archive_round_trip_is_allowlist_only(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    PreferencesStore(source).save({"default_channel": "LIVE", "theme": "light"})
    UserEditStore("LIVE", "english", root=source).save({"User_Key": "Mine"})
    LanguagePackStore("PTU", "french", source).save({"Pack_Key": "Locale"})
    user_dir = UserEditStore("LIVE", "english", root=source).path.parent
    (user_dir / "history.json").write_text('{"private":"history"}', encoding="utf-8")
    (source / "channels" / "LIVE" / "ownership.json").write_text(
        '{"private":"ownership"}', encoding="utf-8"
    )

    archive = tmp_path / "settings.zip"
    export_plan = plan_settings_export(source)
    assert {entry.kind for entry in export_plan.entries} == {
        "preferences", "user-overrides", "language-pack"
    }
    write_settings_archive(export_plan, archive)
    with zipfile.ZipFile(archive) as opened:
        assert "history.json" not in " ".join(opened.namelist())
        assert "ownership.json" not in " ".join(opened.namelist())

    plan = plan_settings_import(archive, destination)
    assert {item.outcome for item in plan.items} == {"add"}
    assert not destination.exists()
    apply_settings_import(plan)
    assert PreferencesStore(destination).load()["theme"] == "light"
    assert UserEditStore("LIVE", "english", root=destination).load() == {"User_Key": "Mine"}
    assert LanguagePackStore("PTU", "french", destination).load() == {"Pack_Key": "Locale"}


def test_restore_previews_conflicts_and_requires_explicit_replacement(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    UserEditStore("LIVE", "english", root=source).save({"Key": "incoming"})
    UserEditStore("LIVE", "english", root=target).save({"Key": "existing"})
    archive = tmp_path / "settings.zip"
    write_settings_archive(plan_settings_export(source), archive)

    plan = plan_settings_import(archive, target)
    assert [item.outcome for item in plan.items].count("change") == 1
    with pytest.raises(PortabilityError, match="authorize replacement"):
        apply_settings_import(plan)
    assert UserEditStore("LIVE", "english", root=target).load() == {"Key": "existing"}
    apply_settings_import(plan, replace_existing=True)
    assert UserEditStore("LIVE", "english", root=target).load() == {"Key": "incoming"}


def test_restore_detects_archive_and_target_changes_after_preview(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    UserEditStore("LIVE", "english", root=source).save({"Key": "incoming"})
    archive = tmp_path / "settings.zip"
    write_settings_archive(plan_settings_export(source), archive)
    plan = plan_settings_import(archive, target)
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(PortabilityError, match="archive changed"):
        apply_settings_import(plan)

    write_settings_archive(plan_settings_export(source), archive, overwrite=True)
    plan = plan_settings_import(archive, target)
    UserEditStore("LIVE", "english", root=target).save({"Key": "external"})
    with pytest.raises(PortabilityError, match="settings changed"):
        apply_settings_import(plan, replace_existing=True)


def test_restore_rejects_parent_symlink_swap_after_preview(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.mkdir()
    UserEditStore("LIVE", "english", root=source).save({"Key": "incoming"})
    archive = tmp_path / "settings.zip"
    write_settings_archive(plan_settings_export(source), archive)
    plan = plan_settings_import(archive, target)

    moved = tmp_path / "moved-target"
    target.rename(moved)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(PortabilityError, match="symbolic link|junction|data root"):
        apply_settings_import(plan)
    assert not (outside / "channels").exists()


def test_restore_rolls_back_completed_files_on_interrupted_write(monkeypatch, tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    PreferencesStore(source).save({"theme": "dark"})
    UserEditStore("LIVE", "english", root=source).save({"Key": "incoming"})
    PreferencesStore(target).save({"theme": "light"})
    UserEditStore("LIVE", "english", root=target).save({"Key": "existing"})
    archive = tmp_path / "settings.zip"
    write_settings_archive(plan_settings_export(source), archive)
    plan = plan_settings_import(archive, target)
    real_atomic = portability._atomic_bytes
    target_calls = 0

    def interrupted(path, payload, **kwargs):
        nonlocal target_calls
        path = Path(path)
        if (
            ".settings-restore-recovery" not in path.parts
            and path.name != ".settings-restore-journal.json"
        ):
            target_calls += 1
        if target_calls == 2:
            raise OSError("synthetic restore crash")
        return real_atomic(path, payload, **kwargs)

    monkeypatch.setattr(portability, "_atomic_bytes", interrupted)
    with pytest.raises(OSError, match="synthetic restore crash"):
        apply_settings_import(plan, replace_existing=True)

    assert PreferencesStore(target).load()["theme"] == "light"
    assert UserEditStore("LIVE", "english", root=target).load() == {"Key": "existing"}
    assert not (target / ".settings-restore.lock").exists()
    assert not (target / ".settings-restore-journal.json").exists()
    assert not (target / ".settings-restore-recovery").exists()


def test_real_process_crash_is_explicitly_recoverable(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    PreferencesStore(source).save({"theme": "dark"})
    UserEditStore("LIVE", "english", root=source).save({"Key": "incoming"})
    PreferencesStore(target).save({"theme": "light"})
    UserEditStore("LIVE", "english", root=target).save({"Key": "existing"})
    archive = tmp_path / "settings.zip"
    write_settings_archive(plan_settings_export(source), archive)

    process = multiprocessing.get_context("spawn").Process(
        target=_crash_settings_restore, args=(str(archive), str(target))
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 92
    assert portability.settings_recovery_status(target) == "applying"
    with pytest.raises(PortabilityError, match="settings recover"):
        apply_settings_import(
            plan_settings_import(archive, target), replace_existing=True
        )

    assert portability.recover_settings_restore(target) == "rolled-back"
    assert PreferencesStore(target).load()["theme"] == "light"
    assert UserEditStore("LIVE", "english", root=target).load() == {"Key": "existing"}
    assert portability.settings_recovery_status(target) is None
    assert not (target / ".settings-restore-recovery").exists()


@pytest.mark.parametrize(
    "name",
    ["../user.ini", "/absolute.ini", "channels\\LIVE\\english\\user.ini"],
)
def test_hostile_archive_paths_are_rejected(tmp_path, name):
    payload = b"Key=Value\n"
    archive = _raw_archive(
        tmp_path / "hostile.zip",
        [(name, payload, "user-overrides", "LIVE", "english")],
    )
    with pytest.raises(PortabilityError, match="unsafe|allowlisted"):
        plan_settings_import(archive, tmp_path / "data")


def test_duplicate_targets_unknown_fields_and_undeclared_files_are_rejected(tmp_path):
    payload = b"Key=Value\n"
    archive = _raw_archive(
        tmp_path / "duplicate-target.zip",
        [
            ("channels/LIVE/english/user.ini", payload, "user-overrides", "LIVE", "english"),
            ("channels/live/english/user.ini", payload, "user-overrides", "LIVE", "english"),
        ],
    )
    with pytest.raises(PortabilityError, match="multiple files"):
        plan_settings_import(archive, tmp_path / "data")

    archive = _raw_archive(
        tmp_path / "undeclared.zip",
        [("preferences.json", b"{}", "preferences", None, None)],
    )
    with zipfile.ZipFile(archive, "a") as opened:
        opened.writestr("secret.txt", "not declared")
    with pytest.raises(PortabilityError, match="undeclared"):
        plan_settings_import(archive, tmp_path / "data")


def test_bad_zip_crc_is_rejected(tmp_path):
    archive = tmp_path / "crc.zip"
    _raw_archive(
        archive,
        [("preferences.json", b'{"theme":"dark"}', "preferences", None, None)],
    )
    raw = bytearray(archive.read_bytes())
    marker = raw.find(b'{"theme":"dark"}')
    assert marker >= 0
    raw[marker] ^= 1
    archive.write_bytes(raw)
    with pytest.raises(PortabilityError, match="invalid settings archive"):
        plan_settings_import(archive, tmp_path / "data")


def test_language_pack_rejects_silently_ignored_raw_lines(tmp_path):
    from starcompanion.portability import load_language_pack

    path = tmp_path / "pack.ini"
    path.write_text("Key=Value\nhostile raw line\n", encoding="utf-8")
    with pytest.raises(PortabilityError, match="must contain"):
        load_language_pack(path)


def test_zip_bomb_entry_count_and_duplicate_members_are_rejected(tmp_path):
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("preferences.json", b"0" * (1024 * 1024))
    with pytest.raises(PortabilityError, match="compression-ratio"):
        plan_settings_import(bomb, tmp_path / "data")

    excessive = tmp_path / "excessive.zip"
    with zipfile.ZipFile(excessive, "w") as archive:
        for index in range(1001):
            archive.writestr(f"entry-{index}", b"")
    with pytest.raises(PortabilityError, match="entry limit"):
        plan_settings_import(excessive, tmp_path / "data")

    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("preferences.json", "{}")
            archive.writestr("preferences.json", "{}")
    with pytest.raises(PortabilityError, match="duplicate paths"):
        plan_settings_import(duplicate, tmp_path / "data")


def test_duplicate_manifest_json_keys_are_rejected(tmp_path):
    archive = tmp_path / "duplicate-json.zip"
    with zipfile.ZipFile(archive, "w") as opened:
        opened.writestr(
            "manifest.json",
            '{"schema":1,"schema":1,"application":"StarCompanion","files":[]}',
        )
    with pytest.raises(PortabilityError, match="duplicate JSON key"):
        plan_settings_import(archive, tmp_path / "data")


def test_excessively_nested_manifest_is_rejected_before_decoding(tmp_path):
    archive = tmp_path / "deep-json.zip"
    deep = '{"padding":' + "[" * 100 + "0" + "]" * 100 + "," \
        '"schema":1,"application":"StarCompanion","files":[]}'
    with zipfile.ZipFile(archive, "w") as opened:
        opened.writestr("manifest.json", deep)
    with pytest.raises(PortabilityError, match="nesting limit"):
        plan_settings_import(archive, tmp_path / "data")
