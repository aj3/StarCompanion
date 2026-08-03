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


def test_community_rewards_are_hidden_by_default(qapp):
    """The project reads the game; someone else's download is not the main flow."""
    fresh = MainWindow()
    tabs = [fresh.tabs.tabText(i) for i in range(fresh.tabs.count())]

    assert "What to show" not in tabs
    assert "Advanced: data" not in tabs
    assert not fresh.start.data_step.isVisibleTo(fresh.start)


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

    assert "Template error" in window.templates.status.text()
    assert window.templates.preview.toPlainText() == ""


def test_recovering_from_a_broken_template(window):
    window.templates.editor.setPlainText("{{ nope_undefined }}")
    window.templates.editor.setPlainText("FIXED {{ base }}")

    assert "Template error" not in window.templates.status.text()
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


def test_plan_without_a_target_explains_rather_than_failing(window):
    # Start now auto-detects a real install, so clear it to test this path.
    window.apply.target_edit.setText("")
    window.state.set_target(None)
    assert window.apply.refresh_plan() is None
    assert "Choose a global.ini" in window.apply.plan_label.text()


def test_plan_without_contracts_explains(qapp, target):
    window = MainWindow()
    window.apply.target_edit.setText(str(target))
    assert window.apply.refresh_plan() is None
    assert "Source tab" in window.apply.plan_label.text()


def test_declining_the_confirmation_writes_nothing(window, target, monkeypatch):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    monkeypatch.setattr(window.apply, "confirm", lambda result: False)

    window.apply.apply_changes()

    assert target.read_bytes() == before


def test_accepting_the_confirmation_writes_and_backs_up(window, target, tmp_path, monkeypatch):
    before = target.read_bytes()
    window.apply.target_edit.setText(str(target))
    window.state.backup_dir = tmp_path / "backups"
    monkeypatch.setattr(window.apply, "confirm", lambda result: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window.apply.apply_changes()

    assert target.read_bytes() != before
    assert LocalizationFile.load(target).get("Other") == "untouched"
    assert [p.read_bytes() for p in (tmp_path / "backups").iterdir()] == [before]


def test_backup_list_and_restore_round_trip(window, target, tmp_path, monkeypatch):
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


def test_game_install_target_shows_a_warning(window, tmp_path):
    install = tmp_path / "LIVE"
    (install / "data" / "Localization" / "english").mkdir(parents=True)
    (install / "Data.p4k").write_bytes(b"x")
    target = install / "data" / "Localization" / "english" / "global.ini"
    target.write_bytes(STOCK.encode("utf-8"))

    window.apply.target_edit.setText(str(target))

    # isVisibleTo, not isVisible: the window is never shown in a headless test.
    assert window.apply.install_warning.isVisibleTo(window.apply)
    assert "Star Citizen install" in window.apply.install_warning.text()


def test_ordinary_target_shows_no_warning(window, target):
    window.apply.target_edit.setText(str(target))
    assert not window.apply.install_warning.isVisibleTo(window.apply)


def test_overwrite_without_stock_warns_instead_of_writing(window, target, monkeypatch):
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

    # The fixture's archive is not a real p4k, so the read fails and says so
    # rather than silently doing nothing.
    assert warned and "Could not read" in warned[0][1]


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
    from starcompanion.sources import game_strings

    fresh = MainWindow()
    fresh.start.install = fake_game

    monkeypatch.setattr(store, "load", lambda install: pytest.fail("cache was used"))
    calls = []
    monkeypatch.setattr(
        game_strings, "from_install",
        lambda install, language="english": calls.append(install) or _empty_set(),
    )
    monkeypatch.setattr(store, "save", lambda install, contracts: None)

    fresh.start.read_game(force=True)
    assert calls, "the archive should have been read"


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
