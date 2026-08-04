"""GUI tests.

Run headless via the offscreen platform. They cover the wiring the GUI adds --
widget/profile binding, preview robustness, and the write gates -- not the
pipeline behaviour already covered elsewhere.
"""

import os
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


@pytest.fixture
def contracts(tmp_path):
    path = tmp_path / "contracts.ini"
    path.write_bytes(MINI.encode("utf-8"))
    return contracts_ini.load(path)


@pytest.fixture
def window(qapp, contracts):
    w = MainWindow()
    w.state.set_contracts(contracts)
    return w


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
    assert "1 contracts" in text and "2 localization keys" in text


def test_source_summary_before_loading(community_rewards, qapp):
    assert "No contract data" in MainWindow().source.summary_text()


def test_loading_bad_path_does_not_crash(community_rewards, window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window.source.path_edit.setText(str(tmp_path / "nope.ini"))
    window.source.load_contracts()
    assert window.state.contracts is not None  # previous data intact


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


def test_step_numbering_has_no_gap_when_step_two_is_hidden(qapp):
    from PySide6.QtWidgets import QGroupBox

    fresh = MainWindow()
    titles = [
        b.title() for b in fresh.start.findChildren(QGroupBox)
        if b.isVisibleTo(fresh.start)
    ]
    assert titles[0].startswith("Step 1")
    assert titles[1].startswith("Step 2"), "must not jump from 1 to 3"


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
    assert [p.read_bytes() for p in (tmp_path / "backups").iterdir()] == [before]


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


def test_advanced_apply_is_hidden_by_default(qapp):
    """The Start tab covers the whole job; this screen is for rare cases."""
    tabs_shown = [
        MainWindow().tabs.tabText(i) for i in range(MainWindow().tabs.count())
    ]
    assert "Advanced: apply" not in tabs_shown


def test_advanced_apply_can_be_switched_back_on(expert_tabs, qapp):
    fresh = MainWindow()
    tabs_shown = [fresh.tabs.tabText(i) for i in range(fresh.tabs.count())]
    assert "Advanced: apply" in tabs_shown
