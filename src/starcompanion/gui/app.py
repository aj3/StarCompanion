"""Main window: five tabs over the headless pipeline, plus profile management."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)

from ..config import Profile, UnsupportedProfileVersion, builtin_profiles, load_builtin
from ..features import community_rewards_enabled, expert_tabs_enabled
from . import theme
from .state import AppState
from .tabs import ApplyTab, FieldsTab, FormattingTab, SourceTab, StartTab, TemplatesTab


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.state = state or AppState(self)
        self.setWindowTitle("StarCompanion")
        self.resize(900, 700)

        self.start = StartTab(self.state)
        self.source = SourceTab(self.state)
        self.fields = FieldsTab(self.state)
        self.formatting = FormattingTab(self.state)
        self.templates = TemplatesTab(self.state)
        self.apply = ApplyTab(self.state)

        tabs = QTabWidget()
        # Start first and selected: everything needed for the common case is
        # there, and the rest is for people who want to tune the output.
        tabs.addTab(self.start, "Start here")
        tabs.addTab(self.fields, "What to show")
        tabs.addTab(self.formatting, "Appearance")
        tabs.addTab(self.templates, "Advanced: custom wording")
        tabs.addTab(self.source, "Data & provenance")
        if expert_tabs_enabled():
            tabs.addTab(self.apply, "Advanced: apply")
        self.setCentralWidget(tabs)
        self.tabs = tabs

        self._build_menu()
        self.state.profileChanged.connect(self._update_title)
        self.state.profileChanged.connect(self.apply_theme)
        self._update_title()
        self.apply_theme()

    def apply_theme(self) -> None:
        """Restyle everything from the profile's chosen theme."""
        selected = self.state.profile.appearance.theme
        if getattr(self, "_applied_theme", None) == selected:
            return
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, selected)
            self._applied_theme = selected

    def toggle_theme(self) -> None:
        current = self.state.profile.appearance.theme
        self.state.profile.appearance.theme = "light" if current == "dark" else "dark"
        self.state.touch_profile()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Profile")

        presets = menu.addMenu("Load built-in")
        for name in builtin_profiles():
            presets.addAction(name, lambda n=name: self.load_builtin_profile(n))

        menu.addSeparator()
        menu.addAction("Open…", self.open_profile)
        menu.addAction("Save as…", self.save_profile)

        view = self.menuBar().addMenu("&View")
        self.theme_action = view.addAction("Switch to light theme", self.toggle_theme)
        self.state.profileChanged.connect(self._update_theme_action)
        self._update_theme_action()

    def _update_theme_action(self) -> None:
        going_to = "light" if self.state.profile.appearance.theme == "dark" else "dark"
        self.theme_action.setText(f"Switch to {going_to} theme")

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
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    from ..offline import enforce_offline_from_environment

    enforce_offline_from_environment()
    app = QApplication(argv if argv is not None else sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
