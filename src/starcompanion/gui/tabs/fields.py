"""What to show tab: which pieces of information get added.

Each option is named for what the player sees in game, not for the field it
maps to. Options that cannot do anything yet say so, rather than sitting there
ticked and apparently doing nothing.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QVBoxLayout, QWidget

from ...features import community_rewards_enabled
from ..components import MetricTile, NoticeBanner, SectionCard, ToggleRow, Tone
from ..state import AppState
from ..theme import SPACING

# (profile field, what it is called, what it actually adds, needs reward data)
TOGGLES: tuple[tuple[str, str, str, bool], ...] = (
    (
        "reputation",
        "Reputation earned",
        "How much standing the contract pays with its mission giver.",
        False,
    ),
    (
        "blueprints",
        "Blueprints it can drop",
        "The list of blueprints a contract can award.",
        False,
    ),
    (
        "item_rewards",
        "Items it awards directly",
        "Named item rewards found in your local game data.",
        False,
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
        self.rows: dict[str, ToggleRow] = {}
        self.setAccessibleName("Contract content settings")
        self.setAccessibleDescription(
            "Choose which locally derived mission facts are added to contract descriptions."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.large)

        summary = QGridLayout()
        summary.setSpacing(SPACING.medium)
        self.enabled_metric = MetricTile("Enabled fields")
        self.coverage_metric = MetricTile("Local reward coverage")
        summary.addWidget(self.enabled_metric, 0, 0)
        summary.addWidget(self.coverage_metric, 0, 1)
        summary.setColumnStretch(0, 1)
        summary.setColumnStretch(1, 1)
        layout.addLayout(summary)

        core = SectionCard(
            "Core mission intelligence",
            "These facts come from the selected local game build and remain empty when the provider has no evidence.",
        )
        extended = SectionCard(
            "Extended reward details",
            "Optional community-assisted fields remain isolated behind the explicit capability flag.",
        )
        extended.setVisible(community_rewards_enabled())
        self.core_section = core
        self.extended_section = extended
        focus_order: list[QCheckBox] = []

        for name, label, hint, needs_rewards in TOGGLES:
            if needs_rewards and not community_rewards_enabled():
                # Hidden rather than shown-and-inert: a tick box that provably
                # cannot do anything reads as a broken feature.
                continue

            row = ToggleRow(label, hint)
            check = row.checkbox
            check.toggled.connect(lambda checked, n=name: self._set(n, checked))
            self.boxes[name] = check
            self.rows[name] = row
            (extended if needs_rewards else core).add_widget(row)
            focus_order.append(check)

        layout.addWidget(core)
        layout.addWidget(extended)

        self.notice = NoticeBanner(tone=Tone.WARNING)
        layout.addWidget(self.notice)
        layout.addStretch(1)

        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

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
        enabled = sum(check.isChecked() for check in self.boxes.values())
        self.enabled_metric.set_value(f"{enabled} / {len(self.boxes)}")
        if contracts is None:
            self.coverage_metric.set_value("Not loaded", "Read the local game archive first")
        elif has_rewards:
            enhanced = sum(1 for contract in contracts.contracts if not contract.reward.is_empty)
            self.coverage_metric.set_value(
                f"{enhanced:,} contracts",
                f"of {len(contracts.contracts):,} loaded",
            )
        else:
            self.coverage_metric.set_value("No matches", "Provider evidence remains visible")
        if contracts is None:
            notice = (
                "Local reward facts have not been loaded. Return to Overview to read "
                "the selected game build."
            )
        elif not has_rewards:
            notice = (
                "No local reward facts matched this build. Check provider status "
                "under Data & provenance or return to Overview."
            )
        else:
            notice = ""
        self.notice.setText(notice)
        self.notice.setVisible(not has_rewards)
