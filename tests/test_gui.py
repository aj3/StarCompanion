"""GUI tests.

Run headless via the offscreen platform. They cover the wiring the GUI adds --
widget/profile binding, preview robustness, and the write gates -- not the
pipeline behaviour already covered elsewhere.
"""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# An explicit guard rather than pytest.importorskip: PySide6 itself imports
# fine without Qt's system libraries, and loading QtWidgets then fails with a
# plain ImportError ("libEGL.so.1: cannot open shared object file"). pytest
# re-raises non-ModuleNotFoundError import failures, so importorskip would
# still break collection here.
try:
    from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the machine
    pytest.skip(
        f"PySide6 widgets unavailable, skipping GUI tests: {exc}",
        allow_module_level=True,
    )

from starcompanion.config import Profile, load_builtin  # noqa: E402
from starcompanion.gui import AppState, MainWindow  # noqa: E402
from starcompanion.ini import BOM, LocalizationFile  # noqa: E402
from starcompanion.inject import MergeMode  # noqa: E402
from starcompanion.model import StringKind  # noqa: E402
from starcompanion.portability import PreferencesStore  # noqa: E402
from starcompanion.sources import contracts_ini  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"

MINI = (
    BOM
    + "Org_x_title=Do a thing <EM4>[100 Rep]</EM4>\n"
    + r"Org_x_desc=Body.\n\n<EM4>Reputation Awarded:</EM4> 100"
    + "\n"
)
STOCK = BOM + "Org_x_title=Original\nOrg_x_desc=Original body.\nOther=untouched\n"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_qt_windows(qapp):
    """Do not retain full window trees and global dialogs between tests."""
    yield
    from PySide6.QtCore import QCoreApplication, QEvent

    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def expert_tabs(monkeypatch):
    """Show the hand-driven apply screen for tests that exercise it."""
    monkeypatch.setenv("STARCOMPANION_EXPERT", "1")


@pytest.fixture
def community_rewards(monkeypatch):
    """Turn on the hidden community-rewards capability for tests that need it."""
    monkeypatch.setenv("STARCOMPANION_COMMUNITY_REWARDS", "1")


@pytest.fixture(autouse=True)
def no_real_game(monkeypatch, tmp_path):
    """Keep tests off the developer's actual install.

    Without this, constructing a window finds the real game and any code path
    that reads it opens a multi-gigabyte archive -- slow, and it would make
    results depend on whose machine the suite runs on.
    """
    from starcompanion import install as installs

    monkeypatch.setattr(installs, "find_default", lambda: None)
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("STARCOMPANION_DATA", str(tmp_path / "data"))


@pytest.fixture
def contracts(tmp_path):
    path = tmp_path / "contracts.ini"
    path.write_bytes(MINI.encode("utf-8"))
    return contracts_ini.load(path)


@pytest.fixture
def window(qapp, contracts):
    w = MainWindow()
    w.state.set_contracts(contracts)
    # The production editor intentionally coalesces rapid state changes on a
    # timer.  Build the in-memory projection explicitly for deterministic GUI
    # tests instead of making every test sleep on the event loop.
    w.editor.rebuild()
    return w


def _wait_until(qapp, predicate, *, timeout=10.0, message="GUI condition did not finish"):
    """Pump Qt without entering QTest's nested event loop."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    if not predicate():
        pytest.fail(message)


# --- C6 application shell ---------------------------------------------------


def test_shell_groups_the_existing_pages_by_workflow(window):
    labels = [button.property("navigationLabel") for button in window.shell._nav_buttons]

    assert labels == [
        "Overview",
        "Contract content",
        "Presentation",
        "Blueprints",
        "Custom wording",
        "String editor",
        "Data & provenance",
        "Backup & recovery",
        "Settings & help",
    ]
    assert window.shell.isAncestorOf(window.start)
    assert window.shell.isAncestorOf(window.templates)


def test_shell_navigation_updates_page_identity(window, qapp):
    window.shell._nav_buttons[2].click()
    qapp.processEvents()

    assert window.tabs.currentIndex() == 2
    assert window.shell.page_title.text() == "Shape the presentation"
    assert window.shell._nav_buttons[2].isChecked()


def test_shell_keeps_local_operating_context_visible(window):
    assert window.shell.profile_button.text() == "PROFILE / DEFAULT"
    assert window.shell.game_badge.text() == "GAME / NOT FOUND"
    assert window.shell.data_badge.text() == "DATA / 1 CONTRACT"
    assert "NO TELEMETRY" in window.shell.status_text.text()
    assert "WRITES REQUIRE CONFIRMATION" in window.shell.status_text.text()


def test_shell_preserves_the_legacy_tab_metadata_api(window):
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == [
        "Start here",
        "What to show",
        "Appearance",
        "Blueprint ownership",
        "Advanced: custom wording",
        "Advanced: strings",
        "Data & provenance",
        "Backup and recovery",
        "Settings and help",
    ]


def test_reusable_state_components_expose_semantic_state(window):
    from starcompanion.gui.components import EmptyState, NoticeBanner, StatusCard, Tone

    notice = NoticeBanner("A recoverable problem", tone=Tone.WARNING)
    empty = EmptyState("No results", "Change the filters and try again.")
    card = StatusCard("Provider")
    card.set_status("Unavailable", Tone.DANGER)

    assert notice.text() == "A recoverable problem"
    assert notice.property("tone") == "warning"
    assert empty.property("component") == "empty-state"
    assert card.property("tone") == "danger"


def test_overview_reports_ready_state_from_existing_core_state(window):
    assert window.start.hero.title_label.text() == "Connect your Star Citizen install"
    assert window.start.contract_card.status_label.text() == "1 contracts ready"
    assert window.start.contracts_empty.isHidden()


def test_ui_preferences_migrate_and_preserve_other_portable_settings(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    PreferencesStore(root).save({"theme": "light", "default_channel": "LIVE"})

    loaded = UiPreferencesStore(root).load()
    stored = PreferencesStore(root).load()

    assert loaded.warning is None
    assert loaded.preferences.theme == "light"
    assert stored["ui_schema"] == 1
    assert stored["last_page"] == "overview"
    assert stored["default_channel"] == "LIVE"


def test_ui_theme_is_independent_from_output_profile(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    PreferencesStore(root).save(
        {"theme": "dark", "ui_schema": 1, "last_page": "overview"}
    )
    fresh = MainWindow(ui_preferences_store=UiPreferencesStore(root))
    light_profile = Profile.model_validate({"appearance": {"theme": "light"}})

    fresh.state.set_profile(light_profile)

    assert fresh.ui_preferences.theme == "dark"
    assert fresh.state.profile.appearance.theme == "light"


def test_theme_toggle_persists_without_mutating_output_profile(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    fresh = MainWindow(ui_preferences_store=UiPreferencesStore(root))
    profile_theme = fresh.state.profile.appearance.theme

    fresh.toggle_theme()

    assert PreferencesStore(root).load()["theme"] == "light"
    assert fresh.state.profile.appearance.theme == profile_theme


def test_last_navigation_page_round_trips(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    first = MainWindow(ui_preferences_store=UiPreferencesStore(root))
    first.shell.set_current_key("presentation")
    first.close()

    second = MainWindow(ui_preferences_store=UiPreferencesStore(root))

    assert second.shell.current_key() == "presentation"


def test_recovery_page_is_a_stable_portable_navigation_destination(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    PreferencesStore(root).save(
        {"theme": "dark", "ui_schema": 1, "last_page": "manual-apply"}
    )

    fresh = MainWindow(ui_preferences_store=UiPreferencesStore(root))

    assert fresh.shell.current_key() == "manual-apply"
    assert PreferencesStore(root).load()["last_page"] == "manual-apply"


def test_corrupt_ui_preferences_are_reported_and_never_overwritten(qapp, tmp_path):
    from starcompanion.gui.preferences import UiPreferencesStore

    root = tmp_path / "preferences"
    root.mkdir()
    path = root / "preferences.json"
    original = b'{"theme":"dark", "theme":"light"}'
    path.write_bytes(original)

    fresh = MainWindow(ui_preferences_store=UiPreferencesStore(root))
    fresh.toggle_theme()

    assert path.read_bytes() == original
    assert fresh.start.preference_warning.isVisibleTo(fresh.start)
    assert "left unchanged" in fresh.start.preference_warning.text()


# --- state -------------------------------------------------------------------


def test_state_starts_with_the_default_profile(qapp):
    assert AppState().profile.name == "default"


def test_profile_edits_do_not_reapply_an_unchanged_theme(qapp, monkeypatch):
    from starcompanion.gui import theme

    applied = []
    monkeypatch.setattr(theme, "apply_theme", lambda app, name: applied.append(name))
    fresh = MainWindow()

    fresh.state.touch_profile()
    fresh.toggle_theme()

    assert applied == ["dark", "light"]


def test_render_is_cached_until_the_profile_changes(window):
    first = window.state.render()
    assert window.state.render() is first

    window.state.profile.fields.reputation = False
    window.state.touch_profile()
    assert window.state.render() is not first


def test_sample_contract_prefers_one_with_rewards(window):
    sample = window.state.sample_contract()
    assert sample is not None and not sample.reward.is_empty


# --- source tab --------------------------------------------------------------


def test_source_summary_reports_what_was_loaded(community_rewards, window):
    text = window.source.summary_text()
    assert "1 contract" in text and "2 localization keys" in text


def test_source_summary_before_loading(community_rewards, qapp):
    assert "No contract data" in MainWindow().source.summary_text()


def test_loading_bad_path_does_not_crash(community_rewards, window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window.source.path_edit.setText(str(tmp_path / "nope.ini"))
    window.source.load_contracts()
    assert window.state.contracts is not None  # previous data intact


def test_provenance_page_renders_provider_health_and_aggregate_evidence(window):
    from starcompanion.model import Evidence, ProviderCapability, ProviderStatus

    contracts = window.state.contracts
    contracts.contracts[0].evidence.append(
        Evidence("local-dataforge-missions", "record", "path", "reward.rep", 100)
    )
    contracts.capabilities.append(
        ProviderCapability(
            provider="local-dataforge-missions",
            version="2",
            status=ProviderStatus.DEGRADED,
            build_version="test-build",
            facts_seen=12,
            contracts_enhanced=1,
            evidence_links=4,
            diagnostics=("optional target missing",),
            reward_facts=10,
            matched_facts=8,
            unmatched_facts=2,
        )
    )
    window.state.set_contracts(contracts)

    assert window.source.evidence_metric.value.text() == "1"
    assert len(window.source.provider_cards) == 1
    provider = window.source.provider_cards[0]
    assert provider.status_label.text() == "Degraded"
    assert provider.coverage.value.text() == "8 / 10"
    assert provider.property("tone") == "warning"
    assert "1 contracts enhanced" in provider.accessibleDescription()


def test_provenance_page_has_truthful_empty_provider_state(window):
    assert window.source.provider_empty.isVisibleTo(window.source)
    assert window.source.provider_cards == []


# --- fields tab --------------------------------------------------------------


def test_field_checkboxes_reflect_the_profile(community_rewards, window):
    window.state.set_profile(load_builtin("minimal"))
    assert not window.fields.boxes["reputation"].isChecked()

    window.state.set_profile(load_builtin("default"))
    assert window.fields.boxes["reputation"].isChecked()


def test_toggling_a_checkbox_updates_the_profile_and_output(community_rewards, window):
    assert "Reputation Awarded" in window.state.render().values["Org_x_desc"]

    window.fields.boxes["reputation"].setChecked(False)

    assert window.state.profile.fields.reputation is False
    assert "Reputation Awarded" not in window.state.render().values["Org_x_desc"]


def test_every_toggle_is_bound(community_rewards, window):
    for name, box in window.fields.boxes.items():
        before = getattr(window.state.profile.fields, name)
        box.setChecked(not before)
        assert getattr(window.state.profile.fields, name) is (not before)


def test_contract_content_uses_summary_metrics_and_toggle_rows(window):
    assert window.fields.enabled_metric.value.text() == "3 / 3"
    assert all(
        row.property("component") == "toggle-row"
        for row in window.fields.rows.values()
    )


# --- formatting tab ----------------------------------------------------------


def test_default_emphasis_flows_into_output(window):
    combo = window.formatting.default_tag
    combo.setCurrentIndex(combo.findData("EM3"))
    assert window.state.profile.formatting.emphasis == "EM3"
    assert "<EM3>Reputation Awarded:</EM3>" in window.state.render().values["Org_x_desc"]


def test_per_field_emphasis_override_and_inherit(window):
    combo = window.formatting.field_tags["reputation"]
    combo.setCurrentIndex(combo.findData("EM"))
    assert window.state.profile.formatting.by_field == {"reputation": "EM"}
    assert "<EM>Reputation Awarded:</EM>" in window.state.render().values["Org_x_desc"]

    combo.setCurrentIndex(combo.findData(None))
    assert window.state.profile.formatting.by_field == {}


def test_only_renderable_tags_are_offered(window):
    combo = window.formatting.default_tag
    tags = {combo.itemData(i) for i in range(combo.count())}
    assert tags == {"EM", "EM1", "EM2", "EM3", "EM4", "b", "i"}
    assert None not in tags, "the main style always has a value"


def test_style_choices_are_shown_in_plain_language(window):
    """Raw game tags must not reach the screen."""
    combo = window.formatting.default_tag
    shown = [combo.itemText(i) for i in range(combo.count())]

    assert not any(text.startswith("EM") for text in shown)
    assert any("Highlight" in text for text in shown)
    assert "Bold" in shown and "Italic" in shown


def test_per_field_styling_starts_hidden(qapp):
    """Seven dropdowns should not greet someone who wants sensible defaults."""
    fresh = MainWindow()
    assert not fresh.formatting.per_field_toggle.isChecked()
    assert not fresh.formatting.per_field.isVisibleTo(fresh.formatting)


def test_per_field_styling_appears_when_asked_for(window):
    window.formatting.per_field_toggle.setChecked(True)
    assert window.formatting.per_field.isVisibleTo(window.formatting)


def test_unticking_per_field_styling_clears_the_overrides(window):
    combo = window.formatting.field_tags["reputation"]
    combo.setCurrentIndex(combo.findData("EM"))
    assert window.state.profile.formatting.by_field

    window.formatting.per_field_toggle.setChecked(False)
    assert window.state.profile.formatting.by_field == {}


def test_title_prefix_choices_avoid_jargon(window):
    combo = window.formatting.prefix
    shown = [combo.itemText(i) for i in range(combo.count())]
    assert shown == ["Nothing", "Who is offering it", "How hard it is", "Both"]


def test_local_rewards_and_provenance_are_visible_by_default(qapp):
    """Local providers are first-class while community download controls stay hidden."""
    fresh = MainWindow()
    tabs = [fresh.tabs.tabText(i) for i in range(fresh.tabs.count())]

    assert "What to show" in tabs
    assert "Data & provenance" in tabs
    assert fresh.start.data_step.isVisibleTo(fresh.start)


def test_community_rewards_can_be_switched_back_on(community_rewards, qapp):
    """Hidden, not removed: the code path is intact behind an environment flag."""
    fresh = MainWindow()
    tabs = [fresh.tabs.tabText(i) for i in range(fresh.tabs.count())]

    assert "What to show" in tabs
    assert fresh.start.data_step.isVisibleTo(fresh.start)


def test_overview_replaces_wizard_steps_with_status_cards(qapp):
    fresh = MainWindow()
    cards = [
        fresh.start.game_card,
        fresh.start.contract_card,
        fresh.start.data_card,
        fresh.start.look_card,
    ]
    assert all(card.property("component") == "status-card" for card in cards)
    assert fresh.start.hero.property("component") == "dashboard-hero"


def test_title_prefix_changes_rendered_titles(window):
    index = window.formatting.prefix.findData("org_rank")
    window.formatting.prefix.setCurrentIndex(index)
    assert window.state.profile.formatting.title.prefix == "org_rank"
    assert window.state.render().values["Org_x_title"].startswith("[Org")


def test_max_pool_items_zero_means_no_limit(window):
    window.formatting.max_items.setValue(5)
    assert window.state.profile.formatting.max_pool_items == 5
    window.formatting.max_items.setValue(0)
    assert window.state.profile.formatting.max_pool_items is None


def test_formatting_widgets_refresh_when_profile_is_replaced(window):
    window.state.set_profile(load_builtin("rank-first"))
    assert window.formatting.prefix.currentData() == "org_rank"
    assert window.formatting.max_items.value() == 12


def test_presentation_summary_tracks_existing_profile_controls(window):
    window.formatting.prefix.setCurrentIndex(window.formatting.prefix.findData("org"))
    window.formatting.max_items.setValue(9)

    assert window.formatting.prefix_metric.value.text() == "Who is offering it"
    assert window.formatting.length_metric.value.text() == "9"


# --- accessibility ----------------------------------------------------------


def test_target_page_controls_have_screen_reader_names_and_descriptions(window):
    controls = [
        *window.fields.boxes.values(),
        window.formatting.default_tag,
        window.formatting.per_field_toggle,
        *window.formatting.field_tags.values(),
        window.formatting.prefix,
        window.formatting.bracket_rep,
        window.formatting.bracket_bp,
        window.formatting.max_items,
        window.source.path_edit,
        window.source.browse_button,
        window.source.load_button,
        window.source.load_cache_button,
        window.source.save_cache_button,
        window.templates.show_help,
        window.templates.org,
        window.templates.kind,
        window.templates.reset_button,
        window.templates.editor,
        window.templates.preview,
        window.editor.search,
        window.editor.state_filter,
        window.editor.source_filter,
        window.editor.category_filter,
        window.editor.provider_filter,
        window.editor.table,
        window.editor.stock_view,
        window.editor.rendered_view,
        window.editor.merged_editor,
        window.editor.provenance_view,
        window.editor.undo_button,
        window.editor.redo_button,
        window.editor.reset_button,
        window.editor.reload_button,
        window.editor.save_button,
        window.start.channel_selector,
        window.start.discover_channels_button,
        window.blueprints.search,
        window.blueprints.ownership_filter,
        window.blueprints.category_filter,
        window.blueprints.reward_filter,
        window.blueprints.table,
        window.blueprints.scan_button,
        window.blueprints.reload_button,
        window.blueprints.recover_button,
        window.support.profile_builtin,
        window.support.profile_open,
        window.support.profile_save,
        window.support.settings_preview,
        window.support.export_settings_button,
        window.support.import_settings_button,
        window.support.apply_settings_button,
        window.support.recover_settings_button,
        window.support.diagnostics_view,
        window.support.build_diagnostics_button,
        window.support.export_diagnostics_button,
        window.support.help_search,
        window.support.help_results,
        window.support.help_text,
    ]

    assert all(control.accessibleName() for control in controls)
    assert all(control.accessibleDescription() for control in controls)
    assert window.fields.accessibleName() == "Choose contract intelligence"
    assert window.formatting.accessibleName() == "Shape the presentation"
    assert window.source.accessibleName() == "Inspect local source evidence"
    assert window.editor.accessibleName() == "Inspect and edit merged strings"


def test_manual_apply_controls_have_screen_reader_names_and_descriptions(
    expert_tabs, window
):
    controls = [
        window.apply.target_edit,
        window.apply.target_browse,
        window.apply.stock_edit,
        window.apply.stock_browse,
        window.apply.mode,
        window.apply.refresh_button,
        window.apply.plan_view.filter,
        window.apply.plan_view.tree,
        window.apply.export_button,
        window.apply.apply_button,
        window.apply.backups,
        window.apply.restore_button,
        window.apply.resolve_button,
    ]

    assert all(control.accessibleName() for control in controls)
    assert all(control.accessibleDescription() for control in controls)
    assert window.apply.accessibleName() == "Review backups and recovery"


def test_qt_accessibility_interfaces_publish_names_and_descriptions(window):
    from PySide6.QtGui import QAccessible

    representatives = [
        window.shell._nav_buttons[1],
        window.fields.boxes["reputation"],
        window.formatting.default_tag,
        window.source.path_edit,
        window.start.hero,
    ]
    for widget in representatives:
        interface = QAccessible.queryAccessibleInterface(widget)
        assert interface is not None
        assert interface.text(QAccessible.Text.Name)
        assert interface.text(QAccessible.Text.Description)


def test_alt_number_shortcuts_navigate_without_a_mouse(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.show()
    window.activateWindow()
    qapp.processEvents()
    QTest.keyClick(
        window,
        Qt.Key.Key_3,
        Qt.KeyboardModifier.AltModifier,
    )
    qapp.processEvents()

    assert window.shell.current_key() == "presentation"
    assert window.shell.page_title.text() == "Shape the presentation"
    assert qapp.focusWidget() is window.shell._nav_buttons[2]


def test_sidebar_supports_arrow_key_navigation(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.show()
    window.activateWindow()
    qapp.processEvents()
    first = window.shell._nav_buttons[0]
    first.setFocus()
    qapp.processEvents()
    QTest.keyClick(first, Qt.Key.Key_Down)
    qapp.processEvents()

    assert window.shell.current_key() == "content"
    assert qapp.focusWidget() is window.shell._nav_buttons[1]

    QTest.keyClick(window.shell._nav_buttons[1], Qt.Key.Key_End)
    assert window.shell.current_key() == "support"
    QTest.keyClick(window.shell._nav_buttons[-1], Qt.Key.Key_Home)
    assert window.shell.current_key() == "overview"


def test_contract_content_has_deterministic_keyboard_focus_order(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("content")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    controls = list(window.fields.boxes.values())
    controls[0].setFocus()
    qapp.processEvents()
    QTest.keyClick(controls[0], Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is controls[1]


def test_contract_toggle_is_keyboard_operable(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("content")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    box = window.fields.boxes["reputation"]
    before = box.isChecked()
    box.setFocus()
    QTest.keyClick(box, Qt.Key.Key_Space)

    assert box.isChecked() is not before


def test_presentation_has_deterministic_keyboard_focus_order(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("presentation")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.formatting.default_tag.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.formatting.default_tag, Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is window.formatting.per_field_toggle


def test_provenance_tools_have_deterministic_keyboard_focus_order(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("provenance")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.source.path_edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.source.path_edit, Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is window.source.browse_button


def test_advanced_editor_uses_virtualized_source_and_plan_models(window):
    assert window.editor.model.rowCount() == 2
    assert window.editor.table.indexWidget(window.editor.proxy.index(0, 0)) is None
    snapshot = window.editor.model.snapshot
    assert snapshot is not None
    assert snapshot.plan.updated
    record = snapshot.records[0]
    assert record.stock
    assert record.rendered
    assert record.winner.kind.value == "generated"
    assert window.editor.total_metric.value.text() == "2"


def test_advanced_editor_debounces_edit_validation_and_supports_undo(window, qapp):
    from PySide6.QtTest import QTest

    window.shell.set_current_key("string-editor")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.editor.table.selectRow(0)
    qapp.processEvents()
    record = window.editor._selected_records()[0]
    window.editor.merged_editor.setPlainText("USER {{ literal }}")
    assert record.key not in window.editor.document.values

    QTest.qWait(300)
    qapp.processEvents()

    assert window.editor.document.values[record.key] == "USER {{ literal }}"
    assert window.editor.undo_button.isEnabled()
    window.editor.undo()
    assert record.key not in window.editor.document.values


def test_advanced_editor_search_is_debounced_and_filterable(window, qapp):
    from PySide6.QtTest import QTest

    modified = next(
        record for record in window.editor.model.snapshot.records if record.modified
    )
    window.editor.search.setText(modified.key)
    assert window.editor.proxy.query == ""
    QTest.qWait(150)
    qapp.processEvents()

    assert window.editor.proxy.query == modified.key.casefold()
    assert window.editor.proxy.rowCount() == 1
    window.editor.state_filter.setCurrentIndex(
        window.editor.state_filter.findData("modified")
    )
    assert window.editor.proxy.rowCount() == 1


def test_advanced_editor_multi_reset_is_one_undoable_command(window, qapp, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel

    keys = tuple(record.key for record in window.editor.model.snapshot.records)
    window.editor.document.load({key: f"user {number}" for number, key in enumerate(keys)})
    window.editor.model.rebuild()
    for row in range(window.editor.proxy.rowCount()):
        window.editor.table.selectionModel().select(
            window.editor.proxy.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.editor.reset_selected()

    assert window.editor.document.values == {}
    assert len(window.editor.document.commands[-1].changes) == 2
    window.editor.undo()
    assert set(window.editor.document.values) == set(keys)


def test_editor_user_ini_load_and_save_never_run_on_gui_thread(
    window, qapp, tmp_path, monkeypatch
):
    from PySide6.QtCore import QThread
    from starcompanion.user_edits import UserEditStore

    load_threads = []
    save_threads = []
    original_load = UserEditStore.load
    original_save = UserEditStore.save

    def tracked_load(store):
        load_threads.append(QThread.currentThread())
        return original_load(store)

    def tracked_save(store, values, **kwargs):
        save_threads.append(QThread.currentThread())
        return original_save(store, values, **kwargs)

    monkeypatch.setattr(UserEditStore, "load", tracked_load)
    monkeypatch.setattr(UserEditStore, "save", tracked_save)
    target = tmp_path / "LIVE" / "data" / "Localization" / "english" / "global.ini"
    window.state.set_target(target)
    _wait_until(
        qapp,
        lambda: not window.editor._jobs and not window.editor.scope_timer.isActive(),
        message="background user.ini load did not finish",
    )
    assert window.state.user_overrides_ready

    key = window.editor.model.snapshot.records[0].key
    window.editor.document.set_value(key, "Saved user value")
    window.editor.model.rebuild()
    window.editor._after_model_change()
    window.editor.save_user_edits()
    _wait_until(
        qapp,
        lambda: not window.editor._jobs,
        message="background user.ini save did not finish",
    )

    assert load_threads and save_threads
    assert all(thread is not qapp.thread() for thread in (*load_threads, *save_threads))
    assert window.state.user_overrides == {key: "Saved user value"}
    assert window.state.effective_values()[key] == "Saved user value"


def test_editor_refuses_to_overwrite_external_user_ini_change(
    window, qapp, tmp_path
):
    from starcompanion.user_edits import UserEditStore

    target = tmp_path / "LIVE" / "data" / "Localization" / "english" / "global.ini"
    window.state.set_target(target)
    _wait_until(
        qapp,
        lambda: not window.editor._jobs and not window.editor.scope_timer.isActive(),
        message="background user.ini load did not finish",
    )
    store = UserEditStore("LIVE", "english")
    store.save({"External_Key": "external"})
    key = window.editor.model.snapshot.records[0].key
    window.editor.document.set_value(key, "draft")
    window.editor.model.rebuild()
    window.editor._after_model_change()

    window.editor.save_user_edits()
    _wait_until(
        qapp,
        lambda: not window.editor._jobs,
        message="external-change rejection did not finish",
    )

    assert store.load() == {"External_Key": "external"}
    assert "changed outside this editor" in window.editor.status.text()
    assert window.editor.document.dirty


def test_advanced_editor_has_deterministic_keyboard_focus_order(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("string-editor")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.editor.search.setFocus()
    QTest.keyClick(window.editor.search, Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is window.editor.state_filter


def test_custom_wording_has_deterministic_keyboard_focus_order(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("templates")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.templates.show_help.setFocus()
    QTest.keyClick(window.templates.show_help, Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is window.templates.org


def test_manual_apply_has_deterministic_keyboard_focus_order(
    expert_tabs, window, qapp
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.shell.set_current_key("manual-apply")
    window.show()
    window.activateWindow()
    qapp.processEvents()
    window.apply.target_edit.setFocus()
    QTest.keyClick(window.apply.target_edit, Qt.Key.Key_Tab)
    qapp.processEvents()

    assert qapp.focusWidget() is window.apply.target_browse


# --- templates tab -----------------------------------------------------------


def test_preview_shows_the_rendered_value(window):
    window.templates.kind.setCurrentIndex(0)
    window.templates.update_preview()
    assert "Do a thing" in window.templates.preview.toPlainText()


def test_editing_a_template_updates_the_preview_and_profile(window):
    window.templates.editor.setPlainText("CUSTOM {{ base }}")

    assert window.templates.preview.toPlainText().startswith("CUSTOM")
    assert window.state.profile.templates["org"].title == "CUSTOM {{ base }}"


def test_broken_template_reports_inline_and_does_not_raise(window):
    window.templates.editor.setPlainText("{{ nope_undefined }}")

    assert "not valid yet" in window.templates.status.text()
    assert window.templates.preview.toPlainText() == ""


def test_recovering_from_a_broken_template(window):
    window.templates.editor.setPlainText("{{ nope_undefined }}")
    window.templates.editor.setPlainText("FIXED {{ base }}")

    assert "not valid yet" not in window.templates.status.text()
    assert window.templates.preview.toPlainText().startswith("FIXED")


def test_use_builtin_clears_the_override(window):
    window.templates.editor.setPlainText("CUSTOM")
    assert "org" in window.state.profile.templates

    window.templates._reset()
    assert "org" not in window.state.profile.templates


def test_template_override_is_scoped_to_its_org(window, tmp_path):
    two = tmp_path / "two.ini"
    two.write_bytes(
        (BOM + "Alpha_x_title=A <EM4>[1 Rep]</EM4>\nBeta_y_title=B <EM4>[2 Rep]</EM4>\n").encode()
    )
    window.state.set_contracts(contracts_ini.load(two))

    window.templates.org.setCurrentIndex(window.templates.org.findData("alpha"))
    window.templates.editor.setPlainText("ALPHA ONLY")

    values = window.state.render().values
    assert values["Alpha_x_title"] == "ALPHA ONLY"
    assert values["Beta_y_title"] != "ALPHA ONLY"


def test_custom_wording_uses_reusable_summary_and_section_components(window):
    window.templates.editor.setPlainText("CUSTOM {{ base }}")

    assert window.templates.override_metric.value.text() == "1"
    assert window.templates.selection_metric.value.text() == "Custom"
    assert window.templates.preview_metric.value.text().isdigit()
    assert window.templates.editor_section.property("component") == "section-card"
    assert window.templates.preview_section.property("component") == "section-card"
    assert window.templates.status.property("tone") == "success"


def test_invalid_custom_wording_is_announced_as_blocked(window):
    window.templates.editor.setPlainText("{{ undefined_value }}")

    assert window.templates.preview_metric.value.text() == "Invalid"
    assert window.templates.status.property("tone") == "danger"
    assert "nothing can be applied" in window.templates.preview_metric.accessibleDescription()


# --- apply tab ---------------------------------------------------------------


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "global.ini"
    path.write_bytes(STOCK.encode("utf-8"))
    return path


def test_plan_reports_counts_and_writes_nothing(window, target):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))

    result = window.apply.refresh_plan()

    assert result is not None and result.updated
    assert target.read_bytes() == before
    assert "updated" in window.apply.plan_label.text()


def test_manual_preview_is_a_bound_serializable_c3_plan(expert_tabs, window, target):
    from starcompanion.inject import InjectionPlan

    window.apply.target_edit.setText(str(target))
    result = window.apply.refresh_plan()
    restored = InjectionPlan.loads(result.dumps())

    assert result.plan_id and restored.plan_id == result.plan_id
    assert result.target == str(target.resolve())
    assert result.target_fingerprint.sha256
    assert result.desired_sha256
    assert result.source_precedence == [
        "stock", "language-overlay", "import", "generated", "user"
    ]
    assert any(
        source["winner"] == "profile:default" and source["winner_kind"] == "generated"
        for source in result.sources.values()
    )
    assert window.apply.plan_view.changed_metric.value.text() != "—"
    assert window.apply.plan_detail.topLevelItemCount() > 0


def test_external_target_change_invalidates_reviewed_manual_apply(
    expert_tabs, window, target, monkeypatch
):
    original = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    result = window.apply.refresh_plan()
    assert result is not None
    external = original + b"External=preserve me\n"
    target.write_bytes(external)
    monkeypatch.setattr(window.apply, "confirm", lambda result: True)
    failures = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: failures.append(args))

    window.apply.apply_changes()

    assert failures
    assert target.read_bytes() == external
    assert not (target.parent / "backups" / ".apply-journal.json").exists()


def test_safe_interrupted_manual_write_is_guided_and_resolvable(
    expert_tabs, window, target, tmp_path, monkeypatch
):
    from starcompanion.transactions import TransactionJournal, bytes_sha256, fingerprint

    window.state.backup_dir = tmp_path / "backups"
    window.apply.target_edit.setText(str(target))
    journal = TransactionJournal(
        window.state.backup_dir / ".apply-journal.json",
        window.state.backup_dir / "last-operation.json",
    )
    before = fingerprint(target)
    journal.begin(
        operation="apply",
        plan_id="a" * 64,
        target=target,
        before=before,
        after_sha256=bytes_sha256(b"different intended result"),
    )
    window.apply.refresh_recovery()

    assert window.apply.recovery_metric.value.text() == "Not Applied"
    assert window.apply.resolve_button.isEnabled()
    assert not window.apply.apply_button.isEnabled()

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    window.apply.resolve_recovery()

    assert not journal.journal_path.exists()
    assert window.apply.recovery_metric.value.text() == "Clean"


def test_unknown_interrupted_target_state_blocks_automatic_recovery(
    expert_tabs, window, target, tmp_path
):
    from starcompanion.transactions import TransactionJournal, bytes_sha256, fingerprint

    window.state.backup_dir = tmp_path / "backups"
    window.apply.target_edit.setText(str(target))
    journal = TransactionJournal(
        window.state.backup_dir / ".apply-journal.json",
        window.state.backup_dir / "last-operation.json",
    )
    journal.begin(
        operation="apply",
        plan_id="b" * 64,
        target=target,
        before=fingerprint(target),
        after_sha256=bytes_sha256(b"intended"),
    )
    target.write_bytes(b"unknown external state")

    window.apply.refresh_recovery()

    assert window.apply.recovery_metric.value.text() == "Attention"
    assert window.apply.recovery_notice.property("tone") == "danger"
    assert not window.apply.resolve_button.isEnabled()
    assert not window.apply.restore_button.isEnabled()


def test_manual_recovery_lists_only_target_scoped_ordinary_backup_files(
    expert_tabs, window, target, tmp_path
):
    directory = tmp_path / "backups"
    directory.mkdir()
    valid = directory / "global.20260804-120000.ini"
    valid.write_bytes(target.read_bytes())
    (directory / "other.20260804-120000.ini").write_bytes(target.read_bytes())
    outside = tmp_path / "outside.ini"
    outside.write_bytes(target.read_bytes())
    linked = directory / "global.20260804-130000.ini"
    try:
        linked.symlink_to(outside)
    except OSError:
        linked = None

    window.state.backup_dir = directory
    window.apply.target_edit.setText(str(target))
    window.apply.refresh_recovery()

    assert window.apply.backups.count() == 1
    assert str(valid.resolve()) in window.apply.backups.item(0).text()
    if linked is not None:
        assert str(linked) not in window.apply.backups.item(0).text()


def test_plan_without_a_target_explains_rather_than_failing(expert_tabs, window):
    # Start now auto-detects a real install, so clear it to test this path.
    window.apply.target_edit.setText("")
    window.state.set_target(None)
    assert window.apply.refresh_plan() is None
    assert "Choose which file to change" in window.apply.plan_label.text()


def test_plan_without_contracts_explains(expert_tabs, qapp, target):
    window = MainWindow()
    window.apply.target_edit.setText(str(target))
    assert window.apply.refresh_plan() is None
    assert "Start tab" in window.apply.plan_label.text()


def test_declining_the_confirmation_writes_nothing(expert_tabs, window, target, monkeypatch):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    monkeypatch.setattr(window.apply, "confirm", lambda result: False)

    window.apply.apply_changes()

    assert target.read_bytes() == before


def test_accepting_the_confirmation_writes_and_backs_up(expert_tabs, window, target, tmp_path, monkeypatch):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    window.state.backup_dir = tmp_path / "backups"
    monkeypatch.setattr(window.apply, "confirm", lambda result: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window.apply.apply_changes()

    assert target.read_bytes() != before
    assert LocalizationFile.load(target).get("Other") == "untouched"
    assert [p.read_bytes() for p in (tmp_path / "backups").glob("*.ini")] == [before]
    assert (tmp_path / "backups" / "last-operation.json").is_file()


def test_backup_list_and_restore_round_trip(expert_tabs, window, target, tmp_path, monkeypatch):
    original = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    window.state.backup_dir = tmp_path / "backups"
    monkeypatch.setattr(window.apply, "confirm", lambda result: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window.apply.apply_changes()

    window.apply.refresh_backups()
    assert window.apply.backups.count() == 1

    window.apply.backups.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.apply.restore_backup()

    assert target.read_bytes() == original
    assert len(list((tmp_path / "backups").glob("*.ini"))) == 2
    assert window.apply._journal().last_operation()["operation"] == "rollback"


def test_game_install_target_shows_a_warning(expert_tabs, window, tmp_path):
    install = tmp_path / "LIVE"
    (install / "data" / "Localization" / "english").mkdir(parents=True)
    (install / "Data.p4k").write_bytes(b"x")
    target = install / "data" / "Localization" / "english" / "global.ini"
    target.write_bytes(STOCK.encode("utf-8"))

    window.apply.target_edit.setText(str(target))

    # isVisibleTo, not isVisible: the window is never shown in a headless test.
    assert window.apply.install_warning.isVisibleTo(window.apply)
    assert "Star Citizen install" in window.apply.install_warning.text()


def test_ordinary_target_shows_no_warning(expert_tabs, window, target):
    window.apply.target_edit.setText(str(target))
    assert not window.apply.install_warning.isVisibleTo(window.apply)


def test_overwrite_without_stock_warns_instead_of_writing(expert_tabs, window, target, monkeypatch):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    window.apply.mode.setCurrentIndex(window.apply.mode.findData(MergeMode.OVERWRITE))
    monkeypatch.setattr(window.apply, "confirm", lambda result: True)

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    window.apply.apply_changes()

    assert warned and target.read_bytes() == before


# --- profile management ------------------------------------------------------


def test_loading_a_builtin_updates_every_tab(community_rewards, window):
    window.load_builtin_profile("minimal")

    assert window.state.profile.name == "minimal"
    assert not window.fields.boxes["reputation"].isChecked()
    assert window.windowTitle().endswith("minimal")


def test_profile_save_and_load_round_trips_through_the_ui(community_rewards, window, tmp_path):
    window.fields.boxes["scrip"].setChecked(False)
    combo = window.formatting.default_tag
    combo.setCurrentIndex(combo.findData("EM2"))

    path = tmp_path / "p.json"
    window.state.profile.save(path)

    fresh = MainWindow()
    fresh.load_profile(path)

    assert fresh.state.profile.fields.scrip is False
    assert fresh.formatting.default_tag.currentData() == "EM2"


def test_unreadable_profile_warns_rather_than_crashing(window, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 99}')
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))

    window.load_profile(bad)

    assert warned and window.state.profile.name == "default"


# --- against real data -------------------------------------------------------


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists(), reason="real sample not present"
)
def test_real_corpus_drives_the_window(qapp):
    window = MainWindow()
    window.state.set_contracts(contracts_ini.load(SAMPLES / "contracts.ini"))

    assert window.templates.org.count() == 39
    assert "770 contracts" in window.source.summary_text()

    window.templates.org.setCurrentIndex(window.templates.org.findData("foxwell"))
    window.templates.update_preview()
    assert window.templates.preview.toPlainText()

    assert len(window.state.render().values) == 1449


# --- start tab (the guided flow) ---------------------------------------------


@pytest.fixture
def fake_game(tmp_path):
    from starcompanion import install as installs

    game = tmp_path / "Roberts Space Industries" / "StarCitizen" / "LIVE"
    (game / "data" / "Localization" / "english").mkdir(parents=True)
    (game / installs.ARCHIVE_NAME).write_bytes(b"archive")
    (game / "data" / "Localization" / "english" / "global.ini").write_bytes(
        STOCK.encode("utf-8")
    )
    return installs.identify(game)


def test_start_tab_is_first_and_selected(window):
    assert window.tabs.tabText(0) == "Start here"
    assert window.tabs.currentIndex() == 0


def test_start_explains_when_no_game_is_found(window):
    window.start.install = None
    window.start.load_error = None
    window.start.refresh()

    text = window.start.game_status_text()
    assert "not found" in text.lower()
    assert "Choose folder" in text


def test_start_reports_a_found_game_in_plain_language(window, fake_game):
    window.start.install = fake_game
    window.start.refresh()

    text = window.start.game_status_text()
    assert "Found Star Citizen" in text and "LIVE" in text


def test_choosing_a_game_derives_the_file_to_modify(window, fake_game):
    """The user never types a path to global.ini."""
    window.start.install = fake_game
    window.start._adopt_install()

    assert window.state.target == fake_game.localization()


def test_update_button_needs_only_a_game(qapp, fake_game):
    """Contract data comes from the game itself, so nothing else is required."""
    fresh = MainWindow()
    fresh.start.install = None
    fresh.start.refresh()
    assert not fresh.start.go.isEnabled()

    fresh.start.install = fake_game
    fresh.start.refresh()
    assert fresh.start.go.isEnabled()


def test_missing_language_setting_is_warned_about(window, fake_game):
    """Without g_language the override is silently ignored in game."""
    window.start.install = fake_game
    window.start.refresh()

    assert window.start.language_warning.isVisibleTo(window.start)
    assert "g_language" in window.start.language_warning.text()


def test_language_warning_clears_once_configured(window, fake_game):
    fake_game.user_cfg.write_text("g_language = english\n")
    window.start.install = fake_game
    window.start.refresh()

    assert not window.start.language_warning.isVisibleTo(window.start)


def test_choosing_a_label_style_sets_the_title_prefix(window):
    window.start.look.setCurrentIndex(window.start.look.findData("org"))
    assert window.state.profile.formatting.title.prefix == "org"

    window.start.look.setCurrentIndex(window.start.look.findData("none"))
    assert window.state.profile.formatting.title.prefix == "none"


def test_update_without_a_game_explains_rather_than_failing(window, monkeypatch):
    window.start.install = None
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))

    window.start.update_game()

    assert shown and "Choose folder" in shown[0][2]


def test_update_reads_the_game_when_contracts_are_missing(qapp, fake_game, monkeypatch):
    """Pressing the button just works: reading happens on demand."""
    fresh = MainWindow()
    fresh.start.install = fake_game
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))

    fresh.start.update_game()
    assert fresh.start.wait_for_jobs()

    # The fixture's archive is not a real p4k, so the read fails and says so
    # rather than silently doing nothing.
    assert warned and "Could not read" in warned[0][1]


def test_guided_update_creates_clean_install_override(window, tmp_path, monkeypatch):
    """The normal install has stock strings in Data.p4k and no loose INI."""
    import p4kbuilder as B
    from starcompanion.install import GameInstall

    root = tmp_path / "StarCitizen" / "LIVE"
    root.mkdir(parents=True)
    (root / "Data.p4k").write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", STOCK.encode("utf-8"))
        .build()
    )
    install = GameInstall(root=root, channel="LIVE", version="test")
    window.start.install = install
    window.start._adopt_install()
    _wait_until(
        QApplication.instance(),
        lambda: window.state.user_overrides_ready,
        message="guided-update user.ini scope did not become ready",
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *a, **k: pytest.fail(f"unexpected error dialog: {a}"),
    )

    assert not install.localization().exists()
    window.start.update_game()
    assert window.start.wait_for_jobs()

    written = LocalizationFile.load(install.localization())
    assert written.get("Other") == "untouched"
    assert written.get("Org_x_title") != "Original"


def test_undo_reports_when_there_is_nothing_to_undo(window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))

    window.start.undo_last()

    assert shown and "nothing" in shown[0][1].lower()


def test_read_button_and_its_instruction_are_in_the_same_step(qapp, fake_game):
    """The button to read the game and the text telling you to press it must
    not live under different headings."""
    fresh = MainWindow()
    fresh.start.install = fake_game
    fresh.start.refresh()

    assert "Read contracts from my game" in fresh.start.read_button.text()
    assert "Read contracts from my game" in fresh.start.contracts_status_text()
    # Step 2 is only about the optional reward numbers.
    assert "Read contracts" not in fresh.start.data_status_text()


def test_read_button_offers_a_re_read_once_contracts_are_loaded(window, fake_game):
    window.start.install = fake_game
    window.start.refresh()
    assert "again" in window.start.read_button.text()


def test_reading_again_bypasses_the_cache(qapp, fake_game, monkeypatch):
    """Pressing the button deliberately should look at the archive again."""
    from starcompanion import store
    import starcompanion.gui.tabs.start as start_module

    fresh = MainWindow()
    fresh.start.install = fake_game

    monkeypatch.setattr(store, "load", lambda install: pytest.fail("cache was used"))
    calls = []
    monkeypatch.setattr(
        start_module,
        "read_contracts",
        lambda install, token, reporter: calls.append(install) or _empty_set(),
    )
    monkeypatch.setattr(store, "save", lambda install, contracts: None)

    fresh.start.read_game(force=True)
    assert fresh.start.wait_for_jobs()
    assert calls, "the archive should have been read"


def test_progress_event_updates_dialog(qapp):
    from PySide6.QtWidgets import QProgressDialog
    from starcompanion.tasks import OperationStage, ProgressEvent

    dialog = QProgressDialog("", "Cancel", 0, 1000)
    event = ProgressEvent(OperationStage.INDEX_ARCHIVE, "Indexing 50 of 100", 50, 100)

    MainWindow().start._show_progress(dialog, event)

    assert dialog.labelText() == "Indexing 50 of 100"
    assert 0 < dialog.value() < 1000


def test_cancelling_background_read_changes_nothing(qapp, fake_game, monkeypatch):
    import time
    import starcompanion.gui.tabs.start as start_module

    def cancellable_read(install, token, reporter):
        while True:
            token.checkpoint()
            time.sleep(0.001)

    monkeypatch.setattr(start_module, "read_contracts", cancellable_read)
    fresh = MainWindow()
    fresh.start.install = fake_game
    fresh.start.read_game(force=True)
    job = next(iter(fresh.start._jobs))

    job.cancel()

    assert fresh.start.wait_for_jobs()
    assert fresh.state.contracts is None
    assert "Nothing was changed" in fresh.start.footer.text()


def test_window_close_cancels_and_joins_worker(qapp, fake_game, monkeypatch):
    import time
    import starcompanion.gui.tabs.start as start_module

    def cancellable_read(install, token, reporter):
        while True:
            token.checkpoint()
            time.sleep(0.001)

    monkeypatch.setattr(start_module, "read_contracts", cancellable_read)
    fresh = MainWindow()
    fresh.start.install = fake_game
    fresh.start.read_game(force=True)
    job = next(iter(fresh.start._jobs))

    fresh.close()

    assert not job.is_running
    assert not fresh.start._jobs
    assert "stopped safely" in fresh.start.operation_status


def test_slow_close_explains_that_it_is_waiting_safely(qapp, fake_game, monkeypatch):
    import threading
    import time
    from PySide6.QtWidgets import QProgressDialog
    import starcompanion.gui.tabs.start as start_module

    started = threading.Event()

    def slow_checkpoint(install, token, reporter):
        started.set()
        time.sleep(0.6)
        token.checkpoint()

    labels = []
    original = QProgressDialog.setLabelText

    def remember_label(dialog, text):
        labels.append(text)
        return original(dialog, text)

    monkeypatch.setattr(start_module, "read_contracts", slow_checkpoint)
    monkeypatch.setattr(QProgressDialog, "setLabelText", remember_label)
    fresh = MainWindow()
    fresh.start.install = fake_game
    fresh.start.read_game(force=True)
    deadline = time.monotonic() + 1
    while not started.is_set() and time.monotonic() < deadline:
        qapp.processEvents()

    fresh.close()

    assert any("Still waiting" in label for label in labels)
    assert not fresh.start._jobs


def _empty_set():
    from starcompanion.model import ContractSet

    return ContractSet()


def test_contracts_status_reports_what_was_read(window, fake_game):
    window.start.install = fake_game
    window.start.refresh()
    assert "Read 1 contracts" in window.start.contracts_status_text()


def test_step_two_only_talks_about_reward_numbers(community_rewards, window, fake_game):
    window.start.install = fake_game
    window.start.refresh()
    text = window.start.data_status_text()
    assert "Reward numbers" in text or "reward" in text.lower()


# --- explaining what things do -----------------------------------------------


def test_bold_and_italic_show_a_real_example(window):
    """Those two are ordinary formatting, so an example can be truthful."""
    combo = window.formatting.default_tag
    for tag in ("b", "i"):
        combo.setCurrentIndex(combo.findData(tag))
        assert window.formatting.example.isVisibleTo(window.formatting)
        assert "Reputation Awarded: 250" in window.formatting.example.text()
        assert "exactly how it will look" in window.formatting.example_note.text()


def test_highlight_styles_show_no_example_at_all(window):
    """Four identical boxes would suggest the four styles are identical.

    The game draws them and does not say how, so the app says so instead of
    inventing a preview.
    """
    combo = window.formatting.default_tag
    for tag in ("EM", "EM1", "EM2", "EM3", "EM4"):
        combo.setCurrentIndex(combo.findData(tag))
        assert not window.formatting.example.isVisibleTo(window.formatting)

    note = window.formatting.example_note.text()
    assert "cannot show you" in note
    assert "Highlight 4" in note, "should still point at the sensible default"


def test_custom_wording_explains_itself(window):
    assert not window.templates.explainer.isVisibleTo(window.templates)

    window.templates.show_help.setChecked(True)
    help_text = window.templates.explainer.text()

    assert window.templates.explainer.isVisibleTo(window.templates)
    assert "{{ base }}" in help_text
    assert "[Foxwell 3]" in help_text, "shows the result, not just the syntax"


def test_custom_wording_is_labelled_advanced(window):
    tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Advanced: custom wording" in tabs


def test_advanced_apply_says_what_it_is_for(expert_tabs, window):
    """A tab called "Advanced: apply" needs to explain when to use it."""
    from PySide6.QtWidgets import QLabel

    intro = next(
        l.text() for l in window.apply.findChildren(QLabel)
        if l.text().startswith("You do not need this tab")
    )
    assert "PTU" in intro
    assert "older backup" in intro
    assert "Update my game" in intro, "should point at the simple path instead"


def test_merge_modes_are_described_by_consequence(expert_tabs, window):
    shown = [window.apply.mode.itemText(i) for i in range(window.apply.mode.count())]
    assert not any("global.ini" in text for text in shown)
    assert any("leave anything else in the file alone" in text for text in shown)


def test_backup_and_recovery_is_available_by_default(qapp):
    """G2 makes guarded restore discoverable without enabling expert mode."""
    tabs_shown = [
        MainWindow().tabs.tabText(i) for i in range(MainWindow().tabs.count())
    ]
    assert "Backup and recovery" in tabs_shown


def test_advanced_apply_can_be_switched_back_on(expert_tabs, qapp):
    fresh = MainWindow()
    tabs_shown = [fresh.tabs.tabText(i) for i in range(fresh.tabs.count())]
    assert "Backup and recovery" in tabs_shown


# --- G2 onboarding, ownership, settings, diagnostics, and help ------------


def _blueprint_contracts():
    from starcompanion.model import BlueprintPool, Contract, ContractSet, Org, Reward

    org = Org("local", "Local Mission Giver")
    contract = Contract(
        "Local_Blueprint_Mission",
        org,
        "Delivery",
        reward=Reward(
            blueprint_pools=[
                BlueprintPool(
                    items=["Coda Pistol", "Norfield"],
                    item_ids={
                        "Coda Pistol": "11111111-1111-1111-1111-111111111111",
                        "Norfield": "22222222-2222-2222-2222-222222222222",
                    },
                    item_categories={"Coda Pistol": "weapons", "Norfield": "components"},
                )
            ]
        ),
    )
    return ContractSet([contract], {org.id: org})


def _wait_for_jobs(qapp, owner, *, timeout=10.0):
    deadline = time.monotonic() + timeout
    while owner._jobs and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    if not owner._jobs:
        return
    for job in tuple(owner._jobs):
        job.shutdown(1000)
    qapp.processEvents()
    pytest.fail("background GUI operation did not finish before bounded shutdown")


def test_g2_discovers_and_switches_installed_channels_off_the_gui_thread(
    window, qapp, tmp_path, monkeypatch
):
    from PySide6.QtCore import QThread
    from starcompanion.install import GameInstall

    roots = []
    for channel in ("LIVE", "PTU"):
        root = tmp_path / "StarCitizen" / channel
        root.mkdir(parents=True)
        (root / "Data.p4k").write_bytes(b"fixture")
        roots.append(GameInstall(root, channel, "test"))
    threads = []

    def discover(**kwargs):
        threads.append(QThread.currentThread())
        kwargs["checkpoint"]()
        return roots

    monkeypatch.setattr("starcompanion.install.find_installs", discover)
    window.start.discover_channels()
    _wait_for_jobs(qapp, window.start)

    assert window.start.channel_selector.count() == 2
    assert threads and all(thread is not qapp.thread() for thread in threads)
    window.start.channel_selector.setCurrentIndex(1)
    assert window.start.install.channel == "PTU"
    assert window.state.target == roots[1].localization()


def test_g2_blueprint_tracker_uses_c4_queries_and_background_log_scan(
    window, qapp, tmp_path, monkeypatch
):
    from starcompanion.blueprints import OwnershipFilter
    from starcompanion.ownership import OwnershipStore

    root = tmp_path / "StarCitizen" / "LIVE"
    root.mkdir(parents=True)
    target = root / "data" / "Localization" / "english" / "global.ini"
    window.state.set_contracts(_blueprint_contracts())
    window.state.set_target(target)
    window.blueprints.load_ownership()
    _wait_for_jobs(qapp, window.blueprints)
    assert window.blueprints.model.rowCount() == 2

    (root / "Game.log").write_text(
        '<2026-03-26T17:15:41.684Z> [Notice] <SHUDEvent_OnNotification> '
        'Added notification "Received Blueprint: Coda Pistol: " [23] to queue. '
        '[Missions][Comms]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window.blueprints.scan_logs()
    _wait_for_jobs(qapp, window.blueprints)
    # The scan-preview success callback starts the revision-checked save job.
    _wait_for_jobs(qapp, window.blueprints)

    saved = OwnershipStore("LIVE").load()
    assert len(saved.records) == 1
    window.blueprints.ownership_filter.setCurrentIndex(
        window.blueprints.ownership_filter.findData(OwnershipFilter.OWNED.value)
    )
    assert window.blueprints.model.rowCount() == 1
    assert window.blueprints.model.rows[0].entry.name == "Coda Pistol"


def test_g2_support_builds_inspectable_redacted_diagnostics_in_background(window, qapp):
    from PySide6.QtCore import QThread
    import starcompanion.gui.tabs.support as support_module

    threads = []
    original = support_module.build_diagnostics

    def tracked(*args, **kwargs):
        threads.append(QThread.currentThread())
        return original(*args, **kwargs)

    support_module.build_diagnostics = tracked
    try:
        window.support.build_report()
        _wait_for_jobs(qapp, window.support)
    finally:
        support_module.build_diagnostics = original

    preview = window.support.diagnostics_view.toPlainText()
    assert threads and all(thread is not qapp.thread() for thread in threads)
    assert '"absolute_paths": "redacted"' in preview
    assert '"ownership": "excluded"' in preview
    assert str(Path.home()) not in preview
    assert window.support.export_diagnostics_button.isEnabled()


def test_g2_settings_export_uses_c5_manifest_archive_in_background(
    window, qapp, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QFileDialog
    from starcompanion.portability import plan_settings_import
    from starcompanion.user_edits import UserEditStore

    UserEditStore("LIVE", "english").save({"User_Key": "private wording"})
    destination = tmp_path / "portable.zip"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(destination), "ZIP (*.zip)"),
    )
    window.support.export_settings()
    _wait_for_jobs(qapp, window.support)

    assert destination.is_file()
    plan = plan_settings_import(destination, tmp_path / "restore")
    assert any(item.archive_path.endswith("user.ini") for item in plan.items)
    assert "private wording" not in window.support.status.text()


def test_g2_settings_import_is_preview_first_then_reloads_verified_preferences(
    window, qapp, tmp_path, monkeypatch
):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QFileDialog
    from starcompanion.portability import (
        PreferencesStore,
        plan_settings_export,
        write_settings_archive,
    )

    source = tmp_path / "source-settings"
    PreferencesStore(source).save(
        {"theme": "light", "ui_schema": 1, "last_page": "support"}
    )
    archive = tmp_path / "incoming.zip"
    write_settings_archive(plan_settings_export(source), archive)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(archive), "ZIP (*.zip)"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.support.preview_settings_import()
    _wait_for_jobs(qapp, window.support)
    assert "CHANGE" in window.support.settings_preview.toPlainText()
    assert window.support.apply_settings_button.isEnabled()
    assert window.ui_preferences.theme == "dark"

    load_threads = []
    original_load = PreferencesStore.load

    def tracked_load(store):
        load_threads.append(QThread.currentThread())
        return original_load(store)

    monkeypatch.setattr(PreferencesStore, "load", tracked_load)
    window.support.apply_settings()
    _wait_for_jobs(qapp, window.support)

    assert window.ui_preferences.theme == "light"
    assert window.support._import_plan is None
    assert load_threads and all(thread is not qapp.thread() for thread in load_threads)


def test_g2_damaged_ownership_requires_explicit_validated_backup_recovery(
    window, qapp, tmp_path, monkeypatch
):
    from starcompanion.ownership import OwnershipState, OwnershipStore

    store = OwnershipStore("LIVE")
    state = OwnershipState("LIVE")
    store.save(state)
    store.save(state)
    store.path.write_text("{damaged", encoding="utf-8")
    target = tmp_path / "StarCitizen" / "LIVE" / "data" / "Localization" / "english" / "global.ini"
    window.state.set_target(target)
    window.blueprints.load_ownership()
    _wait_for_jobs(qapp, window.blueprints)

    assert window.blueprints.ownership is None
    assert window.blueprints.recover_button.isVisibleTo(window.blueprints)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window.blueprints.recover_ownership()
    _wait_for_jobs(qapp, window.blueprints)

    assert window.blueprints.ownership is not None
    assert not window.blueprints.recover_button.isVisibleTo(window.blueprints)


def test_g2_offline_help_searches_bundled_text_without_network(window, qapp):
    from PySide6.QtTest import QTest

    window.support.pages.setCurrentIndex(3)
    window.support.help_search.setText("telemetry")
    QTest.qWait(150)
    qapp.processEvents()

    assert window.support.help_results.count() >= 1
    assert "no telemetry" in window.support.help_text.toPlainText().casefold()


def test_g2_profile_manager_keeps_output_profile_separate_from_ui_preferences(window):
    before_theme = window.ui_preferences.theme
    window.support.profile_builtin.setCurrentIndex(
        window.support.profile_builtin.findData("minimal")
    )

    assert window.state.profile.name == "minimal"
    assert window.ui_preferences.theme == before_theme
