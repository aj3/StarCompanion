"""Qt integration coverage for bounded machine-local layout state."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.gui import app as app_module
from starcompanion.gui.app import MainWindow
from starcompanion.gui.layout import (
    LayoutError,
    LayoutLoad,
    LayoutState,
    WindowGeometry,
)
from starcompanion.gui.preferences import PreferenceLoad, UiPreferences


@pytest.fixture(scope="session")
def qapp_layout():
    return QApplication.instance() or QApplication([])


class MemoryPreferences:
    def __init__(self):
        self.preferences = UiPreferences()
        self.saved = []

    def load(self, **_kwargs):
        return PreferenceLoad(self.preferences)

    def save(self, preferences, **_kwargs):
        self.saved.append(preferences)


class MemoryLayoutStore:
    def __init__(self, loaded: LayoutLoad, *, save_error: Exception | None = None):
        self.loaded = loaded
        self.save_error = save_error
        self.saved = []
        self.reset_count = 0

    def load(self):
        return self.loaded

    def save(self, state):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(state)

    def reset(self):
        self.reset_count += 1
        self.save_error = None
        return True


def saved_layout() -> LayoutState:
    return LayoutState(
        WindowGeometry(0, 0, 1400, 900, False),
        {
            "editor.workspace": (700, 300),
            "templates.workspace": (500, 400),
            "apply.workspace": (600, 300),
        },
        {
            "editor.table": (250, 100, 230, 240, 260, 180, 110),
            "blueprints.table": (
                250,
                110,
                115,
                120,
                80,
                85,
                230,
                95,
                170,
                105,
                155,
            ),
        },
    )


def test_window_restores_geometry_columns_and_preserves_unopened_splitters(
    qapp_layout,
):
    layout = saved_layout()
    store = MemoryLayoutStore(LayoutLoad(layout))
    window = MainWindow(
        ui_preferences_store=MemoryPreferences(),
        layout_store=store,
    )
    try:
        assert (window.width(), window.height()) == (1400, 900)
        assert tuple(
            window.editor.table.columnWidth(column)
            for column in range(window.editor.table.model().columnCount())
        ) == layout.columns["editor.table"]
        assert tuple(
            window.blueprints.table.columnWidth(column)
            for column in range(window.blueprints.table.model().columnCount())
        ) == layout.columns["blueprints.table"]

        # Hidden stacked pages have no stable extent. Their validated sizes
        # stay pending and close-time capture must not replace them with defaults.
        assert window._capture_layout().splitters == layout.splitters
    finally:
        window.close()
    assert store.saved


def test_splitter_restore_is_deferred_until_its_page_is_visible(qapp_layout):
    layout = saved_layout()
    window = MainWindow(
        ui_preferences_store=MemoryPreferences(),
        layout_store=MemoryLayoutStore(LayoutLoad(layout)),
    )
    try:
        window.show()
        qapp_layout.processEvents()
        assert "templates.workspace" in window._pending_splitter_layout

        window.shell.set_current_key("templates")
        qapp_layout.processEvents()

        assert "templates.workspace" not in window._pending_splitter_layout
        assert any(window.templates.splitter.sizes())
    finally:
        window.close()


def test_offscreen_saved_position_is_ignored_but_bounded_size_is_restored(
    qapp_layout,
):
    layout = saved_layout()
    layout = LayoutState(
        WindowGeometry(900_000, 900_000, 1360, 820, False),
        layout.splitters,
        layout.columns,
    )
    window = MainWindow(
        ui_preferences_store=MemoryPreferences(),
        layout_store=MemoryLayoutStore(LayoutLoad(layout)),
    )
    try:
        assert (window.width(), window.height()) == (1360, 820)
        assert window.x() != 900_000
        assert window.y() != 900_000
    finally:
        window.close()


def test_invalid_layout_warning_blocks_save_until_explicit_reset(
    qapp_layout,
    monkeypatch,
):
    store = MemoryLayoutStore(
        LayoutLoad(
            LayoutState(),
            "Window layout could not be loaded: invalid JSON.",
            True,
        ),
        save_error=LayoutError("refusing to overwrite an invalid layout"),
    )
    preferences = MemoryPreferences()
    window = MainWindow(
        ui_preferences_store=preferences,
        layout_store=store,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: app_module.QMessageBox.StandardButton.Yes,
    )
    try:
        assert "invalid JSON" in window.start.preference_warning.text()
        window._save_layout()
        assert "could not be saved" in window.start.preference_warning.text()
        assert not store.saved

        window.editor.table.setColumnWidth(0, 600)
        window.reset_window_layout()

        assert store.reset_count == 1
        assert not window.start.preference_warning.isVisible()
        assert window.editor.table.columnWidth(0) == window.editor.DEFAULT_COLUMN_WIDTHS[0]
        assert preferences.preferences == UiPreferences()
    finally:
        window.close()
