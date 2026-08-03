"""Apply tab: preview the change, then write it under confirmation.

Writing is never one click. The plan is shown first, the confirmation dialog
restates the target and the counts, and a target inside a real game install
gets an additional explicit warning before the button does anything.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...ini import LocalizationFile
from ...inject import (
    MergeMode,
    ValidationFailedError,
    apply,
    looks_like_game_install,
    plan,
    restore,
)
from ..state import AppState

INTRO = (
    "You do not need this tab for normal use — 'Update my game' on the Start "
    "tab does the same job. It is here for three situations:\n"
    "\n"
    "  •  updating a second copy of the game, such as PTU, that was not "
    "found automatically\n"
    "  •  going back to an older backup rather than the most recent one\n"
    "  •  starting from a clean file, when another contract pack is already "
    "installed and you want it gone"
)

MODES = (
    (
        MergeMode.MERGE,
        "Change only the contracts — leave anything else in the file alone",
    ),
    (
        MergeMode.OVERWRITE,
        "Start from a clean copy of the file, discarding other packs' changes",
    ),
)


class ApplyTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state

        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("the game text file to change")
        self.target_edit.textChanged.connect(self._target_changed)
        target_browse = QPushButton("Browse…")
        target_browse.clicked.connect(self._browse_target)

        self.stock_edit = QLineEdit()
        self.stock_edit.setPlaceholderText(
            "a clean, unmodified copy of the file (only needed for the second option)"
        )
        self.stock_edit.textChanged.connect(self._stock_changed)
        stock_browse = QPushButton("Browse…")
        stock_browse.clicked.connect(self._browse_stock)

        self.mode = QComboBox()
        for value, label in MODES:
            self.mode.addItem(label, value)
        self.mode.currentIndexChanged.connect(self._mode_changed)

        self.install_warning = QLabel()
        self.install_warning.setWordWrap(True)
        self.install_warning.setVisible(False)

        target_box = QGroupBox("Which file to change")
        target_layout = QVBoxLayout(target_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("File to change"))
        row.addWidget(self.target_edit, 1)
        row.addWidget(target_browse)
        target_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Clean copy"))
        row.addWidget(self.stock_edit, 1)
        row.addWidget(stock_browse)
        target_layout.addLayout(row)
        target_layout.addWidget(self.mode)
        target_layout.addWidget(self.install_warning)

        refresh = QPushButton("Check what would change")
        refresh.clicked.connect(self.refresh_plan)
        self.plan_label = QLabel("Press the button above to see what would change.")
        self.plan_label.setWordWrap(True)
        self.plan_detail = QListWidget()

        plan_box = QGroupBox("What would change (nothing is written yet)")
        plan_layout = QVBoxLayout(plan_box)
        plan_layout.addWidget(refresh)
        plan_layout.addWidget(self.plan_label)
        plan_layout.addWidget(self.plan_detail)

        self.apply_button = QPushButton("Change this file…")
        self.apply_button.clicked.connect(self.apply_changes)

        self.backups = QListWidget()
        restore_button = QPushButton("Go back to the selected version")
        restore_button.clicked.connect(self.restore_backup)

        backup_box = QGroupBox("Previous versions you can go back to")
        backup_layout = QVBoxLayout(backup_box)
        backup_layout.addWidget(self.backups)
        backup_layout.addWidget(restore_button)

        intro = QLabel(INTRO)
        intro.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(target_box)
        layout.addWidget(plan_box, 1)
        layout.addWidget(self.apply_button)
        layout.addWidget(backup_box, 1)

        state.profileChanged.connect(self._sync_mode)
        state.pathsChanged.connect(self.refresh_backups)
        self._sync_mode()

    # --- paths ---------------------------------------------------------------

    def _browse_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select global.ini", "", "INI (*.ini)")
        if path:
            self.target_edit.setText(path)

    def _browse_stock(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select pristine global.ini", "", "INI (*.ini)")
        if path:
            self.stock_edit.setText(path)

    def _target_changed(self, text: str) -> None:
        path = Path(text.strip()) if text.strip() else None
        self.state.set_target(path)
        self._update_install_warning(path)

    def _stock_changed(self, text: str) -> None:
        self.state.set_stock(Path(text.strip()) if text.strip() else None)

    def _update_install_warning(self, path: Path | None) -> None:
        install = looks_like_game_install(path) if path else None
        self.install_warning.setVisible(install is not None)
        if install:
            self.install_warning.setText(
                f"⚠ This is inside a Star Citizen install ({install}). "
                f"A backup is taken first, and it can be restored below."
            )

    # --- mode ----------------------------------------------------------------

    def _mode_changed(self, index: int) -> None:
        self.state.profile.injection.mode = self.mode.itemData(index).value
        self.state.touch_profile()

    def _sync_mode(self) -> None:
        index = self.mode.findData(self.state.profile.injection.merge_mode)
        if index >= 0 and index != self.mode.currentIndex():
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(index)
            self.mode.blockSignals(False)

    # --- plan ----------------------------------------------------------------

    def refresh_plan(self):
        self.plan_detail.clear()

        if self.state.contracts is None:
            self.plan_label.setText("Read your contracts on the Start tab first.")
            return None
        if not self.state.target or not self.state.target.is_file():
            self.plan_label.setText("Choose which file to change, above.")
            return None

        rendered = self.state.render()
        result = plan(LocalizationFile.load(self.state.target), rendered.values)

        self.plan_label.setText(result.summary())
        for key in result.skipped[:50]:
            self.plan_detail.addItem(f"no such key in target: {key}")
        for key, issue in result.errors[:50]:
            self.plan_detail.addItem(f"{key}: {issue}")
        return result

    # --- write ---------------------------------------------------------------

    def apply_changes(self) -> None:
        result = self.refresh_plan()
        if result is None:
            return
        if not result.is_valid:
            QMessageBox.critical(
                self, "Cannot apply",
                f"{len(result.errors)} value(s) would break in-game. Nothing was written.",
            )
            return
        if not self.confirm(result):
            return

        mode = self.state.profile.injection.merge_mode
        if mode is MergeMode.OVERWRITE and not self.state.stock:
            QMessageBox.warning(
                self, "A clean copy is needed",
                "Starting from a clean copy needs an unmodified version of the "
                "file. Choose one above, or switch back to the first option.",
            )
            return

        try:
            written = apply(
                self.state.target,
                self.state.render().values,
                confirmed=True,
                mode=mode,
                stock_path=self.state.stock,
                backup_dir=self.state.backup_dir,
            )
        except (ValidationFailedError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return

        self.refresh_backups()
        QMessageBox.information(self, "Applied", written.summary())

    def confirm(self, result) -> bool:
        install = looks_like_game_install(self.state.target)
        warning = (
            f"\n\nThis is inside a Star Citizen install:\n{install}"
            if install
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Apply changes?",
            f"Write to:\n{self.state.target}\n\n{result.summary()}"
            f"\n\nA backup is taken first.{warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    # --- backups -------------------------------------------------------------

    def refresh_backups(self) -> None:
        self.backups.clear()
        for path in self.state.backups():
            self.backups.addItem(str(path))

    def restore_backup(self) -> None:
        item = self.backups.currentItem()
        if not item or not self.state.target:
            return
        if QMessageBox.question(
            self, "Restore backup?",
            f"Replace:\n{self.state.target}\n\nwith:\n{item.text()}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        restore(Path(item.text()), self.state.target)
        QMessageBox.information(self, "Restored", "The file was put back.")
