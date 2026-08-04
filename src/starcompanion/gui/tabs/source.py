"""Source tab: choose where contract data comes from and load it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import cache
from ...sources import contracts_ini
from ..state import AppState


class SourceTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to contracts.ini")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)

        load = QPushButton("Load contracts")
        load.clicked.connect(self.load_contracts)

        save_cache = QPushButton("Save cache…")
        save_cache.clicked.connect(self._save_cache)
        load_cache = QPushButton("Load cache…")
        load_cache.clicked.connect(self._load_cache)

        self.summary = QLabel("No contract data loaded.")
        self.summary.setWordWrap(True)

        picker = QHBoxLayout()
        picker.addWidget(self.path_edit, 1)
        picker.addWidget(browse)

        buttons = QHBoxLayout()
        buttons.addWidget(load)
        buttons.addStretch(1)
        buttons.addWidget(load_cache)
        buttons.addWidget(save_cache)

        box = QGroupBox("Contract data")
        inner = QVBoxLayout(box)
        inner.addLayout(picker)
        inner.addLayout(buttons)
        inner.addWidget(self.summary)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addStretch(1)

        state.contractsChanged.connect(self.refresh)

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
        self.summary.setText(self.summary_text())

    def summary_text(self) -> str:
        contracts = self.state.contracts
        if contracts is None:
            return "No contract data loaded."

        lines = [
            f"{len(contracts.contracts)} contracts across {len(contracts.orgs)} mission givers",
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
