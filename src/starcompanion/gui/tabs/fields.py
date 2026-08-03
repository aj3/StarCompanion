"""Fields tab: which reward information appears at all."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from ..state import AppState

# (profile attribute, label, why you might turn it off)
TOGGLES = (
    ("reputation", "Reputation awarded", "Rep amount gained on completion"),
    ("blueprints", "Blueprint pools", "Items the contract can drop"),
    ("scenario_points", "Scenario progress points", "Event contributions, e.g. XenoThreat"),
    ("scrip", "MG Scrip", "Flags contracts paying Scrip; amount is dynamic"),
    ("rank_gates", "Rank gates", "Which reputation tier a pool actually drops at"),
    ("regional_variants", "Regional variants", "Pools that differ by system; verbose"),
    ("caveats", "Data caveats", "Warnings that a pool may be mis-assigned by CIG"),
)


class FieldsTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        self.boxes: dict[str, QCheckBox] = {}

        box = QGroupBox("Show in contract text")
        inner = QVBoxLayout(box)

        for name, label, hint in TOGGLES:
            check = QCheckBox(label)
            check.setToolTip(hint)
            check.toggled.connect(lambda checked, n=name: self._set(n, checked))
            self.boxes[name] = check
            inner.addWidget(check)

            caption = QLabel(hint)
            caption.setEnabled(False)
            caption.setIndent(24)
            inner.addWidget(caption)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addStretch(1)

        state.profileChanged.connect(self.refresh)
        self.refresh()

    def _set(self, name: str, checked: bool) -> None:
        if self._loading:
            return
        setattr(self.state.profile.fields, name, checked)
        self.state.touch_profile()

    def refresh(self) -> None:
        self._loading = True
        try:
            for name, check in self.boxes.items():
                check.setChecked(getattr(self.state.profile.fields, name))
        finally:
            self._loading = False
