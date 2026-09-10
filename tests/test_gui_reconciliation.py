"""Explicit, virtualized per-key localization reconciliation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.gui.reconciliation import (
    ReconciliationDialog,
    ReconciliationTableModel,
)
from starcompanion.gui.state import AppState
from starcompanion.gui.tabs import editor as editor_module
from starcompanion.gui.tabs.editor import (
    AdvancedStringEditorTab,
    DeltaPackExportPreview,
    PersistentEditorSnapshot,
    UserImportPreview,
)
from starcompanion.config import Profile
from starcompanion.sharing import (
    DeltaPackDocument,
    DeltaPackError,
    DeltaPackExportPlan,
)
from starcompanion.user_edits import (
    KeyResolution,
    ReconciliationChoice,
    UserEditError,
    UserEditStore,
)


@pytest.fixture(scope="session")
def qapp_reconciliation():
    return QApplication.instance() or QApplication([])


def _select_rows(dialog: ReconciliationDialog, *rows: int) -> None:
    selection = dialog.table.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(
            dialog.model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_model_shows_only_real_conflicts_and_starts_entirely_unresolved(
    qapp_reconciliation,
):
    long_current = "current " + "x" * 240
    model = ReconciliationTableModel(
        {"Conflict": long_current, "Same": "same", "OnlyCurrent": "local"},
        {"Conflict": "incoming", "Same": "same", "Added": "new"},
    )

    assert model.conflict_keys == ("Conflict",)
    assert model.rowCount() == 1
    assert model.data(model.index(0, model.KEY)) == "Conflict"
    assert model.data(model.index(0, model.CURRENT)).endswith("…")
    assert model.data(
        model.index(0, model.CURRENT), Qt.ItemDataRole.ToolTipRole
    ) == long_current
    assert model.data(model.index(0, model.CHOICE)) == "Unresolved"
    assert model.resolved_count == 0
    assert not model.complete
    assert model.resolutions() is None


def test_dialog_is_virtualized_accessible_and_cannot_accept_unresolved_rows(
    qapp_reconciliation,
):
    dialog = ReconciliationDialog({"A": "old"}, {"A": "new"})
    try:
        assert dialog.table.accessibleName() == "Localization conflicts"
        assert dialog.table.accessibleDescription()
        assert dialog.accept_button.accessibleDescription()
        assert not dialog.accept_button.isEnabled()
        assert dialog.result() == 0
        dialog.accept()
        assert dialog.result() == 0
        for column in range(dialog.model.columnCount()):
            assert dialog.table.indexWidget(dialog.model.index(0, column)) is None
    finally:
        dialog.close()


def test_multi_select_choices_are_explicit_and_complete_only_after_every_row(
    qapp_reconciliation,
):
    current = {"A": "old-a", "B": "old-b", "C": "old-c", "D": "old-d"}
    incoming = {"A": "new-a", "B": "new-b", "C": "new-c", "D": "new-d"}
    dialog = ReconciliationDialog(current, incoming)
    try:
        _select_rows(dialog, 0, 1)
        assert dialog.keep_button.isEnabled()
        assert not dialog.custom_button.isEnabled()
        assert dialog.apply_selected(ReconciliationChoice.KEEP) == ("A", "B")
        assert dialog.resolutions() is None
        assert not dialog.accept_button.isEnabled()

        _select_rows(dialog, 2)
        assert dialog.apply_selected(ReconciliationChoice.APPEND) == ("C",)
        _select_rows(dialog, 3)
        assert dialog.apply_selected(ReconciliationChoice.PREPEND) == ("D",)

        resolutions = dialog.resolutions()
        assert resolutions is not None
        assert resolutions["A"].choice is ReconciliationChoice.KEEP
        assert resolutions["B"].choice is ReconciliationChoice.KEEP
        assert resolutions["C"].choice is ReconciliationChoice.APPEND
        assert resolutions["D"].choice is ReconciliationChoice.PREPEND
        assert dialog.accept_button.isEnabled()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_import_and_single_row_custom_are_explicit_without_prefilled_defaults(
    qapp_reconciliation,
):
    dialog = ReconciliationDialog(
        {"A": "old-a", "B": "old-b"},
        {"A": "new-a", "B": "new-b"},
    )
    try:
        _select_rows(dialog, 0, 1)
        with pytest.raises(ValueError, match="exactly one"):
            dialog.apply_custom("mine")
        assert dialog.resolutions() is None

        _select_rows(dialog, 0)
        assert dialog.custom_button.isEnabled()
        assert dialog.apply_custom("my exact value") == ("A",)
        custom_tooltip = dialog.model.data(
            dialog.model.index(0, dialog.model.CHOICE),
            Qt.ItemDataRole.ToolTipRole,
        )
        assert custom_tooltip == "Custom value: my exact value"

        _select_rows(dialog, 1)
        assert dialog.apply_selected(ReconciliationChoice.IMPORT) == ("B",)
        resolutions = dialog.resolutions()
        assert resolutions is not None
        assert resolutions["A"].custom_value == "my exact value"
        assert resolutions["B"].choice is ReconciliationChoice.IMPORT
    finally:
        dialog.close()


def test_invalid_custom_value_is_rejected_without_resolving_the_row(
    qapp_reconciliation,
):
    model = ReconciliationTableModel({"A": "old"}, {"A": "new"})

    with pytest.raises(UserEditError, match="invalid value"):
        model.resolve_rows(
            [0],
            ReconciliationChoice.CUSTOM,
            custom_value="real\nnewline",
        )
    assert model.resolutions() is None
    assert model.data(model.index(0, model.CHOICE)) == "Unresolved"


def test_no_conflicts_requires_no_invented_resolution(qapp_reconciliation):
    dialog = ReconciliationDialog(
        {"Same": "value"},
        {"Same": "value", "Added": "incoming"},
    )
    try:
        assert dialog.model.rowCount() == 0
        assert dialog.resolutions() == {}
        assert dialog.accept_button.isEnabled()
        assert "no conflicting" in dialog.status.text().casefold()
    finally:
        dialog.close()


def test_large_conflict_set_remains_widget_free(qapp_reconciliation):
    current = {f"Key_{index:05d}": f"old {index}" for index in range(10_000)}
    incoming = {f"Key_{index:05d}": f"new {index}" for index in range(10_000)}
    dialog = ReconciliationDialog(current, incoming)
    try:
        assert dialog.model.rowCount() == 10_000
        assert dialog.table.findChildren(QWidget)  # viewport/header only
        assert dialog.table.indexWidget(dialog.model.index(9_999, 2)) is None
        assert dialog.resolutions() is None
    finally:
        dialog.close()


def test_editor_import_review_schedules_only_the_explicit_resolved_plan(
    qapp_reconciliation,
    monkeypatch,
):
    tab = AdvancedStringEditorTab(AppState())
    preview = UserImportPreview(
        "LIVE",
        "english",
        Path("reviewed-user.ini"),
        {"A": "old"},
        {"A": "new", "B": "added"},
    )
    tab.document.load(preview.current)
    tab._scope_key = ("LIVE", "english")
    monkeypatch.setattr(tab, "_scope", lambda: ("LIVE", "english"))
    seen = {}

    class AcceptedDialog:
        def __init__(self, current, incoming, parent):
            seen["dialog"] = (current, incoming, parent)

        @staticmethod
        def exec():
            return QDialog.DialogCode.Accepted

        @staticmethod
        def resolutions():
            return {"A": KeyResolution(ReconciliationChoice.IMPORT)}

    class Token:
        @staticmethod
        def checkpoint():
            return None

    def apply_import(token, selected, resolutions):
        token.checkpoint()
        seen["apply"] = (selected, resolutions)
        return PersistentEditorSnapshot(
            "LIVE",
            "english",
            Path("user.ini"),
            {"A": "new", "B": "added"},
            True,
            1,
            0,
        )

    def run_job(operation, *, on_success):
        seen["success"] = on_success
        seen["result"] = operation(Token(), None)

    monkeypatch.setattr(editor_module, "ReconciliationDialog", AcceptedDialog)
    monkeypatch.setattr(
        editor_module.QMessageBox,
        "question",
        lambda *args, **kwargs: editor_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(tab, "_apply_import_preview", apply_import)
    monkeypatch.setattr(tab, "_start_job", run_job)

    try:
        tab._import_preview_loaded(preview)
        assert seen["dialog"] == (preview.current, preview.incoming, tab)
        assert seen["apply"][0] is preview
        assert seen["apply"][1]["A"].choice is ReconciliationChoice.IMPORT
        assert seen["result"].values == {"A": "new", "B": "added"}
        assert seen["success"] == tab._user_import_saved
    finally:
        tab.close()


def test_editor_import_preview_fails_closed_if_in_memory_baseline_changes(
    qapp_reconciliation,
    monkeypatch,
):
    tab = AdvancedStringEditorTab(AppState())
    tab.document.load({"A": "changed while loading"})
    tab._scope_key = ("LIVE", "english")
    monkeypatch.setattr(tab, "_scope", lambda: ("LIVE", "english"))
    preview = UserImportPreview(
        "LIVE",
        "english",
        Path("reviewed-user.ini"),
        {"A": "old"},
        {"A": "new"},
    )
    monkeypatch.setattr(
        editor_module,
        "ReconciliationDialog",
        lambda *_args, **_kwargs: pytest.fail("stale preview opened a dialog"),
    )

    try:
        tab._import_preview_loaded(preview)
        assert "baseline changed" in tab.status.text().casefold()
    finally:
        tab.close()


def test_editor_import_commit_rechecks_saved_user_ini_after_review(monkeypatch):
    preview = UserImportPreview(
        "LIVE",
        "english",
        Path("reviewed-user.ini"),
        {"A": "reviewed baseline"},
        {"A": "incoming"},
    )

    class ChangedSession:
        def __init__(self, _store):
            self.values = {"A": "external change"}

    class Token:
        @staticmethod
        def checkpoint():
            return None

    monkeypatch.setattr(editor_module, "EditSession", ChangedSession)

    with pytest.raises(UserEditError, match="changed after import review"):
        AdvancedStringEditorTab._apply_import_preview(
            Token(),
            preview,
            {"A": KeyResolution(ReconciliationChoice.IMPORT)},
        )


def test_editor_import_commit_preserves_shared_plan_origins(monkeypatch):
    preview = UserImportPreview(
        "LIVE",
        "english",
        Path("reviewed-user.ini"),
        {"A": "old"},
        {"A": "new", "B": "added"},
    )
    seen = {}

    class MatchingSession:
        def __init__(self, _store):
            self.values = dict(preview.current)

        def import_plan(self, selected):
            seen["plan"] = selected

    class Token:
        @staticmethod
        def checkpoint():
            return None

    snapshot = PersistentEditorSnapshot(
        "LIVE",
        "english",
        Path("user.ini"),
        {"A": "oldnew", "B": "added"},
        True,
        1,
        0,
    )
    monkeypatch.setattr(editor_module, "EditSession", MatchingSession)
    monkeypatch.setattr(
        AdvancedStringEditorTab,
        "_load_snapshot",
        staticmethod(lambda _channel, _language: snapshot),
    )

    result = AdvancedStringEditorTab._apply_import_preview(
        Token(),
        preview,
        {"A": KeyResolution(ReconciliationChoice.APPEND)},
    )

    assert result is snapshot
    assert seen["plan"].origin_for("A") == "derived"
    assert seen["plan"].origin_for("B") == "imported"
    assert {change.key: change.after for change in seen["plan"].changes} == {
        "A": "oldnew",
        "B": "added",
    }


def test_delta_import_preview_validates_archive_against_selected_target(monkeypatch):
    archive = Path("reviewed-authored.zip")
    document = DeltaPackDocument(
        archive,
        "c" * 64,
        "HOTFIX",
        "english",
        {"A": "new", "B": "added"},
        Profile(name="included-profile"),
    )

    class SavedSession:
        def __init__(self, store):
            assert store.channel == "LIVE"
            assert store.language == "english"
            self.values = {"A": "old"}

    monkeypatch.setattr(editor_module, "load_delta_pack", lambda source: document)
    monkeypatch.setattr(editor_module, "EditSession", SavedSession)

    preview = AdvancedStringEditorTab._load_delta_import_preview(
        "LIVE",
        "english",
        archive,
    )

    assert (preview.channel, preview.language) == ("LIVE", "english")
    assert preview.current == {"A": "old"}
    assert preview.incoming == {"A": "new", "B": "added"}
    assert preview.source_kind == "authored delta pack"
    assert preview.source_scope == "HOTFIX/english"
    assert preview.profile_name == "included-profile"
    assert preview.archive_sha256 == "c" * 64


def test_delta_import_confirmation_shows_metadata_not_values_or_profile_activation(
    qapp_reconciliation,
    monkeypatch,
):
    tab = AdvancedStringEditorTab(AppState())
    preview = UserImportPreview(
        "LIVE",
        "english",
        Path("reviewed-authored.zip"),
        {"A": "old private text"},
        {"A": "new private text", "B": "added private text"},
        source_kind="authored delta pack",
        source_scope="HOTFIX/english",
        profile_name="included-profile",
        archive_sha256="d" * 64,
    )
    tab.document.load(preview.current)
    tab._scope_key = ("LIVE", "english")
    monkeypatch.setattr(tab, "_scope", lambda: ("LIVE", "english"))
    seen = {}

    class AcceptedDialog:
        def __init__(self, *_args):
            pass

        @staticmethod
        def exec():
            return QDialog.DialogCode.Accepted

        @staticmethod
        def resolutions():
            return {"A": KeyResolution(ReconciliationChoice.IMPORT)}

    def question(_parent, title, message, *_args, **_kwargs):
        seen["title"] = title
        seen["message"] = message
        return editor_module.QMessageBox.StandardButton.Yes

    def run_job(operation, *, on_success):
        seen["operation"] = operation
        seen["success"] = on_success

    monkeypatch.setattr(editor_module, "ReconciliationDialog", AcceptedDialog)
    monkeypatch.setattr(editor_module.QMessageBox, "question", question)
    monkeypatch.setattr(tab, "_start_job", run_job)

    try:
        tab._import_preview_loaded(preview)
        assert "authored delta pack" in seen["title"]
        assert "Target scope: LIVE/english" in seen["message"]
        assert "Source scope (advisory): HOTFIX/english" in seen["message"]
        assert f"Archive SHA-256: {'d' * 64}" in seen["message"]
        assert "Included profile: included-profile (not activated)" in seen["message"]
        assert "old private text" not in seen["message"]
        assert "new private text" not in seen["message"]
        assert "added private text" not in seen["message"]
        assert seen["success"] == tab._user_import_saved
    finally:
        tab.close()


def test_authored_delta_export_preview_hides_values_and_runs_write_in_job(
    qapp_reconciliation,
    monkeypatch,
):
    tab = AdvancedStringEditorTab(AppState())
    plan = DeltaPackExportPlan(
        UserEditStore("LIVE", "english"),
        "a" * 64,
        "b" * 64,
        {"Private_Key": "secret authored text"},
        3,
        b"{}",
        "LIVE",
        "english",
    )
    preview = DeltaPackExportPreview(
        plan,
        Path("authored.zip"),
        True,
        "shareable",
    )
    seen = {}

    class Token:
        @staticmethod
        def checkpoint():
            return None

    def question(_parent, _title, message, *_args, **_kwargs):
        seen["message"] = message
        return editor_module.QMessageBox.StandardButton.Yes

    def write_export(token, selected):
        token.checkpoint()
        seen["write"] = selected
        return selected.destination

    def run_job(operation, *, on_success):
        seen["success"] = on_success
        seen["destination"] = operation(Token(), None)

    monkeypatch.setattr(editor_module.QMessageBox, "question", question)
    monkeypatch.setattr(tab, "_write_delta_export", write_export)
    monkeypatch.setattr(tab, "_start_job", run_job)

    try:
        tab._delta_export_planned(preview)
        assert "Authored values: 1" in seen["message"]
        assert "Excluded imported/derived/unknown values: 3" in seen["message"]
        assert "Overwrite existing file: Yes" in seen["message"]
        assert "shareable" in seen["message"]
        assert "secret authored text" not in seen["message"]
        assert seen["write"] is preview
        assert seen["destination"] == Path("authored.zip")
        assert seen["success"] == tab._delta_export_written
    finally:
        tab.close()


def test_authored_delta_export_rejects_non_zip_before_reading_user_store():
    with pytest.raises(DeltaPackError, match="must be a .zip"):
        AdvancedStringEditorTab._plan_delta_export(
            "LIVE",
            "english",
            Profile(),
            Path("authored.txt"),
        )
