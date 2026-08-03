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
from ..features import community_rewards_enabled
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
        if community_rewards_enabled():
            # Every option on these two concerns reward numbers, so with that
            # capability off they would be an empty tab and a dead end.
            tabs.addTab(self.fields, "What to show")
        tabs.addTab(self.formatting, "Appearance")
        tabs.addTab(self.templates, "Custom wording")
        if community_rewards_enabled():
            tabs.addTab(self.source, "Advanced: data")
        tabs.addTab(self.apply, "Advanced: apply")
        self.setCentralWidget(tabs)
        self.tabs = tabs

        self._build_menu()
        self.state.profileChanged.connect(self._update_title)
        self._update_title()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Profile")

        presets = menu.addMenu("Load built-in")
        for name in builtin_profiles():
            presets.addAction(name, lambda n=name: self.load_builtin_profile(n))

        menu.addSeparator()
        menu.addAction("Open…", self.open_profile)
        menu.addAction("Save as…", self.save_profile)

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


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
