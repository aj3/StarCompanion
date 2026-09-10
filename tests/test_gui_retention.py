"""Focused GUI coverage for bounded localization backup retention."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.config import Profile
from starcompanion.gui.state import AppState
from starcompanion.gui.tabs.apply import ApplyTab
from starcompanion.gui.tabs.editor import (
    AdvancedStringEditorTab,
    PersistentEditorSnapshot,
)
from starcompanion.inject import DEFAULT_BACKUP_RETENTION, MAX_BACKUP_RETENTION


@pytest.fixture(scope="session")
def qapp_retention():
    return QApplication.instance() or QApplication([])


def test_backup_retention_control_is_bounded_accessible_and_profile_backed(
    qapp_retention,
):
    state = AppState()
    tab = ApplyTab(state)
    changes = []
    state.profileChanged.connect(lambda: changes.append(True))
    try:
        assert tab.backup_retention.minimum() == 1
        assert tab.backup_retention.maximum() == MAX_BACKUP_RETENTION
        assert tab.backup_retention.value() == DEFAULT_BACKUP_RETENTION
        assert tab.backup_retention.accessibleName()
        assert tab.backup_retention.accessibleDescription()

        tab.backup_retention.setValue(7)

        assert state.profile.injection.backup_retention == 7
        assert changes
    finally:
        tab.close()


def test_loading_a_profile_updates_retention_without_recursive_profile_change(
    qapp_retention,
):
    state = AppState()
    tab = ApplyTab(state)
    changes = []
    state.profileChanged.connect(lambda: changes.append(True))
    try:
        profile = Profile(name="retention-test")
        profile.injection.backup_retention = 3
        state.set_profile(profile)

        assert tab.backup_retention.value() == 3
        assert len(changes) == 1
    finally:
        tab.close()


def test_editor_surfaces_nonfatal_snapshot_cleanup_warning(
    qapp_retention,
    monkeypatch,
):
    state = AppState()
    tab = AdvancedStringEditorTab(state)
    state.begin_user_override_scope(("LIVE", "english"))
    tab._scope_key = ("LIVE", "english")
    monkeypatch.setattr(tab, "_scope", lambda: ("LIVE", "english"))
    snapshot = PersistentEditorSnapshot(
        "LIVE",
        "english",
        Path("user.ini"),
        {"Key": "value"},
        True,
        1,
        0,
        "user.ini was saved, but older snapshot cleanup was skipped: permission denied",
    )
    try:
        tab._loaded(snapshot)
        assert tab.status.property("tone") == "warning"
        assert "cleanup was skipped" in tab.status.text()
    finally:
        tab.close()
