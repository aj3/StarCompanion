"""G2 profiles, portable settings, redacted diagnostics, and offline help."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
from ..jobs import QtOperationJob
from ..state import AppState


HELP_ARTICLES = (
    (
        "First run",
        "Start on Overview. Choose or discover an installed channel, read its local contract data, "
        "review presentation choices, then use Update my game. Extraction and preparation are "
        "cancellable; a confirmation and backup precede every game-file replacement.",
    ),
    (
        "Channels and languages",
        "LIVE, PTU, EPTU, TECH-PREVIEW, and HOTFIX are isolated. Localization, user wording, "
        "ownership, caches, and operation plans stay scoped to the selected channel and language.",
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
        "requires explicit replacement approval for conflicts.",
    ),
    (
        "Privacy and diagnostics",
        "StarCompanion has no telemetry or automatic network access. Shareable diagnostics contain "
        "counts, versions, capability state, and fingerprints; paths, usernames, logs, ownership, game "
        "strings, and user-authored values are redacted or excluded.",
    ),
    (
        "Validation and source precedence",
        "Stock localization is followed by generated profile output and then explicit user wording. "
        "The string editor shows every contribution and blocks invalid operation plans without "
        "inventing localization text.",
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

    def __init__(
        self,
        state: AppState,
        *,
        installs_provider=lambda: (),
        open_profile=None,
        save_profile=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.state = state
        self.installs_provider = installs_provider
        self.open_profile_action = open_profile
        self.save_profile_action = save_profile
        self._jobs: set[QtOperationJob] = set()
        self._shutting_down = False
        self._diagnostics: dict[str, object] | None = None
        self._import_plan: SettingsImportPlan | None = None

        self.status = NoticeBanner(
            "Profile, portability, diagnostics, and help remain local to this computer.",
            tone=Tone.INFO,
        )
        self.pages = QTabWidget()
        self.pages.setAccessibleName("Settings diagnostics and offline help")
        self.pages.addTab(self._profile_page(), "Profile")
        self.pages.addTab(self._settings_page(), "Portability")
        self.pages.addTab(self._diagnostics_page(), "Diagnostics")
        self.pages.addTab(self._help_page(), "Offline help")

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.pages, 1)

        state.profileChanged.connect(self._profile_changed)
        self._profile_changed()
        self._filter_help()

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
        layout.addStretch(1)
        return page

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
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
        return page

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
