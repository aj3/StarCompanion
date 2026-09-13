from pathlib import Path

import pytest

from starcompanion import store
from starcompanion.data_location import (
    DataLocationError,
    apply_data_migration,
    plan_data_migration,
    portable_data_root,
    resolve_data_location,
    synchronized_root_warning,
)


def test_environment_root_has_priority_and_is_validated(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    monkeypatch.setenv("STARCOMPANION_DATA", str(selected))
    assert resolve_data_location().root == selected
    assert resolve_data_location().mode == "environment"


def test_nondefault_data_root_keeps_cache_with_portable_state(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    monkeypatch.setenv("STARCOMPANION_DATA", str(selected))
    monkeypatch.delenv("STARCOMPANION_CACHE", raising=False)
    assert store.cache_dir() == selected / "cache"


def test_portable_marker_selects_beside_executable_root(tmp_path, monkeypatch):
    monkeypatch.delenv("STARCOMPANION_DATA", raising=False)
    executable = tmp_path / "app" / "StarCompanion.exe"
    executable.parent.mkdir()
    (executable.parent / ".starcompanion-portable").write_bytes(
        b"StarCompanion portable data v1\n"
    )
    location = resolve_data_location(executable)
    assert location.mode == "portable"
    assert location.root == executable.parent / "StarCompanionData"


def test_invalid_portable_marker_falls_back_with_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("STARCOMPANION_DATA", raising=False)
    executable = tmp_path / "app" / "StarCompanion.exe"
    executable.parent.mkdir()
    (executable.parent / ".starcompanion-portable").write_text("hostile")
    location = resolve_data_location(executable)
    assert location.mode == "default"
    assert location.warning


def test_migration_is_allowlisted_bounded_copy_only_and_conflict_safe(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "channels" / "LIVE" / "english").mkdir(parents=True)
    (source / "preferences.json").write_text("{}\n")
    (source / "channels" / "LIVE" / "english" / "user.ini").write_text("a=b\n")
    (source / "cache.bin").write_bytes(b"excluded")
    plan = plan_data_migration(source, destination)
    assert {item.relative_path for item in plan.entries} == {
        "preferences.json",
        "channels/LIVE/english/user.ini",
    }
    with pytest.raises(DataLocationError, match="confirmation"):
        apply_data_migration(plan, confirmed=False)


def test_portable_migration_copies_without_deleting_source(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    source = tmp_path / "source"
    (source / "channels" / "LIVE" / "english").mkdir(parents=True)
    user = source / "channels" / "LIVE" / "english" / "user.ini"
    user.write_bytes(b"a=b\n")
    executable = tmp_path / "app" / "StarCompanion.exe"
    executable.parent.mkdir()
    destination = portable_data_root(executable)
    plan = plan_data_migration(
        source, destination, mode="portable", executable=executable
    )
    apply_data_migration(plan, confirmed=True)
    assert user.read_bytes() == b"a=b\n"
    assert (destination / "channels" / "LIVE" / "english" / "user.ini").read_bytes() == b"a=b\n"
    assert (executable.parent / ".starcompanion-portable").is_file()


def test_custom_migration_activates_only_after_all_files_are_copied(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("STARCOMPANION_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "preferences.json").write_text('{"theme": "dark"}\n')

    plan = plan_data_migration(source, destination, mode="custom")
    apply_data_migration(plan, confirmed=True)

    assert (destination / "preferences.json").is_file()
    assert (source / "preferences.json").is_file()
    location = resolve_data_location()
    assert location.mode == "custom"
    assert location.root == destination


def test_changed_source_after_preview_refuses_migration(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    file = source / "preferences.json"
    file.write_text("{}\n")
    plan = plan_data_migration(source, destination)
    file.write_text('{"changed": true}\n')
    with pytest.raises(DataLocationError, match="changed after preview"):
        apply_data_migration(plan, confirmed=True)


def test_destination_link_inserted_after_preview_is_rejected(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    (source / "channels" / "LIVE" / "english").mkdir(parents=True)
    (source / "channels" / "LIVE" / "english" / "user.ini").write_text("a=b\n")
    outside.mkdir()
    plan = plan_data_migration(source, destination)
    (destination / "channels").mkdir(parents=True)
    try:
        (destination / "channels" / "LIVE").symlink_to(
            outside, target_is_directory=True
        )
    except OSError:
        pytest.skip("directory symlinks are not available on this Windows host")

    with pytest.raises(DataLocationError, match="link"):
        apply_data_migration(plan, confirmed=True)
    assert not (outside / "english" / "user.ini").exists()


def test_activation_file_changed_during_copy_refuses_final_switch(
    tmp_path, monkeypatch
):
    import starcompanion.data_location as data_location

    monkeypatch.delenv("STARCOMPANION_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "preferences.json").write_text("{}\n")
    plan = plan_data_migration(source, destination)
    original_write = data_location._atomic_bytes

    def change_activation_after_copy(path, payload, *, root):
        original_write(path, payload, root=root)
        if Path(path) == destination / "preferences.json":
            plan.activation_path.parent.mkdir(parents=True, exist_ok=True)
            plan.activation_path.write_text(
                '{"schema_version": 1, "mode": "custom", '
                f'"root": "{source.as_posix()}"}}\n',
                encoding="utf-8",
            )

    monkeypatch.setattr(data_location, "_atomic_bytes", change_activation_after_copy)

    with pytest.raises(DataLocationError, match="activation file changed"):
        apply_data_migration(plan, confirmed=True)
    assert resolve_data_location().root == source


def test_sync_roots_are_warned_about():
    assert "synchronized" in synchronized_root_warning(Path("C:/Users/A/OneDrive/Data"))
