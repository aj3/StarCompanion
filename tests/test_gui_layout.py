"""Qt-independent safety coverage for local GUI layout persistence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import starcompanion.gui.layout as layout_module
from starcompanion.gui.layout import (
    LAYOUT_FILENAME,
    LAYOUT_SCHEMA,
    MAX_COLUMNS,
    MAX_COLUMN_GROUPS,
    MAX_LAYOUT_BYTES,
    MAX_SPLITTERS,
    MAX_SPLITTER_PARTS,
    LayoutError,
    LayoutState,
    LocalLayoutStore,
    WindowGeometry,
    decode_layout,
    encode_layout,
)
from starcompanion.portability import PreferencesStore, plan_settings_export


def state() -> LayoutState:
    return LayoutState(
        WindowGeometry(120, 80, 1280, 800, False),
        {
            "editor.workspace": (680, 360),
            "templates.workspace": (480, 520),
        },
        {
            "editor.table": (240, 90, 220, 220, 240, 170, 100),
            "blueprints.table": (250, 110, 90, 170, 110, 180),
        },
    )


def document() -> dict[str, object]:
    return json.loads(encode_layout(state()))


def write_document(root: Path, value: object) -> Path:
    path = root / LAYOUT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_round_trip_uses_a_separate_local_only_file(tmp_path):
    store = LocalLayoutStore(tmp_path)
    store.save(state())

    loaded = store.load()

    assert store.path == tmp_path / "ui-layout.json"
    assert loaded.state == state()
    assert loaded.warning is None
    assert not loaded.write_blocked
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema"] == LAYOUT_SCHEMA


def test_missing_layout_returns_safe_defaults(tmp_path):
    loaded = LocalLayoutStore(tmp_path).load()

    assert loaded.state == LayoutState()
    assert loaded.warning is None
    assert not loaded.write_blocked


def test_decoder_rejects_schema_shape_and_duplicate_keys():
    invalid = [
        [],
        {},
        {**document(), "schema": LAYOUT_SCHEMA + 1},
        {**document(), "schema": True},
        {**document(), "unexpected": 1},
    ]
    for value in invalid:
        with pytest.raises(LayoutError):
            decode_layout(json.dumps(value).encode("utf-8"))

    duplicate = (
        b'{"schema":1,"schema":1,"window":null,"splitters":{},"columns":{}}'
    )
    with pytest.raises(LayoutError, match="duplicate JSON key"):
        decode_layout(duplicate)


def test_decoder_rejects_excessive_json_nesting_before_object_creation():
    payload = ("[" * 17 + "0" + "]" * 17).encode("ascii")

    with pytest.raises(LayoutError, match="nesting limit"):
        decode_layout(payload)


def test_decoder_strictly_bounds_window_geometry():
    invalid_windows = [
        {},
        {"x": 0, "y": 0, "width": 639, "height": 800, "maximized": False},
        {"x": 0, "y": 0, "width": 1280, "height": 479, "maximized": False},
        {"x": True, "y": 0, "width": 1280, "height": 800, "maximized": False},
        {"x": 0, "y": 0, "width": 1280, "height": 800, "maximized": 1},
        {
            "x": 1_000_001,
            "y": 0,
            "width": 1280,
            "height": 800,
            "maximized": False,
        },
    ]
    for window in invalid_windows:
        value = document()
        value["window"] = window
        with pytest.raises(LayoutError):
            decode_layout(json.dumps(value).encode("utf-8"))


def test_decoder_strictly_bounds_splitter_counts_types_and_sizes():
    invalid_splitters = [
        [],
        {"editor.workspace": [1]},
        {"editor.workspace": [1] * (MAX_SPLITTER_PARTS + 1)},
        {"editor.workspace": [0, 0]},
        {"editor.workspace": [True, 10]},
        {"editor.workspace": [10, 32_769]},
        {"Invalid key": [10, 10]},
        {f"splitter.{index}": [1, 1] for index in range(MAX_SPLITTERS + 1)},
    ]
    for splitters in invalid_splitters:
        value = document()
        value["splitters"] = splitters
        with pytest.raises(LayoutError):
            decode_layout(json.dumps(value).encode("utf-8"))


def test_decoder_strictly_bounds_column_counts_types_and_widths():
    invalid_columns = [
        [],
        {"editor.table": []},
        {"editor.table": [100] * (MAX_COLUMNS + 1)},
        {"editor.table": [True]},
        {"editor.table": [15]},
        {"editor.table": [4_097]},
        {"Invalid key": [100]},
        {f"table.{index}": [100] for index in range(MAX_COLUMN_GROUPS + 1)},
    ]
    for columns in invalid_columns:
        value = document()
        value["columns"] = columns
        with pytest.raises(LayoutError):
            decode_layout(json.dumps(value).encode("utf-8"))


def test_encoder_validates_programmatic_values():
    with pytest.raises(LayoutError):
        encode_layout(LayoutState(splitters={"editor.workspace": (10, True)}))
    with pytest.raises(LayoutError):
        encode_layout(LayoutState(columns={1: (100,)}))  # type: ignore[dict-item]
    with pytest.raises(LayoutError):
        encode_layout(LayoutState(window="1280x800"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        b"{broken",
        b'[{"schema":1}]',
        json.dumps({**document(), "schema": LAYOUT_SCHEMA + 1}).encode("utf-8"),
        b" " * (MAX_LAYOUT_BYTES + 1),
    ],
    ids=("broken-json", "wrong-root", "newer-schema", "oversized"),
)
def test_corrupt_newer_and_oversized_files_fail_safe_without_overwrite(tmp_path, payload):
    store = LocalLayoutStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(payload)

    loaded = store.load()

    assert loaded.state == LayoutState()
    assert loaded.warning
    assert loaded.write_blocked and store.write_blocked
    with pytest.raises(LayoutError, match="refusing to overwrite"):
        store.save(state())
    assert store.path.read_bytes() == payload


def test_save_revalidates_existing_file_even_when_load_was_not_called(tmp_path):
    store = LocalLayoutStore(tmp_path)
    original = b"not-json"
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(original)

    with pytest.raises(LayoutError, match="refusing to overwrite"):
        store.save(state())

    assert store.write_blocked
    assert store.path.read_bytes() == original


def test_explicit_reset_clears_invalid_file_and_unblocks_future_save(tmp_path):
    store = LocalLayoutStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(b"broken")
    assert store.load().write_blocked

    assert store.reset()
    assert not store.path.exists()
    assert not store.write_blocked

    store.save(state())
    assert store.load().state == state()


def test_clear_missing_file_is_a_safe_noop(tmp_path):
    store = LocalLayoutStore(tmp_path)

    assert not store.clear()
    assert not store.write_blocked


def test_non_file_layout_path_is_rejected_and_not_removed(tmp_path):
    store = LocalLayoutStore(tmp_path)
    store.path.mkdir(parents=True)

    loaded = store.load()

    assert loaded.write_blocked
    with pytest.raises(LayoutError, match="ordinary local file"):
        store.clear()
    assert store.path.is_dir()


def test_layout_inspection_failure_is_reported_without_exposing_the_path(
    tmp_path, monkeypatch
):
    store = LocalLayoutStore(tmp_path / "private-player-layout")
    target = store.path
    real_stat = layout_module.os.stat

    def denied(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError(13, "permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(layout_module.os, "stat", denied)
    loaded = store.load()

    assert loaded.write_blocked
    assert "permission denied" in loaded.warning
    assert str(tmp_path) not in loaded.warning


def test_symbolic_link_layout_is_rejected_without_touching_its_target(tmp_path):
    target = tmp_path / "outside.json"
    original = encode_layout(state())
    target.write_bytes(original)
    root = tmp_path / "data"
    root.mkdir()
    store = LocalLayoutStore(root)
    try:
        store.path.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    assert store.load().write_blocked
    with pytest.raises(LayoutError, match="ordinary local file"):
        store.clear()
    assert target.read_bytes() == original
    assert store.path.is_symlink()


def test_failed_atomic_replace_preserves_previous_layout_and_cleans_temp(
    tmp_path, monkeypatch
):
    store = LocalLayoutStore(tmp_path)
    first = state()
    store.save(first)
    original = store.path.read_bytes()
    changed = copy.deepcopy(first)
    changed = LayoutState(
        WindowGeometry(200, 100, 1440, 900, True),
        changed.splitters,
        changed.columns,
    )

    def fail_replace(_source, _destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(layout_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        store.save(changed)

    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".ui-layout.json.*.tmp"))


def test_layout_is_excluded_from_portable_settings_export(tmp_path):
    PreferencesStore(tmp_path).save({"theme": "dark"})
    LocalLayoutStore(tmp_path).save(state())

    export = plan_settings_export(tmp_path)

    assert "preferences.json" in {entry.archive_path for entry in export.entries}
    assert LAYOUT_FILENAME not in {entry.archive_path for entry in export.entries}
