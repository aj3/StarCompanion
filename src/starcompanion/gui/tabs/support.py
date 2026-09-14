"""G2 profiles, portable settings, redacted diagnostics, and offline help."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...config import builtin_profiles, load_builtin
from ...data_location import (
    DataMigrationPlan,
    apply_data_migration,
    plan_data_migration,
    portable_data_root,
    resolve_data_location,
)
from ...diagnostics import build_diagnostics, render_diagnostics, write_diagnostics
from ...portability import (
    SettingsImportPlan,
    PreferencesStore,
    apply_settings_import,
    plan_settings_export,
    plan_settings_import,
    recover_settings_restore,
    settings_recovery_status,
    write_settings_archive,
)
from ...user_edits import data_dir
from ..components import NoticeBanner, SectionCard, Tone
from ..event_log import EventLog, LEVELS, write_event_export
from ..jobs import QtOperationJob
from ..state import AppState
from ..ui_text import LOCALE_LABELS, normalize_ui_locale


HELP_ARTICLES = (
    (
        "First run",
        "Start on Overview. Choose or discover an installed channel, read its local contract data, "
        "review presentation choices, then use Update my game. Extraction and preparation are "
        "cancellable; a confirmation and backup precede every game-file replacement.",
    ),
    (
        "Channels and languages",
        "Localization, user wording, caches, and operation plans stay scoped to the selected "
        "channel and language. Blueprint ownership reviews LIVE and HOTFIX together by default; "
        "the visible option can separate them, while PTU, EPTU, and TECH-PREVIEW always remain isolated."
        " Discover installed languages before activation. StarCompanion changes only the effective "
        "g_language line in USER.cfg after preview, backup, and confirmation. Restore stock removes "
        "only the selected loose localization override through the same safe plan.",
    ),
    (
        "Blueprint ownership",
        "The tracker builds its catalog from local mission data and incrementally scans Game.log and "
        "logbackups. It stores acquisition evidence locally; unresolved names are never guessed.",
    ),
    (
        "Backups and recovery",
        "Backup & recovery lists only ordinary target-scoped INI files. Restore fingerprints both the "
        "selected backup and current target, preserves the current file, journals replacement, and "
        "verifies the final digest.",
    ),
    (
        "Settings portability",
        "Export creates a bounded manifest-verified archive of interface preferences, user wording, "
        "and language packs. Import is preview-first, rejects unsafe paths and duplicate members, and "
        "requires explicit replacement approval for conflicts. Application data can also be copied "
        "to a validated custom or packaged beside-executable root; the source remains untouched and "
        "the new root activates only after restart.",
    ),
    (
        "Privacy and diagnostics",
        "StarCompanion has no telemetry or automatic network access. Shareable diagnostics contain "
        "counts, versions, capability state, and fingerprints; paths, usernames, logs, ownership, game "
        "strings, and user-authored values are redacted or excluded.",
    ),
    (
        "Interface modes, tour, themes, and events",
        "Simple mode keeps only the existing Update and Undo workflow visible; Full mode restores every "
        "workspace. The replayable guided tour changes focus only. Four bundled themes share one reviewed "
        "stylesheet. Interface catalogs are bundled and independent from the game language. The event viewer "
        "stores at most 500 redacted interface events and never reads Game.log.",
    ),
    (
        "Validation and source precedence",
        "Stock localization is followed by generated profile output and then explicit user wording. "
        "The string editor shows every contribution and blocks invalid operation plans without "
        "inventing localization text. Copy visible rows exports only the filtered bounded projection "
        "and excludes hidden provenance.",
    ),
    (
        "Structured presentation",
        "Presentation profiles use validated labels, complete reward-section ordering, and number "
        "formatting by default. Version 1 profiles migrate automatically. Sandboxed per-mission-giver "
        "templates remain stored but affect output only after Advanced custom templates is explicitly enabled.",
    ),
)


@dataclass(frozen=True)
class SettingsExportSummary:
    destination: Path
    entries: int


@dataclass(frozen=True)
class SettingsApplyResult:
    plan: SettingsImportPlan | None
    preferences: dict[str, object]
    recovery: str | None = None


class SupportTab(QWidget):
    """Local administration tools with all filesystem work in Qt jobs."""

    settingsImported = Signal(object)
    interfaceLocaleChanged = Signal(str)
    tourRequested = Signal()

    def __init__(
        self,
        state: AppState,
        *,
        installs_provider=lambda: (),
        open_profile=None,
        save_profile=None,
        event_log: EventLog | None = None,
        interface_locale: str = "en-US",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.state = state
        self.installs_provider = installs_provider
        self.open_profile_action = open_profile
        self.save_profile_action = save_profile
        self.event_log = event_log if event_log is not None else EventLog(parent=self)
        self.interface_locale = normalize_ui_locale(interface_locale)
        self._jobs: set[QtOperationJob] = set()
        self._shutting_down = False
        self._diagnostics: dict[str, object] | None = None
        self._import_plan: SettingsImportPlan | None = None
        self._migration_plan: DataMigrationPlan | None = None
        self._session_data_root = data_dir()

        self.status = NoticeBanner(
            "Profile, portability, diagnostics, and help remain local to this computer.",
            tone=Tone.INFO,
        )
        self.pages = QTabWidget()
        self.pages.setAccessibleName("Settings diagnostics and offline help")
        self.pages.addTab(self._profile_page(), "Profile")
        self.pages.addTab(self._settings_page(), "Portability")
        self.pages.addTab(self._diagnostics_page(), "Diagnostics")
        self.pages.addTab(self._events_page(), "Event viewer")
        self.pages.addTab(self._help_page(), "Offline help")

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.pages, 1)

        state.profileChanged.connect(self._profile_changed)
        self._profile_changed()
        self._filter_help()
        self.event_log.changed.connect(self._refresh_events)
        self._refresh_events()

    def _profile_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.profile_summary = QLabel()
        self.profile_summary.setWordWrap(True)
        self.profile_summary.setProperty("role", "muted")
        self.profile_builtin = QComboBox()
        self.profile_builtin.setAccessibleName("Built-in output profile")
        self.profile_builtin.setAccessibleDescription(
            "Select a bundled profile; this changes rendered output, not interface preferences."
        )
        for name in builtin_profiles():
            self.profile_builtin.addItem(name.replace("-", " ").title(), name)
        self.profile_builtin.currentIndexChanged.connect(self._load_builtin)
        self.profile_open = QPushButton("Open profile…")
        self.profile_open.setAccessibleName("Open output profile")
        self.profile_open.setAccessibleDescription("Open a validated StarCompanion profile JSON file.")
        self.profile_open.clicked.connect(
            lambda: self.open_profile_action() if self.open_profile_action else None
        )
        self.profile_save = QPushButton("Save profile as…")
        self.profile_save.setAccessibleName("Save output profile")
        self.profile_save.setAccessibleDescription(
            "Save the current output profile separately from portable interface settings."
        )
        self.profile_save.clicked.connect(
            lambda: self.save_profile_action() if self.save_profile_action else None
        )
        actions = QHBoxLayout()
        actions.addWidget(self.profile_open)
        actions.addWidget(self.profile_save)
        actions.addStretch(1)
        section = SectionCard(
            "Output profile",
            "Profiles control generated contract text. Theme, navigation, ownership, and user wording are separate state.",
        )
        section.add_widget(self.profile_summary)
        section.add_widget(self.profile_builtin)
        section.add_layout(actions)
        layout.addWidget(section)
        self.interface_language = QComboBox()
        self.interface_language.setAccessibleName("Application interface language")
        self.interface_language.setAccessibleDescription(
            "Choose a bundled offline interface catalog independently from the selected game language."
        )
        for locale, label in LOCALE_LABELS.items():
            self.interface_language.addItem(label, locale)
        self.interface_language.setCurrentIndex(
            max(0, self.interface_language.findData(self.interface_locale))
        )
        self.interface_language.currentIndexChanged.connect(
            self._interface_language_selected
        )
        self.replay_tour_button = QPushButton("Replay guided tour")
        self.replay_tour_button.setAccessibleName("Replay the guided interface tour")
        self.replay_tour_button.setAccessibleDescription(
            "Walk through existing controls without reading, changing, or transmitting data."
        )
        self.replay_tour_button.clicked.connect(self.tourRequested)
        experience = SectionCard(
            "Interface experience",
            "Simple/full mode, theme, and interface language are portable UI preferences and never change generated game text.",
        )
        experience.add_widget(self.interface_language)
        experience.add_widget(self.replay_tour_button)
        layout.addWidget(experience)
        layout.addStretch(1)
        return page

    def _interface_language_selected(self, index: int) -> None:
        locale = self.interface_language.itemData(index)
        if isinstance(locale, str):
            self.interface_locale = normalize_ui_locale(locale)
            self.interfaceLocaleChanged.emit(self.interface_locale)

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.data_location_detail = QLabel()
        self.data_location_detail.setWordWrap(True)
        self.data_location_detail.setProperty("role", "muted")
        self.data_location_warning = NoticeBanner(tone=Tone.WARNING)
        self.choose_data_location_button = QPushButton("Choose data directory…")
        self.choose_data_location_button.setAccessibleName("Choose application data directory")
        self.choose_data_location_button.setAccessibleDescription(
            "Preview a bounded copy-only migration; the source remains untouched and the new root activates on restart."
        )
        self.choose_data_location_button.clicked.connect(self.choose_data_location)
        self.portable_mode_button = QPushButton("Use beside-executable data…")
        self.portable_mode_button.setAccessibleName("Use beside-executable portable data")
        self.portable_mode_button.setAccessibleDescription(
            "For packaged builds, preview copying portable state beside StarCompanion and enable it on restart."
        )
        self.portable_mode_button.clicked.connect(self.enable_portable_mode)
        self.apply_data_location_button = QPushButton("Apply reviewed data move…")
        self.apply_data_location_button.setProperty("role", "danger")
        self.apply_data_location_button.setAccessibleName("Apply reviewed data-directory migration")
        self.apply_data_location_button.setAccessibleDescription(
            "Copy only reviewed allowlisted files, preserve the source, and activate the new root after restart."
        )
        self.apply_data_location_button.setEnabled(False)
        self.apply_data_location_button.clicked.connect(self.apply_data_location)
        data_actions = QHBoxLayout()
        data_actions.addWidget(self.choose_data_location_button)
        data_actions.addWidget(self.portable_mode_button)
        data_actions.addWidget(self.apply_data_location_button)
        data_actions.addStretch(1)
        data_section = SectionCard(
            "Application data location",
            "User wording, ownership, settings, and local layout can be copied safely. Cache files are disposable and are not migrated.",
        )
        data_section.add_widget(self.data_location_detail)
        data_section.add_widget(self.data_location_warning)
        data_section.add_layout(data_actions)
        layout.addWidget(data_section)
        self.settings_detail = QLabel(
            "Exported archives include allowlisted preferences, channel/language user.ini files, and language packs."
        )
        self.settings_detail.setWordWrap(True)
        self.settings_detail.setProperty("role", "muted")
        self.settings_preview = QPlainTextEdit()
        self.settings_preview.setReadOnly(True)
        self.settings_preview.setAccessibleName("Portable settings preview")
        self.settings_preview.setAccessibleDescription(
            "Manifest outcomes for the selected settings archive; no values are displayed."
        )
        self.settings_preview.setPlaceholderText("Choose Import to validate and preview an archive.")
        self.export_settings_button = QPushButton("Export settings…")
        self.export_settings_button.setAccessibleName("Export portable settings archive")
        self.export_settings_button.setAccessibleDescription(
            "Validate allowlisted settings and write a manifest-verified archive in a background worker."
        )
        self.export_settings_button.clicked.connect(self.export_settings)
        self.import_settings_button = QPushButton("Import and preview…")
        self.import_settings_button.setAccessibleName("Import and preview settings archive")
        self.import_settings_button.setAccessibleDescription(
            "Validate an archive and show add, change, and unchanged outcomes without writing."
        )
        self.import_settings_button.clicked.connect(self.preview_settings_import)
        self.apply_settings_button = QPushButton("Apply reviewed import…")
        self.apply_settings_button.setProperty("role", "danger")
        self.apply_settings_button.setAccessibleName("Apply reviewed settings import")
        self.apply_settings_button.setAccessibleDescription(
            "Confirm and apply the exact validated import plan with rollback recovery."
        )
        self.apply_settings_button.setEnabled(False)
        self.apply_settings_button.clicked.connect(self.apply_settings)
        self.recover_settings_button = QPushButton("Recover interrupted import…")
        self.recover_settings_button.setAccessibleName("Recover interrupted settings import")
        self.recover_settings_button.setAccessibleDescription(
            "Use the C5 recovery journal to roll back or finalize only a verified interrupted import."
        )
        self.recover_settings_button.clicked.connect(self.recover_settings)
        actions = QHBoxLayout()
        actions.addWidget(self.export_settings_button)
        actions.addWidget(self.import_settings_button)
        actions.addWidget(self.apply_settings_button)
        actions.addWidget(self.recover_settings_button)
        actions.addStretch(1)
        section = SectionCard(
            "Safe settings portability",
            "Archive paths, sizes, hashes, compression ratios, scopes, and conflicts are verified by the C5 backend.",
        )
        section.add_widget(self.settings_detail)
        section.add_widget(self.settings_preview)
        section.add_layout(actions)
        layout.addWidget(section)
        layout.addStretch(1)
        self._refresh_data_location()
        return page

    def _refresh_data_location(self) -> None:
        location = resolve_data_location()
        self.data_location_detail.setText(
            f"Mode: {location.mode}\nCurrent root: {location.root}\n"
            "A migration changes future launches only; the current session keeps its opened stores."
        )
        self.data_location_warning.setText(location.warning or "")
        self.data_location_warning.setVisible(bool(location.warning))
        environment_locked = location.mode == "environment"
        self.choose_data_location_button.setEnabled(not environment_locked)
        packaged = bool(getattr(sys, "frozen", False))
        self.portable_mode_button.setEnabled(packaged and not environment_locked)
        if environment_locked:
            self.choose_data_location_button.setToolTip(
                "STARCOMPANION_DATA controls this process; remove that environment setting first."
            )
        elif not packaged:
            self.portable_mode_button.setToolTip(
                "Beside-executable mode is enabled only in a packaged StarCompanion build."
            )

    def choose_data_location(self) -> None:
        destination = QFileDialog.getExistingDirectory(
            self, "Choose a new StarCompanion data directory"
        )
        if not destination or self._jobs:
            return
        self._plan_data_location(Path(destination), "custom")

    def enable_portable_mode(self) -> None:
        if self._jobs or not getattr(sys, "frozen", False):
            return
        self._plan_data_location(portable_data_root(), "portable")

    def _plan_data_location(self, destination: Path, mode: str) -> None:
        source = self._session_data_root
        self._migration_plan = None
        self.apply_data_location_button.setEnabled(False)
        self._start_job(
            lambda token, _reporter: (
                token.checkpoint(),
                plan_data_migration(source, destination, mode=mode),
                token.checkpoint(),
            )[1],
            self._data_location_planned,
        )

    def _data_location_planned(self, plan: DataMigrationPlan) -> None:
        self._migration_plan = plan
        self.settings_preview.setPlainText(
            "Data-directory migration preview\n"
            f"Mode: {plan.mode}\n"
            f"Source: {plan.source_root}\n"
            f"Destination: {plan.destination_root}\n\n"
            + "\n".join(
                f"{item.outcome.upper():9} {item.relative_path} ({item.size:,} bytes)"
                for item in plan.entries
            )
        )
        self.apply_data_location_button.setEnabled(True)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Reviewed {len(plan.entries):,} allowlisted files; {len(plan.changes):,} require copying."
        )

    def apply_data_location(self) -> None:
        plan = self._migration_plan
        if plan is None or self._jobs:
            return
        if QMessageBox.question(
            self,
            "Apply reviewed data-directory migration?",
            f"Copy {len(plan.changes):,} file(s) to:\n{plan.destination_root}\n\n"
            "The source is not deleted. The new location takes effect after restart.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_job(
            lambda token, _reporter: (
                token.checkpoint(),
                apply_data_migration(plan, confirmed=True),
            )[1],
            lambda _result: self._data_location_applied(plan),
        )

    def _data_location_applied(self, plan: DataMigrationPlan) -> None:
        self._migration_plan = None
        self.apply_data_location_button.setEnabled(False)
        # Freeze new stores on the open root until this process exits. The
        # process-only override disappears before the next normal launch.
        os.environ["STARCOMPANION_DATA"] = str(self._session_data_root)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Data copied safely to {plan.destination_root}. Restart StarCompanion to use it; the original remains recoverable."
        )

    def _diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.diagnostics_view = QPlainTextEdit()
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setAccessibleName("Redacted diagnostics preview")
        self.diagnostics_view.setAccessibleDescription(
            "Shareable JSON excluding paths, usernames, logs, ownership, game strings, and user values."
        )
        self.diagnostics_view.setPlaceholderText("Build a fresh redacted report to preview it here.")
        self.build_diagnostics_button = QPushButton("Build redacted report")
        self.build_diagnostics_button.setProperty("role", "primary")
        self.build_diagnostics_button.setAccessibleName("Build redacted diagnostics report")
        self.build_diagnostics_button.setAccessibleDescription(
            "Inspect local install and portable-data metadata in a background worker."
        )
        self.build_diagnostics_button.clicked.connect(self.build_report)
        self.export_diagnostics_button = QPushButton("Export reviewed report…")
        self.export_diagnostics_button.setAccessibleName("Export redacted diagnostics")
        self.export_diagnostics_button.setAccessibleDescription(
            "Write exactly the report visible in this preview without adding private data."
        )
        self.export_diagnostics_button.setEnabled(False)
        self.export_diagnostics_button.clicked.connect(self.export_report)
        actions = QHBoxLayout()
        actions.addWidget(self.build_diagnostics_button)
        actions.addWidget(self.export_diagnostics_button)
        actions.addStretch(1)
        section = SectionCard(
            "Privacy-preserving diagnostics",
            "The report is offline, inspectable before export, and contains an explicit privacy manifest.",
        )
        section.add_widget(self.diagnostics_view, 1)
        section.add_layout(actions)
        layout.addWidget(section, 1)
        return page

    def _events_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.event_level = QComboBox()
        self.event_level.addItem("All levels", None)
        for level in LEVELS:
            self.event_level.addItem(level.title(), level)
        self.event_level.setAccessibleName("Application event level filter")
        self.event_level.setAccessibleDescription(
            "Filter the bounded in-memory redacted event list."
        )
        self.event_level.currentIndexChanged.connect(self._refresh_events)
        self.event_view = QPlainTextEdit()
        self.event_view.setReadOnly(True)
        self.event_view.setAccessibleName("Redacted application event viewer")
        self.event_view.setAccessibleDescription(
            "Shows only bounded event codes and redacted summaries; raw game logs and strings are excluded."
        )
        self.clear_events_button = QPushButton("Clear in-memory events")
        self.clear_events_button.setAccessibleName("Clear application events")
        self.clear_events_button.setAccessibleDescription(
            "Forget the bounded in-memory event ring without deleting or changing any files."
        )
        self.clear_events_button.clicked.connect(self.event_log.clear)
        self.export_events_button = QPushButton("Export redacted events…")
        self.export_events_button.setAccessibleName("Export redacted application events")
        self.export_events_button.setAccessibleDescription(
            "Write only the currently filtered bounded event records after choosing a destination."
        )
        self.export_events_button.clicked.connect(self.export_events)
        actions = QHBoxLayout()
        actions.addWidget(self.clear_events_button)
        actions.addWidget(self.export_events_button)
        actions.addStretch(1)
        section = SectionCard(
            "Bounded redacted event viewer",
            "The ring stores at most 500 local interface events. It never ingests Game.log, localization values, ownership, or raw exception text.",
        )
        section.add_widget(self.event_level)
        section.add_widget(self.event_view, 1)
        section.add_layout(actions)
        layout.addWidget(section, 1)
        return page

    def _refresh_events(self, *_args) -> None:
        level = self.event_level.currentData()
        lines = [
            f"{item.timestamp}  {item.level.upper():7}  {item.event}"
            + (f"  —  {item.detail}" if item.detail else "")
            for item in self.event_log.entries(level)
        ]
        self.event_view.setPlainText("\n".join(lines))
        self.export_events_button.setEnabled(bool(lines) and not self._jobs)

    def export_events(self) -> None:
        if self._jobs or not self.event_log.entries(self.event_level.currentData()):
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export redacted application events",
            "starcompanion-events.json",
            "JSON (*.json)",
        )
        if not destination:
            return
        payload = self.event_log.export_bytes(self.event_level.currentData())
        path = Path(destination)
        self._start_job(
            lambda token, _reporter: self._write_events(token, path, payload),
            lambda written: self._events_exported(written),
        )

    @staticmethod
    def _write_events(token, destination: Path, payload: bytes) -> Path:
        token.checkpoint()
        write_event_export(destination, payload)
        token.checkpoint()
        return destination

    def _events_exported(self, destination: Path) -> None:
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(f"Exported the reviewed redacted event list to {destination}.")

    def _help_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.help_search = QLineEdit()
        self.help_search.setPlaceholderText("Search bundled help")
        self.help_search.setClearButtonEnabled(True)
        self.help_search.setAccessibleName("Search offline help")
        self.help_search.setAccessibleDescription(
            "Searches bundled help text in memory and never opens a website."
        )
        self.help_search.textChanged.connect(lambda: self.help_timer.start())
        self.help_results = QListWidget()
        self.help_results.setAccessibleName("Offline help topics")
        self.help_results.setAccessibleDescription("Bundled help topics matching the local search.")
        self.help_results.currentRowChanged.connect(self._show_help)
        self.help_text = QPlainTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setAccessibleName("Offline help article")
        self.help_text.setAccessibleDescription("Selected bundled StarCompanion help article.")
        split = QHBoxLayout()
        split.addWidget(self.help_results, 1)
        split.addWidget(self.help_text, 2)
        section = SectionCard(
            "Searchable offline help",
            "All guidance is bundled with the application; no page view, query, or diagnostic is transmitted.",
        )
        section.add_widget(self.help_search)
        section.add_layout(split)
        layout.addWidget(section, 1)
        self.help_timer = QTimer(self)
        self.help_timer.setSingleShot(True)
        self.help_timer.setInterval(120)
        self.help_timer.timeout.connect(self._filter_help)
        return page

    def _profile_changed(self) -> None:
        profile = self.state.profile
        self.profile_summary.setText(
            f"Active profile: {profile.name}\n"
            f"Merge mode: {profile.injection.mode}\n"
            f"Wording mode: {profile.wording.mode}\n"
            "Structured rules, dormant templates, and field formatting remain inside this output profile only."
        )
        index = self.profile_builtin.findData(profile.name)
        self.profile_builtin.blockSignals(True)
        self.profile_builtin.setCurrentIndex(index if index >= 0 else -1)
        self.profile_builtin.blockSignals(False)

    def _load_builtin(self, index: int) -> None:
        name = self.profile_builtin.itemData(index)
        if name:
            self.state.set_profile(load_builtin(name))

    def export_settings(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export portable settings", "starcompanion-settings.zip", "ZIP (*.zip)"
        )
        if not destination or self._jobs:
            return
        path = Path(destination)
        self._start_job(
            lambda token, _reporter: self._export_settings(token, path),
            lambda summary: self._settings_exported(summary),
        )

    @staticmethod
    def _export_settings(token, destination: Path) -> SettingsExportSummary:
        token.checkpoint()
        plan = plan_settings_export(data_dir())
        token.checkpoint()
        write_settings_archive(plan, destination)
        return SettingsExportSummary(destination, len(plan.entries))

    def _settings_exported(self, summary: SettingsExportSummary) -> None:
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Exported {summary.entries:,} validated settings entries to {summary.destination}."
        )

    def preview_settings_import(self) -> None:
        archive, _ = QFileDialog.getOpenFileName(
            self, "Import portable settings", "", "ZIP (*.zip)"
        )
        if not archive or self._jobs:
            return
        self._import_plan = None
        self.apply_settings_button.setEnabled(False)
        self._start_job(
            lambda token, _reporter: self._plan_import(token, Path(archive)),
            self._settings_import_planned,
        )

    @staticmethod
    def _plan_import(token, archive: Path) -> SettingsImportPlan:
        token.checkpoint()
        recovery = settings_recovery_status(data_dir())
        if recovery is not None:
            raise ValueError(f"interrupted settings restore requires recovery ({recovery})")
        plan = plan_settings_import(archive, data_dir())
        token.checkpoint()
        return plan

    def _settings_import_planned(self, plan: SettingsImportPlan) -> None:
        self._import_plan = plan
        lines = [
            f"Archive: {plan.archive.name}",
            f"Verified SHA-256: {plan.archive_sha256}",
            "",
            *(f"{item.outcome.upper():9} {item.archive_path}" for item in plan.items),
        ]
        self.settings_preview.setPlainText("\n".join(lines))
        self.apply_settings_button.setEnabled(bool(plan.changes))
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Validated {len(plan.items):,} settings entries; {len(plan.changes):,} would change."
        )

    def apply_settings(self) -> None:
        plan = self._import_plan
        if plan is None or not plan.changes or self._jobs:
            return
        conflicts = sum(item.outcome == "change" for item in plan.changes)
        if QMessageBox.question(
            self,
            "Apply reviewed settings import?",
            f"Archive: {plan.archive.name}\nChanges: {len(plan.changes):,}\n"
            f"Existing files replaced: {conflicts:,}\n\n"
            "The C5 restore journal will roll back an interrupted write.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_job(
            lambda token, _reporter: self._apply_import(token, plan, conflicts > 0),
            self._settings_applied,
        )

    @staticmethod
    def _apply_import(token, plan: SettingsImportPlan, replace: bool) -> SettingsApplyResult:
        token.checkpoint()
        apply_settings_import(plan, replace_existing=replace)
        token.checkpoint()
        preferences = PreferencesStore(data_dir()).load()
        token.checkpoint()
        return SettingsApplyResult(plan, preferences)

    def _settings_applied(self, result: SettingsApplyResult) -> None:
        assert result.plan is not None
        plan = result.plan
        self._import_plan = None
        self.apply_settings_button.setEnabled(False)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Applied {len(plan.changes):,} verified settings changes. Runtime state is being reloaded."
        )
        self.settingsImported.emit(result.preferences)

    def recover_settings(self) -> None:
        if self._jobs:
            return
        if QMessageBox.question(
            self,
            "Recover interrupted settings import?",
            "Inspect the C5 restore journal and recover only its verified before/after states?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_job(self._recover_settings, self._settings_recovered)

    @staticmethod
    def _recover_settings(token, _reporter):
        token.checkpoint()
        status = settings_recovery_status(data_dir())
        if status is None:
            raise ValueError("no interrupted settings import requires recovery")
        result = recover_settings_restore(data_dir())
        token.checkpoint()
        preferences = PreferencesStore(data_dir()).load()
        token.checkpoint()
        return SettingsApplyResult(None, preferences, result)

    def _settings_recovered(self, result: SettingsApplyResult) -> None:
        self._import_plan = None
        self.settings_preview.clear()
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(f"Settings recovery completed safely: {result.recovery}.")
        self.settingsImported.emit(result.preferences)

    def build_report(self) -> None:
        if self._jobs:
            return
        installs = tuple(self.installs_provider())
        self._start_job(
            lambda token, _reporter: self._build_report(token, installs),
            self._report_ready,
        )

    @staticmethod
    def _build_report(token, installs):
        token.checkpoint()
        report = build_diagnostics(installs, root=data_dir())
        token.checkpoint()
        return report

    def _report_ready(self, report: dict[str, object]) -> None:
        self._diagnostics = report
        self.diagnostics_view.setPlainText(render_diagnostics(report).decode("utf-8"))
        self.export_diagnostics_button.setEnabled(True)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            "Redacted diagnostics are ready for inspection. No report has been exported."
        )

    def export_report(self) -> None:
        if self._diagnostics is None or self._jobs:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export redacted diagnostics", "starcompanion-diagnostics.json", "JSON (*.json)"
        )
        if not destination:
            return
        report = self._diagnostics
        path = Path(destination)
        self._start_job(
            lambda token, _reporter: self._write_report(token, report, path),
            lambda written: self._report_exported(written),
        )

    @staticmethod
    def _write_report(token, report, destination: Path) -> Path:
        token.checkpoint()
        write_diagnostics(report, destination)
        token.checkpoint()
        return destination

    def _report_exported(self, destination: Path) -> None:
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(f"Exported the reviewed redacted report to {destination}.")

    def _filter_help(self) -> None:
        query = self.help_search.text().strip().casefold()
        self.help_results.clear()
        for index, (title, body) in enumerate(HELP_ARTICLES):
            if query and query not in f"{title}\n{body}".casefold():
                continue
            self.help_results.addItem(title)
            self.help_results.item(self.help_results.count() - 1).setData(256, index)
        if self.help_results.count():
            self.help_results.setCurrentRow(0)
        else:
            self.help_text.setPlainText("No bundled help topic matches that search.")

    def _show_help(self, row: int) -> None:
        item = self.help_results.item(row)
        if item is None:
            return
        index = item.data(256)
        title, body = HELP_ARTICLES[index]
        self.help_text.setPlainText(f"{title}\n\n{body}")

    def _start_job(self, operation, success) -> None:
        if self._shutting_down:
            return
        job = QtOperationJob(operation, self)
        self._jobs.add(job)
        self._set_busy(True)
        job.succeeded.connect(success)
        job.failed.connect(self._job_failed)
        job.finished.connect(lambda: self._job_finished(job))
        job.start()

    def _job_failed(self, exc: Exception) -> None:
        self.event_log.publish("error", "local-admin-operation-stopped")
        self.status.set_tone(Tone.DANGER)
        self.status.setText(f"Local administration operation stopped safely: {exc}")

    def _job_finished(self, job: QtOperationJob) -> None:
        self._jobs.discard(job)
        job.deleteLater()
        if not self._shutting_down:
            self._set_busy(bool(self._jobs))

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.export_settings_button,
            self.import_settings_button,
            self.recover_settings_button,
            self.build_diagnostics_button,
        ):
            button.setEnabled(not busy)
        self.apply_settings_button.setEnabled(
            not busy and self._import_plan is not None and bool(self._import_plan.changes)
        )
        self.export_diagnostics_button.setEnabled(not busy and self._diagnostics is not None)
        self.clear_events_button.setEnabled(not busy)
        self.export_events_button.setEnabled(
            not busy and bool(self.event_log.entries(self.event_level.currentData()))
        )

    def shutdown_jobs(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.help_timer.stop()
        for job in tuple(self._jobs):
            job.shutdown(5000)
        self._jobs.clear()


__all__ = [
    "HELP_ARTICLES",
    "SettingsApplyResult",
    "SettingsExportSummary",
    "SupportTab",
]
