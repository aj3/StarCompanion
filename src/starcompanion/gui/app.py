"""Main window and C6 application shell over the headless pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
)

from ..config import Profile, UnsupportedProfileVersion, builtin_profiles, load_builtin
from ..features import community_rewards_enabled
from . import theme
from .preferences import PAGE_KEYS, UiPreferences, UiPreferencesStore
from .shell import ApplicationShell, PageSpec
from .state import AppState
from .tabs import (
    AdvancedStringEditorTab,
    ApplyTab,
    BlueprintTrackerTab,
    FieldsTab,
    FormattingTab,
    SourceTab,
    StartTab,
    SupportTab,
    TemplatesTab,
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        state: AppState | None = None,
        ui_preferences_store: UiPreferencesStore | None = None,
    ):
        super().__init__()
        self.state = state or AppState(self)
        self.ui_preferences_store = ui_preferences_store or UiPreferencesStore()
        loaded_preferences = self.ui_preferences_store.load(
            legacy_theme=self.state.profile.appearance.theme
        )
        self.ui_preferences = loaded_preferences.preferences
        self.ui_preference_warning = loaded_preferences.warning
        self.setWindowTitle("StarCompanion")
        self.setAccessibleName("StarCompanion")
        self.setAccessibleDescription(
            "Local-first Star Citizen contract enhancement and review application."
        )
        self.setMinimumSize(1040, 680)
        self.resize(1280, 800)

        self.start = StartTab(self.state)
        self.source = SourceTab(self.state)
        self.fields = FieldsTab(self.state)
        self.formatting = FormattingTab(self.state)
        self.templates = TemplatesTab(self.state)
        self.editor = AdvancedStringEditorTab(self.state)
        self.apply = ApplyTab(self.state)
        self.blueprints = BlueprintTrackerTab(self.state)
        self.support = SupportTab(
            self.state,
            installs_provider=lambda: self.start.installs,
            open_profile=self.open_profile,
            save_profile=self.save_profile,
        )

        self.shell = ApplicationShell()
        # The underlying pages and order are unchanged. PageSpec only gives
        # the new shell clearer workflow language and layout metadata.
        self.shell.add_page(
            self.start,
            PageSpec(
                "overview",
                "Start here",
                "Overview",
                "Contract workspace",
                "Find your install, read local data, and prepare a safe game update.",
                "Workspace",
            ),
        )
        self.shell.add_page(
            self.fields,
            PageSpec(
                "content",
                "What to show",
                "Contract content",
                "Choose contract intelligence",
                "Control which local mission facts appear in the contract manager.",
                "Workspace",
            ),
        )
        self.shell.add_page(
            self.formatting,
            PageSpec(
                "presentation",
                "Appearance",
                "Presentation",
                "Shape the presentation",
                "Tune title structure, labels, and formatting without changing source data.",
                "Workspace",
            ),
        )
        self.shell.add_page(
            self.blueprints,
            PageSpec(
                "blueprints",
                "Blueprint ownership",
                "Blueprints",
                "Track blueprint ownership",
                "Search the local C4 catalog and review channel-scoped acquisition evidence.",
                "Workspace",
                scrollable=False,
            ),
        )
        self.shell.add_page(
            self.templates,
            PageSpec(
                "templates",
                "Advanced: custom wording",
                "Custom wording",
                "Author custom wording",
                "Edit explicit templates with live previews and model-level history.",
                "Advanced",
                scrollable=True,
            ),
        )
        self.shell.add_page(
            self.editor,
            PageSpec(
                "string-editor",
                "Advanced: strings",
                "String editor",
                "Inspect and edit merged strings",
                "Search stock, rendered, and user values with complete source and validation evidence.",
                "Advanced",
                scrollable=False,
            ),
        )
        self.shell.add_page(
            self.source,
            PageSpec(
                "provenance",
                "Data & provenance",
                "Data & provenance",
                "Inspect local source evidence",
                "Review provider capability, coverage, provenance, and build diagnostics.",
                "Advanced",
            ),
        )
        self.shell.add_page(
            self.apply,
            PageSpec(
                "manual-apply",
                "Backup and recovery",
                "Backup & recovery",
                "Review backups and recovery",
                "Browse target-scoped backups, resolve known interruptions, or inspect a manual plan.",
                "Tools",
                scrollable=True,
            ),
        )
        self.shell.add_page(
            self.support,
            PageSpec(
                "support",
                "Settings and help",
                "Settings & help",
                "Manage local settings and support",
                "Profiles, verified portability, redacted diagnostics, and searchable offline guidance.",
                "Tools",
                scrollable=False,
            ),
        )
        self.setCentralWidget(self.shell)
        # Keep the narrow tab-like API used by older GUI integration tests and
        # extensions while navigation is now rendered by ApplicationShell.
        self.tabs = self.shell.stack

        self._build_menu()
        self.state.profileChanged.connect(self._update_title)
        self.state.profileChanged.connect(self._refresh_shell_context)
        self.state.contractsChanged.connect(self._refresh_shell_context)
        self.state.pathsChanged.connect(self._refresh_shell_context)
        self.shell.themeRequested.connect(self.toggle_theme)
        self.shell.pageChanged.connect(self._page_changed)
        self.support.settingsImported.connect(self._reload_imported_settings)
        if not self.shell.set_current_key(self.ui_preferences.last_page):
            self.shell.set_current_key("overview")
            self.ui_preferences = self.ui_preferences.with_page("overview")
            self._save_ui_preferences()
        self._update_title()
        self._refresh_shell_context()
        self.start.set_ui_preference_warning(self.ui_preference_warning)
        self.apply_theme()

    def apply_theme(self) -> None:
        """Restyle from application preferences, independent of output profiles."""
        selected = self.ui_preferences.theme
        if getattr(self, "_applied_theme", None) == selected:
            return
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, selected)
            self._applied_theme = selected
        self.shell.set_theme_name(selected)

    def toggle_theme(self) -> None:
        current = self.ui_preferences.theme
        selected = "light" if current == "dark" else "dark"
        self.ui_preferences = self.ui_preferences.with_theme(selected)
        self.apply_theme()
        self._update_theme_action()
        self._save_ui_preferences()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Profile")
        self._populate_profile_menu(menu)

        shell_menu = QMenu("Profile", self)
        self._populate_profile_menu(shell_menu)
        self.shell.profile_button.setMenu(shell_menu)

        view = self.menuBar().addMenu("&View")
        self.theme_action = view.addAction("Switch to light theme", self.toggle_theme)
        self._update_theme_action()

    def _populate_profile_menu(self, menu: QMenu) -> None:
        presets = menu.addMenu("Load built-in")
        for name in builtin_profiles():
            presets.addAction(name, lambda n=name: self.load_builtin_profile(n))

        menu.addSeparator()
        menu.addAction("Open…", self.open_profile)
        menu.addAction("Save as…", self.save_profile)

    def _update_theme_action(self) -> None:
        going_to = "light" if self.ui_preferences.theme == "dark" else "dark"
        self.theme_action.setText(f"Switch to {going_to} theme")
        self.shell.set_theme_name(self.ui_preferences.theme)

    def _page_changed(self, key: str) -> None:
        updated = self.ui_preferences.with_page(key)
        if updated == self.ui_preferences:
            return
        self.ui_preferences = updated
        self._save_ui_preferences()

    def _save_ui_preferences(self) -> None:
        try:
            self.ui_preferences_store.save(self.ui_preferences)
        except (OSError, ValueError) as exc:
            self.ui_preference_warning = (
                f"Interface preferences could not be saved: {exc}. "
                "The existing file was left unchanged."
            )
        else:
            self.ui_preference_warning = None
        self.start.set_ui_preference_warning(self.ui_preference_warning)

    def _reload_imported_settings(self, values: object) -> None:
        """Publish imported preferences/user values without crossing model boundaries."""
        if not isinstance(values, dict):
            return
        theme_name = values.get("theme", self.ui_preferences.theme)
        page = values.get("last_page", "overview")
        self.ui_preferences = UiPreferences(
            theme=theme_name if theme_name in {"dark", "light"} else self.ui_preferences.theme,
            last_page=page if page in PAGE_KEYS else "overview",
        )
        self.ui_preference_warning = None
        self._applied_theme = None
        self.apply_theme()
        self._update_theme_action()
        self.start.set_ui_preference_warning(self.ui_preference_warning)
        if not self.editor.document.dirty:
            self.editor.load_user_edits()

    def _refresh_shell_context(self) -> None:
        install = self.start.install
        game = None
        if install is not None:
            game = install.channel
            if install.version:
                game = f"{game} {install.version}"

        contracts = self.state.contracts
        data = None
        if contracts:
            count = len(contracts.contracts)
            data = f"{count:,} {'contract' if count == 1 else 'contracts'}"
        self.shell.set_context(
            profile=self.state.profile.name,
            game=game,
            data=data,
        )

    # --- profile actions -----------------------------------------------------

    def load_builtin_profile(self, name: str) -> None:
        self.state.set_profile(load_builtin(name))

    def open_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open profile", "", "JSON (*.json)")
        if path:
            self.load_profile(Path(path))

    def load_profile(self, path: Path) -> None:
        try:
            profile = Profile.load(path)
        except (UnsupportedProfileVersion, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open profile", str(exc))
            return

        if self.state.contracts:
            for problem in profile.validate_against(self.state.contracts):
                QMessageBox.warning(self, "Profile problem", problem)

        self.state.set_profile(profile)

    def save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save profile", f"{self.state.profile.name}.json", "JSON (*.json)"
        )
        if path:
            self.state.profile.save(Path(path))

    def _update_title(self) -> None:
        self.setWindowTitle(f"StarCompanion — {self.state.profile.name}")

    def closeEvent(self, event) -> None:
        self.start.shutdown_jobs()
        self.editor.shutdown_jobs()
        self.blueprints.shutdown_jobs()
        self.support.shutdown_jobs()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    from ..offline import enforce_offline_from_environment

    enforce_offline_from_environment()
    arguments = list(argv if argv is not None else sys.argv)
    smoke_test = "--smoke-test" in arguments
    arguments = [argument for argument in arguments if argument != "--smoke-test"]
    app = QApplication(arguments)
    window = MainWindow()
    window.show()
    if smoke_test:
        # Release assurance launches the frozen GUI with networking denied and
        # an isolated data root.  Let the shell paint once, then exercise the
        # ordinary close path so worker shutdown remains part of the smoke.
        QTimer.singleShot(250, window.close)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
