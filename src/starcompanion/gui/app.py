"""Main window and C6 application shell over the headless pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QActionGroup
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
from .coach import CoachStep, CoachTour
from .event_log import EventLog
from .layout import LayoutError, LayoutState, LocalLayoutStore, WindowGeometry
from .preferences import PAGE_KEYS, UiPreferences, UiPreferencesStore
from .shell import ApplicationShell, PageSpec
from .state import AppState
from .ui_text import UiTranslator, normalize_ui_locale
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
    DEFAULT_WINDOW_SIZE = (1280, 800)

    def __init__(
        self,
        state: AppState | None = None,
        ui_preferences_store: UiPreferencesStore | None = None,
        layout_store: LocalLayoutStore | None = None,
    ):
        super().__init__()
        self.state = state or AppState(self)
        self.ui_preferences_store = ui_preferences_store or UiPreferencesStore()
        self.layout_store = layout_store or LocalLayoutStore()
        self._pending_splitter_layout: dict[str, tuple[int, ...]] = {}
        self._layout_restore_timer = QTimer(self)
        self._layout_restore_timer.setSingleShot(True)
        self._layout_restore_timer.timeout.connect(self._restore_visible_splitter)
        self._close_after_save_timer = QTimer(self)
        self._close_after_save_timer.setSingleShot(True)
        self._close_after_save_timer.timeout.connect(self._finish_close_after_save)
        self._close_after_editor_save = False
        self._allow_dirty_close = False
        self._tour: CoachTour | None = None
        loaded_preferences = self.ui_preferences_store.load(
            legacy_theme=self.state.profile.appearance.theme
        )
        loaded_layout = self.layout_store.load()
        self.ui_preferences = loaded_preferences.preferences
        self.translator = UiTranslator(self.ui_preferences.interface_locale)
        self.events = EventLog(parent=self)
        self.events.publish("info", "application-started")
        self.ui_preference_warning = loaded_preferences.warning
        self.layout_warning = loaded_layout.warning
        self.setWindowTitle("StarCompanion")
        self.setAccessibleName("StarCompanion")
        self.setAccessibleDescription(
            "Local-first Star Citizen contract enhancement and review application."
        )
        self.setMinimumSize(1040, 680)
        self.resize(*self.DEFAULT_WINDOW_SIZE)

        self.start = StartTab(
            self.state,
            language=self.ui_preferences.default_language,
        )
        self.start.languageChanged.connect(self._set_default_language)
        self.start.scopeStatusChanged.connect(self._refresh_shell_context)
        self.source = SourceTab(self.state)
        self.fields = FieldsTab(self.state)
        self.formatting = FormattingTab(self.state)
        self.templates = TemplatesTab(self.state)
        self.editor = AdvancedStringEditorTab(self.state)
        self.apply = ApplyTab(self.state)
        self.blueprints = BlueprintTrackerTab(
            self.state,
            link_live_hotfix=self.ui_preferences.link_live_hotfix,
        )
        self.blueprints.linkLiveHotfixChanged.connect(
            self._set_live_hotfix_preference
        )
        self.support = SupportTab(
            self.state,
            installs_provider=lambda: self.start.installs,
            open_profile=self.open_profile,
            save_profile=self.save_profile,
            event_log=self.events,
            interface_locale=self.ui_preferences.interface_locale,
        )

        self.shell = ApplicationShell()
        self.shell.set_translator(self.translator)
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
        self.shell.set_translator(self.translator)
        self.setCentralWidget(self.shell)
        # Keep the narrow tab-like API used by older GUI integration tests and
        # extensions while navigation is now rendered by ApplicationShell.
        self.tabs = self.shell.stack
        self.start.recoveryRequested.connect(self._open_recovery)

        self._build_menu()
        self.state.profileChanged.connect(self._update_title)
        self.state.profileChanged.connect(self._refresh_shell_context)
        self.state.contractsChanged.connect(self._refresh_shell_context)
        self.state.pathsChanged.connect(self._refresh_shell_context)
        self.shell.themeRequested.connect(self.toggle_theme)
        self.shell.simpleModeRequested.connect(
            lambda: self.set_simple_mode(not self.ui_preferences.simple_mode)
        )
        self.shell.pageChanged.connect(self._page_changed)
        self.support.settingsImported.connect(self._reload_imported_settings)
        self.support.interfaceLocaleChanged.connect(self.set_interface_locale)
        self.support.tourRequested.connect(self.start_guided_tour)
        self.set_simple_mode(self.ui_preferences.simple_mode, persist=False)
        if not self.shell.set_current_key(self.ui_preferences.last_page):
            self.shell.set_current_key("overview")
            self.ui_preferences = self.ui_preferences.with_page("overview")
            self._save_ui_preferences()
        self._update_title()
        self._refresh_shell_context()
        self.apply_theme()
        self._restore_layout(loaded_layout.state)
        self._publish_interface_warning()

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
        index = theme.THEME_ORDER.index(current) if current in theme.THEME_ORDER else 0
        selected = theme.THEME_ORDER[(index + 1) % len(theme.THEME_ORDER)]
        self.select_theme(selected)

    def select_theme(self, selected: str) -> None:
        if selected not in theme.PALETTES:
            return
        self.ui_preferences = self.ui_preferences.with_theme(selected)
        self.apply_theme()
        self._update_theme_action()
        self._save_ui_preferences()
        self.events.publish("info", "interface-theme-changed", selected)

    def set_simple_mode(self, enabled: bool, *, persist: bool = True) -> None:
        enabled = bool(enabled)
        self.shell.set_simple_mode(enabled)
        self.start.set_simple_mode(enabled)
        if hasattr(self, "simple_mode_action"):
            self.simple_mode_action.setChecked(enabled)
        if persist:
            self.ui_preferences = self.ui_preferences.with_simple_mode(enabled)
            self._save_ui_preferences()
            self.events.publish(
                "info", "workspace-mode-changed", "simple" if enabled else "full"
            )

    def set_interface_locale(self, locale: str, *, persist: bool = True) -> None:
        selected = normalize_ui_locale(locale)
        self.translator = UiTranslator(selected)
        self.shell.set_translator(self.translator)
        self.shell.set_theme_name(self.ui_preferences.theme)
        self._refresh_shell_context()
        if self.support.interface_language.currentData() != selected:
            self.support.interface_language.blockSignals(True)
            self.support.interface_language.setCurrentIndex(
                self.support.interface_language.findData(selected)
            )
            self.support.interface_language.blockSignals(False)
        self.support.interface_locale = selected
        self.ui_preferences = self.ui_preferences.with_interface_locale(selected)
        if persist:
            self._save_ui_preferences()
        self.events.publish("info", "interface-language-changed", selected)

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Profile")
        self._populate_profile_menu(menu)

        shell_menu = QMenu("Profile", self)
        self._populate_profile_menu(shell_menu)
        self.shell.profile_button.setMenu(shell_menu)

        view = self.menuBar().addMenu("&View")
        self.theme_action = view.addAction("Next theme", self.toggle_theme)
        self.theme_menu = view.addMenu("Choose theme")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for name in theme.THEME_ORDER:
            action = self.theme_menu.addAction(name.replace("-", " ").title())
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=name: self.select_theme(value))
            self.theme_group.addAction(action)
            self.theme_actions[name] = action
        self.simple_mode_action = view.addAction("Simple mode")
        self.simple_mode_action.setCheckable(True)
        self.simple_mode_action.triggered.connect(self.set_simple_mode)
        self.reset_layout_action = view.addAction(
            "Reset window layout…", self.reset_window_layout
        )
        help_menu = self.menuBar().addMenu("&Help")
        self.guided_tour_action = help_menu.addAction(
            "Guided tour…", self.start_guided_tour
        )
        self._update_theme_action()

    def _populate_profile_menu(self, menu: QMenu) -> None:
        presets = menu.addMenu("Load built-in")
        for name in builtin_profiles():
            presets.addAction(name, lambda n=name: self.load_builtin_profile(n))

        menu.addSeparator()
        menu.addAction("Open…", self.open_profile)
        menu.addAction("Save as…", self.save_profile)

    def _update_theme_action(self) -> None:
        index = theme.THEME_ORDER.index(self.ui_preferences.theme)
        going_to = theme.THEME_ORDER[(index + 1) % len(theme.THEME_ORDER)]
        self.theme_action.setText(f"Next theme: {going_to.replace('-', ' ').title()}")
        for name, action in self.theme_actions.items():
            action.setChecked(name == self.ui_preferences.theme)
        self.shell.set_theme_name(self.ui_preferences.theme)

    def _page_changed(self, key: str) -> None:
        self._schedule_pending_splitter_restore()
        updated = self.ui_preferences.with_page(key)
        if updated == self.ui_preferences:
            return
        self.ui_preferences = updated
        self._save_ui_preferences()
        self.events.publish("info", "workspace-page-changed", key)

    def _open_recovery(self) -> None:
        self.shell.set_current_key("manual-apply")
        self.apply.refresh_recovery()

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
        self._publish_interface_warning()

    def _set_live_hotfix_preference(self, enabled: bool) -> None:
        updated = self.ui_preferences.with_live_hotfix_link(enabled)
        if updated == self.ui_preferences:
            return
        self.ui_preferences = updated
        self._save_ui_preferences()

    def _set_default_language(self, language: str) -> None:
        updated = self.ui_preferences.with_default_language(language)
        if updated == self.ui_preferences:
            return
        self.ui_preferences = updated
        self._save_ui_preferences()

    def _reload_imported_settings(self, values: object) -> None:
        """Publish imported preferences/user values without crossing model boundaries."""
        if not isinstance(values, dict):
            return
        theme_name = values.get("theme", self.ui_preferences.theme)
        page = values.get("last_page", "overview")
        self.ui_preferences = UiPreferences(
            theme=theme_name if theme_name in theme.PALETTES else self.ui_preferences.theme,
            last_page=page if page in PAGE_KEYS else "overview",
            link_live_hotfix=bool(values.get("link_live_hotfix", True)),
            default_language=str(values.get("default_language", "english")),
            simple_mode=bool(values.get("simple_mode", False)),
            tutorial_completed=bool(values.get("tutorial_completed", False)),
            interface_locale=normalize_ui_locale(values.get("interface_locale", "en-US")),
        )
        self.ui_preference_warning = None
        self._applied_theme = None
        self.apply_theme()
        self._update_theme_action()
        self.set_interface_locale(self.ui_preferences.interface_locale, persist=False)
        self.set_simple_mode(self.ui_preferences.simple_mode, persist=False)
        self._publish_interface_warning()
        self.blueprints.set_link_live_hotfix(
            self.ui_preferences.link_live_hotfix,
            persist=False,
        )
        if self.start.selected_language != self.ui_preferences.default_language:
            self.start.set_selected_language(self.ui_preferences.default_language)
        if not self.editor.document.dirty:
            self.editor.load_user_edits()

    def _refresh_shell_context(self) -> None:
        install = self.start.install
        game = None
        if install is not None:
            game = install.channel
            if install.version:
                game = f"{game} {install.version}"
            selected = self.start.selected_language
            active = self.start.active_language or "not active"
            override = "custom" if self.start.override_present else "stock"
            game = f"{game} · {selected} selected · {active} active · {override}"

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

    def _publish_interface_warning(self) -> None:
        warnings = tuple(
            item for item in (self.ui_preference_warning, self.layout_warning) if item
        )
        self.start.set_ui_preference_warning("\n".join(warnings) or None)

    @staticmethod
    def _set_splitter_sizes(splitter, sizes: tuple[int, ...] | None) -> None:
        if sizes is not None and len(sizes) == splitter.count() and any(sizes):
            splitter.setSizes(list(sizes))

    @staticmethod
    def _set_column_widths(table, widths: tuple[int, ...] | None) -> None:
        if widths is None or len(widths) != table.model().columnCount():
            return
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)

    @staticmethod
    def _geometry_is_visible(geometry: WindowGeometry) -> bool:
        proposed = QRect(geometry.x, geometry.y, geometry.width, geometry.height)
        return any(
            screen.availableGeometry().intersects(proposed)
            for screen in QApplication.screens()
        )

    def _restore_layout(self, state: LayoutState) -> None:
        geometry = state.window
        if geometry is not None:
            self.resize(geometry.width, geometry.height)
            if self._geometry_is_visible(geometry):
                self.move(geometry.x, geometry.y)

        # Hidden QStackedWidget pages do not have a usable splitter extent.
        # Keep validated ratios pending until each page is actually displayed.
        self._pending_splitter_layout = dict(state.splitters)
        self._set_column_widths(
            self.editor.table,
            state.columns.get("editor.table"),
        )
        self._set_column_widths(
            self.blueprints.table,
            state.columns.get("blueprints.table"),
        )
        if geometry is not None and geometry.maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        self._schedule_pending_splitter_restore()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._schedule_pending_splitter_restore()

    def start_guided_tour(self) -> None:
        if self._tour is not None:
            self._tour.raise_()
            self._tour.activateWindow()
            return
        if self.ui_preferences.simple_mode:
            steps = (
                CoachStep(
                    "overview",
                    "Update safely",
                    "This action reads local data when needed, prepares a preview, and asks before writing.",
                    self.start.go,
                ),
                CoachStep(
                    "overview",
                    "Undo from a backup",
                    "Undo uses the existing verified backup and confirmation workflow.",
                    self.start.undo,
                ),
                CoachStep(
                    "overview",
                    "Open the full workspace",
                    "Use Full mode when you want detailed presentation, provenance, ownership, and recovery tools.",
                    self.shell.mode_button,
                ),
            )
        else:
            steps = (
                CoachStep(
                    "overview",
                    "Start with one safe update",
                    "StarCompanion reads local files, prepares an exact plan, and asks before every write.",
                    self.start.go,
                ),
                CoachStep(
                    "content",
                    "Choose evidenced content",
                    "Category controls enable only locally supported facts; individual controls remain available.",
                    self.fields.category_boxes["rewards"],
                ),
                CoachStep(
                    "string-editor",
                    "Inspect every final string",
                    "Search and edit the virtualized source graph with provenance and model-level undo.",
                    self.editor.search,
                ),
                CoachStep(
                    "support",
                    "Review privacy and recovery",
                    "Settings, diagnostics, events, and help remain local and inspectable.",
                    self.support.pages,
                ),
            )
        self._tour = CoachTour(steps, self.shell.set_current_key, self)
        self._tour.completed.connect(self._guided_tour_completed)
        self._tour.finished.connect(lambda _result: setattr(self, "_tour", None))
        self._tour.show()
        self.events.publish("info", "guided-tour-opened")

    def _guided_tour_completed(self) -> None:
        self.ui_preferences = self.ui_preferences.with_tutorial_completed()
        self._save_ui_preferences()
        self.events.publish("info", "guided-tour-completed")

    def _schedule_pending_splitter_restore(self) -> None:
        self._layout_restore_timer.start(0)

    def _restore_visible_splitter(self) -> None:
        key = self.shell.current_key()
        selected = {
            "string-editor": ("editor.workspace", self.editor.workspace, None),
            "templates": (
                "templates.workspace",
                self.templates.splitter,
                self.templates.sync_splitter_orientation,
            ),
            "manual-apply": (
                "apply.workspace",
                self.apply.workspace_splitter,
                self.apply.sync_splitter_orientation,
            ),
        }.get(key)
        if selected is None:
            return
        layout_key, splitter, synchronize = selected
        sizes = self._pending_splitter_layout.get(layout_key)
        if sizes is None:
            return
        if synchronize is not None:
            synchronize()
        self._set_splitter_sizes(splitter, sizes)
        self._pending_splitter_layout.pop(layout_key, None)

    @staticmethod
    def _captured_splitter(splitter) -> tuple[int, ...] | None:
        sizes = tuple(int(value) for value in splitter.sizes())
        return sizes if len(sizes) >= 2 and any(sizes) else None

    @staticmethod
    def _captured_columns(table) -> tuple[int, ...]:
        return tuple(
            int(table.columnWidth(column))
            for column in range(table.model().columnCount())
        )

    def _capture_layout(self) -> LayoutState:
        geometry = self.normalGeometry()
        splitters = {}
        for key, splitter in (
            ("editor.workspace", self.editor.workspace),
            ("templates.workspace", self.templates.splitter),
            ("apply.workspace", self.apply.workspace_splitter),
        ):
            sizes = self._pending_splitter_layout.get(key)
            if sizes is None:
                sizes = self._captured_splitter(splitter)
            if sizes is not None:
                splitters[key] = sizes
        return LayoutState(
            WindowGeometry(
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
                self.isMaximized(),
            ),
            splitters,
            {
                "editor.table": self._captured_columns(self.editor.table),
                "blueprints.table": self._captured_columns(self.blueprints.table),
            },
        )

    def _save_layout(self) -> None:
        try:
            self.layout_store.save(self._capture_layout())
        except (LayoutError, OSError, ValueError) as exc:
            self.layout_warning = (
                f"Window layout could not be saved: {exc}. "
                "The existing file was left unchanged."
            )
        else:
            self.layout_warning = None
        self._publish_interface_warning()

    def reset_window_layout(self) -> None:
        if QMessageBox.question(
            self,
            "Reset window layout?",
            "Reset this machine's window size, splitters, and table-column widths? "
            "Profiles and portable preferences are not changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.layout_store.reset()
        except (LayoutError, OSError) as exc:
            self.layout_warning = f"Window layout could not be reset: {exc}."
            self._publish_interface_warning()
            return
        self.showNormal()
        self.resize(*self.DEFAULT_WINDOW_SIZE)
        self._pending_splitter_layout.clear()
        self.editor.reset_layout()
        self.blueprints.reset_layout()
        self.templates.reset_splitter_layout()
        self.apply.reset_splitter_layout()
        self.layout_warning = None
        self._publish_interface_warning()

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
        if (
            self.isVisible()
            and self.editor.document.dirty
            and not self._allow_dirty_close
        ):
            choice = QMessageBox.question(
                self,
                "Unsaved wording changes",
                "The String editor has unsaved channel/language-specific changes.\n\n"
                "Save writes them through the existing background C3 store. "
                "Discard closes without writing them.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                self.editor.save_user_edits()
                if not self.editor.jobs_active:
                    QMessageBox.warning(
                        self,
                        "Could not start save",
                        "The editor could not start a safe background save. The window remains open.",
                    )
                    event.ignore()
                    return
                self._close_after_editor_save = True
                self._close_after_save_timer.start(50)
                event.ignore()
                return
            self._allow_dirty_close = True
        self._layout_restore_timer.stop()
        self._close_after_save_timer.stop()
        self.start.shutdown_jobs()
        self.editor.shutdown_jobs()
        self.blueprints.shutdown_jobs()
        self.support.shutdown_jobs()
        self._save_layout()
        super().closeEvent(event)

    def _finish_close_after_save(self) -> None:
        if not self._close_after_editor_save:
            return
        if self.editor.jobs_active:
            self._close_after_save_timer.start(50)
            return
        self._close_after_editor_save = False
        if self.editor.document.dirty:
            QMessageBox.warning(
                self,
                "Unsaved changes remain",
                "The background save did not complete successfully. The window remains open.",
            )
            return
        self._allow_dirty_close = True
        self.close()


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
        # Paint once and exercise the ordinary close path deterministically.
        app.processEvents()
        window.close()
        app.processEvents()
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
