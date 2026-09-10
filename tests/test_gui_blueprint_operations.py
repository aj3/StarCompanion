"""Focused G4 GUI ownership operations without filesystem fixtures."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.blueprints import (
    BlueprintCatalog,
    CatalogEntry,
    RewardSource,
    query_blueprints,
)
from starcompanion.gui.state import AppState
from starcompanion.gui.tabs import blueprints as blueprint_tab
from starcompanion.gui.tabs.blueprints import BlueprintTrackerTab
from starcompanion.ownership import (
    Acquisition,
    ImportCandidate,
    ImportPlan,
    OwnershipConflictError,
    OwnershipDecision,
    OwnershipRecord,
    OwnershipState,
)


@pytest.fixture(scope="session")
def qapp_blueprints():
    return QApplication.instance() or QApplication([])


def catalog() -> BlueprintCatalog:
    return BlueprintCatalog(
        (
            CatalogEntry(
                "cig:first",
                "Aves Core",
                "aves core",
                category="armor",
                item_type="helmet",
                item_class="combat",
                size="medium",
                grade="a",
                reward_sources={
                    RewardSource("Mission_First", "Foxwell", "Salvage")
                },
            ),
            CatalogEntry(
                "cig:second",
                "Coda Pistol",
                "coda pistol",
                category="weapons",
                item_type="pistol",
                item_class="ballistic",
                size="small",
                grade="b",
                reward_sources={
                    RewardSource("Mission_Second", "BitZeros", "Bounty")
                },
            ),
        )
    )


def configured_tab() -> BlueprintTrackerTab:
    tab = BlueprintTrackerTab(AppState(), link_live_hotfix=False)
    tab.catalog = catalog()
    tab.ownership = OwnershipState("LIVE")
    tab.channel = "LIVE"
    tab.scope_name = "LIVE"
    tab._replace_filter_values()
    tab.refresh_query()
    return tab


def test_manual_multiselect_is_one_previewed_revision_bound_job(
    qapp_blueprints,
    monkeypatch,
):
    tab = configured_tab()
    seen = {}
    selection = tab.table.selectionModel()
    for row in range(tab.model.rowCount()):
        selection.select(
            tab.model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    original_plan = blueprint_tab.plan_manual_ownership

    def plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        seen["plan"] = result
        return result

    def question(_parent, _title, message, *_args, **_kwargs):
        seen["message"] = message
        return blueprint_tab.QMessageBox.StandardButton.Yes

    def start_job(operation, on_success, **_kwargs):
        seen["operation"] = operation
        seen["success"] = on_success

    monkeypatch.setattr(blueprint_tab, "plan_manual_ownership", plan)
    monkeypatch.setattr(blueprint_tab.QMessageBox, "question", question)
    monkeypatch.setattr(tab, "_start_job", start_job)

    try:
        tab.mark_selected(OwnershipDecision.OWNED)
        assert len(seen["plan"].changes) == 2
        assert seen["plan"].expected_revision == 0
        assert "Aves Core" in seen["message"]
        assert "Coda Pistol" in seen["message"]
        assert "Revision: 0" in seen["message"]
        assert callable(seen["operation"])
        assert callable(seen["success"])
    finally:
        tab.close()


def test_all_g4_catalog_filters_combine_in_the_gui(qapp_blueprints):
    tab = configured_tab()
    tab.ownership.records["cig:first"] = OwnershipRecord(
        "cig:first",
        "Aves Core",
        [Acquisition("event", "manual", None, "manual", "evidence")],
    )
    tab.model.set_rows(query_blueprints(tab.catalog, tab.ownership))

    selected = (
        (tab.category_filter, "armor"),
        (tab.type_filter, "helmet"),
        (tab.class_filter, "combat"),
        (tab.size_filter, "medium"),
        (tab.grade_filter, "a"),
        (tab.mission_filter, "Mission_First"),
        (tab.reward_filter, "Foxwell"),
    )
    try:
        for combo, value in selected:
            index = combo.findData(value)
            assert index >= 0
            combo.setCurrentIndex(index)
        assert tab.model.rowCount() == 1
        assert tab.model.rows[0].entry.blueprint_id == "cig:first"
        assert tab.model.rows[0].owned
    finally:
        tab.close()


def test_full_rescan_requires_confirmation_and_sets_explicit_mode(
    qapp_blueprints,
    monkeypatch,
):
    tab = configured_tab()
    seen = []
    monkeypatch.setattr(
        blueprint_tab.QMessageBox,
        "question",
        lambda *_args, **_kwargs: blueprint_tab.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        tab,
        "scan_logs",
        lambda *, full_rescan=False: seen.append(full_rescan),
    )
    try:
        tab.full_rescan()
        assert seen == [True]
    finally:
        tab.close()


def test_g4_ownership_actions_have_accessible_descriptions(qapp_blueprints):
    tab = configured_tab()
    try:
        controls = (
            tab.full_scan_button,
            tab.mark_owned_button,
            tab.mark_unowned_button,
            tab.resolve_button,
            tab.import_button,
            tab.export_button,
        )
        assert all(control.accessibleName() for control in controls)
        assert all(control.accessibleDescription() for control in controls)
    finally:
        tab.close()


def test_import_preview_schedules_only_exact_revision_bound_candidates(
    qapp_blueprints,
    monkeypatch,
):
    tab = configured_tab()
    plan = ImportPlan(
        "incoming.json",
        "f" * 64,
        "LIVE",
        0,
        (ImportCandidate("cig:first", "Aves Core"),),
        ("Unknown item",),
        (),
    )
    seen = {}

    def question(_parent, _title, message, *_args, **_kwargs):
        seen["message"] = message
        return blueprint_tab.QMessageBox.StandardButton.Yes

    def start_job(operation, on_success, **_kwargs):
        seen["operation"] = operation
        seen["success"] = on_success

    monkeypatch.setattr(blueprint_tab.QMessageBox, "question", question)
    monkeypatch.setattr(tab, "_start_job", start_job)
    try:
        tab._import_preview_ready("LIVE", False, tab.catalog, plan)
        assert "Exact additions: 1" in seen["message"]
        assert "Unmatched (not imported): 1" in seen["message"]
        assert "Aves Core" in seen["message"]
        assert callable(seen["operation"])
        assert callable(seen["success"])
    finally:
        tab.close()


def test_export_rechecks_revision_before_building_or_writing_payload(monkeypatch):
    class Loaded:
        state = OwnershipState("LIVE", revision=2)

    class ChangedStore:
        path = None

        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def load_details():
            return Loaded()

    class Token:
        @staticmethod
        def checkpoint():
            return None

    monkeypatch.setattr(blueprint_tab, "OwnershipStore", ChangedStore)
    monkeypatch.setattr(
        blueprint_tab,
        "write_export",
        lambda *_args, **_kwargs: pytest.fail("stale export wrote a file"),
    )

    with pytest.raises(OwnershipConflictError, match="changed after export confirmation"):
        BlueprintTrackerTab._write_export(
            Token(),
            "LIVE",
            False,
            catalog(),
            1,
            blueprint_tab.Path("ownership.json"),
        )
