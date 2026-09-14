"""Replayable, local-only workflow coach marks."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class CoachStep:
    page: str
    title: str
    body: str
    target: QWidget


class CoachTour(QDialog):
    """Guide focus through existing controls without reading or writing data."""

    completed = Signal()

    def __init__(self, steps: tuple[CoachStep, ...], navigate, parent=None):
        super().__init__(parent)
        if not steps:
            raise ValueError("guided tour requires at least one step")
        self.steps = steps
        self.navigate = navigate
        self.index = 0
        self.setWindowTitle("StarCompanion guided tour")
        self.setModal(False)
        self.setMinimumWidth(460)
        self.setAccessibleName("Replayable StarCompanion guided tour")

        self.progress = QLabel()
        self.progress.setProperty("role", "overline")
        self.title = QLabel()
        self.title.setProperty("role", "section-title")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setProperty("role", "muted")
        self.back = QPushButton("Back")
        self.next = QPushButton("Next")
        self.skip = QPushButton("Skip tour")
        self.back.clicked.connect(self.previous_step)
        self.next.clicked.connect(self.next_step)
        self.skip.clicked.connect(self.finish)

        actions = QHBoxLayout()
        actions.addWidget(self.skip)
        actions.addStretch(1)
        actions.addWidget(self.back)
        actions.addWidget(self.next)
        layout = QVBoxLayout(self)
        layout.addWidget(self.progress)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addLayout(actions)
        self.show_step(0)

    def show_step(self, index: int) -> None:
        previous = self.steps[self.index].target
        previous.setProperty("coachTarget", False)
        previous.style().unpolish(previous)
        previous.style().polish(previous)
        self.index = max(0, min(index, len(self.steps) - 1))
        step = self.steps[self.index]
        self.navigate(step.page)
        self.progress.setText(f"STEP {self.index + 1} OF {len(self.steps)}")
        self.title.setText(step.title)
        self.body.setText(step.body)
        self.back.setEnabled(self.index > 0)
        self.next.setText("Finish" if self.index == len(self.steps) - 1 else "Next")
        step.target.setProperty("coachTarget", True)
        step.target.style().unpolish(step.target)
        step.target.style().polish(step.target)
        step.target.setFocus()

    def previous_step(self) -> None:
        self.show_step(self.index - 1)

    def next_step(self) -> None:
        if self.index == len(self.steps) - 1:
            self.finish()
        else:
            self.show_step(self.index + 1)

    def _clear_target(self) -> None:
        target = self.steps[self.index].target
        target.setProperty("coachTarget", False)
        target.style().unpolish(target)
        target.style().polish(target)

    def done(self, result: int) -> None:
        self._clear_target()
        super().done(result)

    def finish(self) -> None:
        self.completed.emit()
        self.accept()


__all__ = ["CoachStep", "CoachTour"]
