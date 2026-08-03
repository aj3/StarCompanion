"""What to show tab: which pieces of information get added.

Each option is named for what the player sees in game, not for the field it
maps to. Options that cannot do anything yet say so, rather than sitting there
ticked and apparently doing nothing.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from ..state import AppState

# (profile field, what it is called, what it actually adds, needs reward data)
TOGGLES: tuple[tuple[str, str, str, bool], ...] = (
    (
        "reputation",
        "Reputation earned",
        "How much standing the contract pays with its mission giver.",
        True,
    ),
    (
        "blueprints",
        "Blueprints it can drop",
        "The list of blueprints a contract can award.",
        True,
    ),
    (
        "owned",
        "Mark blueprints you already have",
        "Needs your collection exported from SCMDB.",
        True,
    ),
    (
        "rank_gates",
        "Which rank a blueprint needs",
        "Some drops only happen once you have enough standing.",
        True,
    ),
    (
        "regional_variants",
        "Differences by location",
        "The same contract can drop different items in different systems. "
        "Detailed, and makes descriptions longer.",
        True,
    ),
    (
        "scenario_points",
        "Event contribution points",
        "For limited-time events, such as XenoThreat.",
        True,
    ),
    (
        "scrip",
        "MG Scrip",
        "Flags contracts that pay Scrip. The amount changes, so only the fact "
        "is shown.",
        True,
    ),
    (
        "caveats",
        "Warnings about unreliable data",
        "Notes when a blueprint list is known to be wrong or disputed.",
        True,
    ),
)


class FieldsTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        self.boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        box = QGroupBox("Add this to contract descriptions")
        inner = QVBoxLayout(box)

        for name, label, hint, _needs_rewards in TOGGLES:
            check = QCheckBox(label)
            check.toggled.connect(lambda checked, n=name: self._set(n, checked))
            self.boxes[name] = check
            inner.addWidget(check)

            caption = QLabel(hint)
            caption.setWordWrap(True)
            caption.setEnabled(False)
            caption.setIndent(24)
            inner.addWidget(caption)

        layout.addWidget(box)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        layout.addStretch(1)

        state.profileChanged.connect(self.refresh)
        state.contractsChanged.connect(self.refresh)
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

        contracts = self.state.contracts
        has_rewards = bool(
            contracts and any(not c.reward.is_empty for c in contracts.contracts)
        )
        self.notice.setText(
            ""
            if has_rewards
            else (
                "None of these can be shown yet. Star Citizen does not store "
                "reward information on your computer, so it has to come from a "
                "contract list — see step 2 on the Start tab."
            )
        )
