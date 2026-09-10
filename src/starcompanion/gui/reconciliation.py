"""Virtualized, explicit reconciliation of conflicting localization values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..user_edits import KeyResolution, ReconciliationChoice, plan_import


_CHOICE_LABELS = {
    ReconciliationChoice.KEEP: "Keep current",
    ReconciliationChoice.IMPORT: "Use imported",
    ReconciliationChoice.APPEND: "Append imported",
    ReconciliationChoice.PREPEND: "Prepend imported",
    ReconciliationChoice.CUSTOM: "Custom value",
}


def _elide(value: str, limit: int = 180) -> str:
    compact = value.replace("\r", "").replace("\n", " ↵ ")
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class ReconciliationTableModel(QAbstractTableModel):
    """One virtual row per conflict, backed by the shared import planner."""

    HEADERS = ("Localization key", "Current value", "Imported value", "Choice")
    KEY, CURRENT, INCOMING, CHOICE = range(len(HEADERS))
    completionChanged = Signal(int, int)

    def __init__(
        self,
        current: Mapping[str, str],
        incoming: Mapping[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self._current = dict(current)
        self._incoming = dict(incoming)
        initial = plan_import(self._current, self._incoming)
        self._keys = initial.conflicts
        self._resolutions: dict[str, KeyResolution] = {}

    @property
    def conflict_keys(self) -> tuple[str, ...]:
        return self._keys

    @property
    def resolved_count(self) -> int:
        return len(self._resolutions)

    @property
    def complete(self) -> bool:
        return self.resolved_count == len(self._keys)

    def resolutions(self) -> dict[str, KeyResolution] | None:
        """Return a defensive copy only after every conflict is explicit."""

        return dict(self._resolutions) if self.complete else None

    def resolve_rows(
        self,
        rows: Iterable[int],
        choice: ReconciliationChoice | str,
        *,
        custom_value: str | None = None,
    ) -> tuple[str, ...]:
        """Apply one explicit choice to selected virtual rows as one update."""

        selected_choice = ReconciliationChoice(choice)
        selected_rows = tuple(sorted(set(rows)))
        if not selected_rows:
            return ()
        if selected_rows[0] < 0 or selected_rows[-1] >= len(self._keys):
            raise IndexError("reconciliation row is outside the model")
        if selected_choice is ReconciliationChoice.CUSTOM:
            if len(selected_rows) != 1:
                raise ValueError("a custom value requires exactly one selected conflict")
            if custom_value is None:
                raise ValueError("a custom resolution requires an explicit value")
        elif custom_value is not None:
            raise ValueError("custom text is only valid for a custom resolution")

        candidate = dict(self._resolutions)
        resolution = KeyResolution(selected_choice, custom_value)
        changed_rows: list[int] = []
        for row in selected_rows:
            key = self._keys[row]
            if candidate.get(key) != resolution:
                candidate[key] = resolution
                changed_rows.append(row)
        if not changed_rows:
            return ()

        # The shared model validates custom values and the exact conflict keys;
        # unresolved rows remain unresolved instead of receiving a default.
        plan_import(self._current, self._incoming, resolutions=candidate)
        self._resolutions = candidate
        first, last = min(changed_rows), max(changed_rows)
        self.dataChanged.emit(
            self.index(first, self.CHOICE),
            self.index(last, self.CHOICE),
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.AccessibleTextRole,
            ],
        )
        self.completionChanged.emit(self.resolved_count, len(self._keys))
        return tuple(self._keys[row] for row in changed_rows)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._keys)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            not index.isValid()
            or not 0 <= index.row() < len(self._keys)
            or not 0 <= index.column() < len(self.HEADERS)
        ):
            return None
        key = self._keys[index.row()]
        resolution = self._resolutions.get(key)
        choice = "Unresolved" if resolution is None else _CHOICE_LABELS[resolution.choice]
        full_values = (key, self._current[key], self._incoming[key], choice)

        if role == Qt.ItemDataRole.DisplayRole:
            return _elide(full_values[index.column()])
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.CHOICE and resolution is not None:
                if resolution.choice is ReconciliationChoice.CUSTOM:
                    return f"Custom value: {resolution.custom_value}"
                return _CHOICE_LABELS[resolution.choice]
            return full_values[index.column()]
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{self.HEADERS[index.column()]}: {full_values[index.column()]}"
        return None


class ReconciliationDialog(QDialog):
    """Explicit UI for completing a conflict-resolution mapping."""

    def __init__(
        self,
        current: Mapping[str, str],
        incoming: Mapping[str, str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Reconcile imported localization")
        self.setModal(True)
        self.resize(960, 560)
        self.setAccessibleName("Localization conflict reconciliation")
        self.setAccessibleDescription(
            "Review every conflicting localization key and explicitly choose how to resolve it."
        )

        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Every conflict starts unresolved. Select one or more rows, then apply an "
            "explicit choice. Custom text is available for one row at a time."
        )
        introduction.setWordWrap(True)
        introduction.setAccessibleName("Reconciliation instructions")
        introduction.setAccessibleDescription(introduction.text())
        layout.addWidget(introduction)

        self.model = ReconciliationTableModel(current, incoming, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)
        self.table.setAccessibleName("Localization conflicts")
        self.table.setAccessibleDescription(
            "Virtualized conflict table with key, current value, imported value, "
            "and explicit choice."
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.keep_button = self._choice_button(
            "Keep current",
            "Keep the existing user value for every selected conflict.",
            ReconciliationChoice.KEEP,
        )
        self.import_button = self._choice_button(
            "Use imported",
            "Replace every selected conflict with its imported value.",
            ReconciliationChoice.IMPORT,
        )
        self.append_button = self._choice_button(
            "Append imported",
            "Append each imported value to the corresponding current value.",
            ReconciliationChoice.APPEND,
        )
        self.prepend_button = self._choice_button(
            "Prepend imported",
            "Prepend each imported value to the corresponding current value.",
            ReconciliationChoice.PREPEND,
        )
        self.custom_button = QPushButton("Custom…")
        self.custom_button.setAccessibleName("Set a custom conflict value")
        self.custom_button.setAccessibleDescription(
            "Enter an explicit custom value for the one selected conflict."
        )
        self.custom_button.clicked.connect(self._prompt_custom)
        for button in (
            self.keep_button,
            self.import_button,
            self.append_button,
            self.prepend_button,
            self.custom_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.status = QLabel()
        self.status.setAccessibleName("Reconciliation completion status")
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.accept_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.accept_button.setText("Use resolved import")
        self.accept_button.setAccessibleName("Use fully resolved import")
        self.accept_button.setAccessibleDescription(
            "Continue only after every conflict has an explicit resolution."
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.table.selectionModel().selectionChanged.connect(
            self._update_selection_actions
        )
        self.model.completionChanged.connect(self._update_completion)
        self._update_selection_actions()
        self._update_completion(self.model.resolved_count, self.model.rowCount())

    def _choice_button(
        self,
        text: str,
        description: str,
        choice: ReconciliationChoice,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.clicked.connect(
            lambda _checked=False, selected=choice: self.apply_selected(selected)
        )
        return button

    def _selected_rows(self) -> tuple[int, ...]:
        return tuple(
            sorted(index.row() for index in self.table.selectionModel().selectedRows())
        )

    def apply_selected(
        self,
        choice: ReconciliationChoice | str,
    ) -> tuple[str, ...]:
        selected = ReconciliationChoice(choice)
        if selected is ReconciliationChoice.CUSTOM:
            raise ValueError("use apply_custom for an explicit custom value")
        return self.model.resolve_rows(self._selected_rows(), selected)

    def apply_custom(self, value: str) -> tuple[str, ...]:
        return self.model.resolve_rows(
            self._selected_rows(),
            ReconciliationChoice.CUSTOM,
            custom_value=value,
        )

    def _prompt_custom(self) -> None:
        if len(self._selected_rows()) != 1:
            return
        value, accepted = QInputDialog.getMultiLineText(
            self,
            "Custom localization value",
            "Enter the complete value for the selected conflict:",
        )
        if accepted:
            self.apply_custom(value)

    def _update_selection_actions(self, *_args) -> None:
        count = len(self._selected_rows())
        for button in (
            self.keep_button,
            self.import_button,
            self.append_button,
            self.prepend_button,
        ):
            button.setEnabled(count > 0)
        self.custom_button.setEnabled(count == 1)

    def _update_completion(self, resolved: int, total: int) -> None:
        complete = resolved == total
        self.accept_button.setEnabled(complete)
        if total:
            text = f"{resolved:,} of {total:,} conflicts explicitly resolved."
        else:
            text = "This import has no conflicting values."
        self.status.setText(text)
        self.status.setAccessibleDescription(text)

    def resolutions(self) -> dict[str, KeyResolution] | None:
        return self.model.resolutions()

    def accept(self) -> None:
        if self.model.complete:
            super().accept()


__all__ = ["ReconciliationDialog", "ReconciliationTableModel"]
