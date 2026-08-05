"""Source tab: choose where contract data comes from and load it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import cache
from ...model import ProviderStatus
from ...sources import contracts_ini
from ..components import EmptyState, MetricTile, ProviderHealthCard, SectionCard, Tone
from ..state import AppState
from ..theme import SPACING


class SourceTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.provider_cards: list[ProviderHealthCard] = []
        self.setAccessibleName("Data and provenance")
        self.setAccessibleDescription(
            "Inspect local dataset coverage, provider health, evidence counts, and diagnostics."
        )

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to contracts.ini")
        self.path_edit.setAccessibleName("Community contract-list path")
        self.path_edit.setAccessibleDescription(
            "Optional local contracts.ini path; normal local extraction is performed on Overview."
        )
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setAccessibleName("Browse for a local contract list")
        self.browse_button.setAccessibleDescription("Choose a local contracts.ini file.")
        self.browse_button.clicked.connect(self._browse)

        self.load_button = QPushButton("Load contracts")
        self.load_button.setAccessibleName("Load the selected contract list")
        self.load_button.setAccessibleDescription(
            "Load the selected local contract list into the current workspace."
        )
        self.load_button.clicked.connect(self.load_contracts)

        self.save_cache_button = QPushButton("Save cache…")
        self.save_cache_button.setAccessibleName("Save the current local cache")
        self.save_cache_button.setAccessibleDescription(
            "Save the current normalized contract dataset to a local cache."
        )
        self.save_cache_button.clicked.connect(self._save_cache)
        self.load_cache_button = QPushButton("Load cache…")
        self.load_cache_button.setAccessibleName("Load a local cache")
        self.load_cache_button.setAccessibleDescription(
            "Load a previously saved normalized local cache."
        )
        self.load_cache_button.clicked.connect(self._load_cache)

        self.summary = QLabel("No contract data loaded.")
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "muted")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary.setAccessibleName("Detailed provider diagnostics")

        picker = QHBoxLayout()
        picker.addWidget(self.path_edit, 1)
        picker.addWidget(self.browse_button)

        buttons = QHBoxLayout()
        buttons.addWidget(self.load_button)
        buttons.addStretch(1)
        buttons.addWidget(self.load_cache_button)
        buttons.addWidget(self.save_cache_button)

        self.dataset_section = SectionCard(
            "Local dataset summary",
            "Aggregate counts from the normalized contract model currently loaded in this workspace.",
        )
        metrics = QGridLayout()
        metrics.setSpacing(SPACING.small)
        self.contract_metric = MetricTile("Contracts")
        self.org_metric = MetricTile("Mission givers")
        self.key_metric = MetricTile("Localization keys")
        self.evidence_metric = MetricTile("Contract evidence")
        metrics.addWidget(self.contract_metric, 0, 0)
        metrics.addWidget(self.org_metric, 0, 1)
        metrics.addWidget(self.key_metric, 0, 2)
        metrics.addWidget(self.evidence_metric, 0, 3)
        for column in range(4):
            metrics.setColumnStretch(column, 1)
        self.dataset_section.add_layout(metrics)
        self.dataset_empty = EmptyState(
            "No local dataset loaded",
            "Use Overview to read your installed game, or use the manual tools below for an explicit local file.",
        )
        self.dataset_section.add_widget(self.dataset_empty)

        self.providers_section = SectionCard(
            "Provider health",
            "Capability and coverage reported by each local enhancement provider for this build.",
        )
        self.provider_empty = EmptyState(
            "No provider reports available",
            "Provider capability appears after local game data has been extracted and normalized.",
        )
        self.providers_section.add_widget(self.provider_empty)
        self.provider_layout = self.providers_section.body_layout

        diagnostics = SectionCard(
            "Technical provenance summary",
            "Selectable aggregate diagnostics; source values and player paths are not displayed here.",
        )
        diagnostics.add_widget(self.summary)

        tools = SectionCard(
            "Manual data tools",
            "Optional local import/cache actions for explicit testing and recovery workflows.",
        )
        tools.add_layout(picker)
        tools.add_layout(buttons)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.large)
        layout.addWidget(self.dataset_section)
        layout.addWidget(self.providers_section)
        layout.addWidget(diagnostics)
        layout.addWidget(tools)
        layout.addStretch(1)

        focus_order = [
            self.path_edit,
            self.browse_button,
            self.load_button,
            self.load_cache_button,
            self.save_cache_button,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.contractsChanged.connect(self.refresh)
        self.refresh()

    # --- actions -------------------------------------------------------------

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select contracts.ini", "", "INI (*.ini)")
        if path:
            self.path_edit.setText(path)

    def load_contracts(self) -> None:
        path = Path(self.path_edit.text().strip())
        if not path.is_file():
            self._warn("Not found", f"{path} is not a file.")
            return
        try:
            self.state.set_contracts(contracts_ini.load(path))
        except (OSError, ValueError) as exc:
            self._warn("Could not load contracts", str(exc))

    def _save_cache(self) -> None:
        if self.state.contracts is None:
            self._warn("Nothing to save", "Load contract data first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save cache", "cache.json", "JSON (*.json)")
        if path:
            cache.save(self.state.contracts, Path(path), source="gui")

    def _load_cache(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load cache", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.state.set_contracts(cache.load(Path(path)))
        except (OSError, ValueError) as exc:
            self._warn("Could not load cache", str(exc))

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        text = self.summary_text()
        self.summary.setText(text)
        self.summary.setAccessibleDescription(text)
        contracts = self.state.contracts
        self.dataset_empty.setVisible(contracts is None)

        if contracts is None:
            for metric in (
                self.contract_metric,
                self.org_metric,
                self.key_metric,
                self.evidence_metric,
            ):
                metric.set_value("—")
        else:
            evidence = sum(len(contract.evidence) for contract in contracts.contracts)
            self.contract_metric.set_value(f"{len(contracts.contracts):,}")
            self.org_metric.set_value(f"{len(contracts.orgs):,}")
            self.key_metric.set_value(f"{self.state.key_count:,}")
            self.evidence_metric.set_value(f"{evidence:,}")

        self._rebuild_provider_cards(contracts.capabilities if contracts else [])

    def _rebuild_provider_cards(self, capabilities) -> None:
        for card in self.provider_cards:
            self.provider_layout.removeWidget(card)
            card.deleteLater()
        self.provider_cards.clear()
        self.provider_empty.setVisible(not capabilities)

        tones = {
            ProviderStatus.AVAILABLE: Tone.SUCCESS,
            ProviderStatus.DEGRADED: Tone.WARNING,
            ProviderStatus.UNAVAILABLE: Tone.DANGER,
            ProviderStatus.DISABLED: Tone.NEUTRAL,
        }
        for capability in capabilities:
            card = ProviderHealthCard(capability.provider)
            diagnostic = capability.diagnostics[0] if capability.diagnostics else ""
            card.set_health(
                status=capability.status.value.title(),
                tone=tones[capability.status],
                version=capability.version,
                build=capability.build_version,
                facts_seen=capability.facts_seen,
                contracts_enhanced=capability.contracts_enhanced,
                evidence_links=capability.evidence_links,
                matched_facts=capability.matched_facts,
                reward_facts=capability.reward_facts,
                unmatched_facts=capability.unmatched_facts,
                diagnostic=diagnostic,
            )
            self.provider_layout.addWidget(card)
            self.provider_cards.append(card)

    def summary_text(self) -> str:
        contracts = self.state.contracts
        if contracts is None:
            return "No contract data loaded."

        contract_count = len(contracts.contracts)
        org_count = len(contracts.orgs)
        lines = [
            f"{contract_count} {'contract' if contract_count == 1 else 'contracts'} "
            f"across {org_count} {'mission giver' if org_count == 1 else 'mission givers'}",
            f"{self.state.key_count} localization keys",
        ]
        if contracts.unparsed:
            lines.append(f"{len(contracts.unparsed)} with no reward data found")
        lines.append(
            f"{sum(len(contract.evidence) for contract in contracts.contracts):,} provenance evidence links"
        )
        for capability in contracts.capabilities:
            lines.append(
                f"{capability.provider} v{capability.version}: {capability.status.value}; "
                f"{capability.contracts_enhanced:,} contracts enhanced "
                f"(build {capability.build_version})"
            )
            lines.append(
                f"  coverage: {capability.matched_facts:,}/"
                f"{capability.reward_facts:,} reward facts matched; "
                f"{capability.unmatched_facts:,} unmatched"
            )
            if capability.diagnostic_counts:
                lines.append(
                    "  diagnostics: "
                    + ", ".join(
                        f"{category}={count:,}"
                        for category, count in capability.diagnostic_counts
                    )
                )
            if capability.unmatched_reason_counts:
                lines.append(
                    "  unmatched reasons: "
                    + ", ".join(
                        f"{reason}={count:,}"
                        for reason, count in capability.unmatched_reason_counts
                    )
                )
            if capability.diagnostics:
                lines.append(f"  diagnostic: {capability.diagnostics[0]}")
            if capability.unmatched_samples:
                lines.append(f"  unmatched: {capability.unmatched_samples[0]}")
        return "\n".join(lines)

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
