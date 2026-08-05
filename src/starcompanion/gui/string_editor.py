"""Virtualized C3 source-graph and operation-plan string editor models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from ..ini import BOM, LocalizationFile
from ..inject import InjectionPlan, plan
from ..model import ContractSet, Evidence
from ..render import RenderResult
from ..source_graph import Contribution, ResolvedEntry, SourceGraph, SourceKind, SourceLayer
from ..user_edits import Change, EditCommand, MAX_HISTORY
from ..validate import Issue, Severity


@dataclass(frozen=True)
class StringRecord:
    key: str
    category: str
    organization: str
    family: str
    stock: str | None
    rendered: str | None
    merged: str
    winner: Contribution
    contributions: tuple[Contribution, ...]
    evidence: tuple[Evidence, ...]
    operation: str
    issues: tuple[Issue, ...] = ()

    @property
    def modified(self) -> bool:
        return self.stock != self.merged

    @property
    def conflicted(self) -> bool:
        return len({item.value for item in self.contributions}) > 1

    @property
    def missing(self) -> bool:
        return self.stock is None or self.rendered is None

    @property
    def invalid(self) -> bool:
        return any(issue.severity is Severity.ERROR for issue in self.issues)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(sorted({item.provider for item in self.evidence}))

    @property
    def search_text(self) -> str:
        return "\0".join(
            (
                self.key,
                self.category,
                self.organization,
                self.family,
                self.stock or "",
                self.rendered or "",
                self.merged,
                self.winner.source_id,
                self.winner.kind.value,
                *self.providers,
            )
        ).casefold()


@dataclass(frozen=True)
class StringEditorSnapshot:
    records: tuple[StringRecord, ...]
    plan: InjectionPlan
    source_counts: dict[str, int]
    provider_names: tuple[str, ...]
    category_names: tuple[str, ...]
    source_names: tuple[str, ...]
    by_key: dict[str, StringRecord] = field(repr=False)

    @property
    def invalid_count(self) -> int:
        return sum(record.invalid for record in self.records)

    @property
    def modified_count(self) -> int:
        return sum(record.modified for record in self.records)

    @property
    def conflict_count(self) -> int:
        return sum(record.conflicted for record in self.records)


def build_string_snapshot(
    contracts: ContractSet,
    rendered: RenderResult,
    *,
    profile_name: str,
    user_values: dict[str, str] | None = None,
) -> StringEditorSnapshot:
    """Build one immutable projection using only existing C3 value models."""

    user_values = dict(user_values or {})
    stock: dict[str, str] = {}
    categories: dict[str, str] = {}
    organizations: dict[str, str] = {}
    families: dict[str, str] = {}
    for contract in contracts.contracts:
        # Iterate the existing kind buckets directly. Contract.kind_of() is a
        # convenient single-key helper but using it once per row would turn a
        # large variant group into an O(n²) first build.
        for kind, keys in contract.keys.items():
            for key in keys:
                stock[key] = contract.text(key) or contract.base_text(key) or ""
                categories[key] = kind.value
                organizations[key] = contract.org.name
                families[key] = contract.family

    generated_provenance = {
        key: tuple(_evidence_text(item) for item in rendered.provenance.get(key, ()))
        for key in rendered.values
    }
    layers = [
        SourceLayer("cig-localization", SourceKind.STOCK, stock),
        SourceLayer(
            f"profile:{profile_name}",
            SourceKind.GENERATED,
            rendered.values,
            provenance=generated_provenance,
        ),
    ]
    if user_values:
        layers.append(SourceLayer("user.ini", SourceKind.USER, user_values))
    merged = SourceGraph(layers).resolve()

    baseline = _localization(stock)
    operation_plan = plan(baseline, merged.values)
    outcomes: dict[str, str] = {}
    for label, keys in (
        ("add", operation_plan.added),
        ("change", operation_plan.updated),
        ("remove", operation_plan.removed),
        ("unchanged", operation_plan.unchanged),
        ("skipped", operation_plan.skipped),
    ):
        outcomes.update((key, label) for key in keys)
    issues: dict[str, list[Issue]] = {}
    for key, issue in (*operation_plan.warnings, *operation_plan.errors):
        issues.setdefault(key, []).append(issue)

    records = tuple(
        StringRecord(
            key=key,
            category=categories.get(key, "unknown"),
            organization=organizations.get(key, "Unknown"),
            family=families.get(key, "Unknown"),
            stock=stock.get(key),
            rendered=rendered.values.get(key),
            merged=entry.value,
            winner=entry.winner,
            contributions=entry.contributions,
            evidence=tuple(rendered.provenance.get(key, ())),
            operation=outcomes.get(key, "unchanged"),
            issues=tuple(issues.get(key, ())),
        )
        for key, entry in merged.entries.items()
    )
    source_counts: dict[str, int] = {}
    for record in records:
        source_counts[record.winner.source_id] = source_counts.get(record.winner.source_id, 0) + 1
    return StringEditorSnapshot(
        records=records,
        plan=operation_plan,
        source_counts=source_counts,
        provider_names=tuple(sorted({provider for record in records for provider in record.providers})),
        category_names=tuple(sorted({record.category for record in records})),
        source_names=tuple(sorted({record.winner.kind.value for record in records})),
        by_key={record.key: record for record in records},
    )


def _localization(values: dict[str, str]) -> LocalizationFile:
    body = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    return LocalizationFile.loads(BOM + body + ("\n" if body else ""))


def _evidence_text(item: Evidence) -> str:
    return f"{item.provider}:{item.record_id}:{item.field_path}"


class StringEditorDocument:
    """In-memory C3 command document; persistence is an explicit outer action."""

    def __init__(self, values: dict[str, str] | None = None):
        self.baseline_values: dict[str, str] = dict(values or {})
        self.values: dict[str, str] = dict(self.baseline_values)
        self.commands: list[EditCommand] = []
        self.cursor = 0

    @property
    def dirty(self) -> bool:
        return self.values != self.baseline_values

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.commands)

    def load(self, values: dict[str, str]) -> None:
        self.baseline_values = dict(values)
        self.values = dict(values)
        self.commands = []
        self.cursor = 0

    def set_value(self, key: str, value: str) -> tuple[str, ...]:
        if self.values.get(key) == value:
            return ()
        return self.execute(EditCommand.set(self.values, key, value))

    def reset(self, keys: Iterable[str]) -> tuple[str, ...]:
        changes = tuple(
            Change(key, self.values[key], None)
            for key in dict.fromkeys(keys)
            if key in self.values
        )
        if not changes:
            return ()
        return self.execute(EditCommand("reset selected values to source", changes))

    def execute(self, command: EditCommand) -> tuple[str, ...]:
        updated = command.apply(self.values)
        commands = self.commands[: self.cursor] + [command]
        if len(commands) > MAX_HISTORY:
            commands = commands[-MAX_HISTORY:]
        self.values = updated
        self.commands = commands
        self.cursor = len(commands)
        return tuple(change.key for change in command.changes)

    def undo(self) -> tuple[str, ...]:
        if not self.can_undo:
            return ()
        command = self.commands[self.cursor - 1]
        self.values = command.undo(self.values)
        self.cursor -= 1
        return tuple(change.key for change in command.changes)

    def redo(self) -> tuple[str, ...]:
        if not self.can_redo:
            return ()
        command = self.commands[self.cursor]
        self.values = command.apply(self.values)
        self.cursor += 1
        return tuple(change.key for change in command.changes)

    def save_command(self) -> EditCommand | None:
        changes = tuple(
            Change(key, self.baseline_values.get(key), self.values.get(key))
            for key in sorted(set(self.baseline_values) | set(self.values))
            if self.baseline_values.get(key) != self.values.get(key)
        )
        return EditCommand("save advanced editor changes", changes) if changes else None


class StringTableModel(QAbstractTableModel):
    HEADERS = (
        "Localization key",
        "Category",
        "Stock",
        "Rendered",
        "Merged",
        "Winning source",
        "Plan outcome",
    )
    KEY, CATEGORY, STOCK, RENDERED, MERGED, SOURCE, OUTCOME = range(len(HEADERS))
    RecordRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, document: StringEditorDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.snapshot: StringEditorSnapshot | None = None
        self._contracts: ContractSet | None = None
        self._rendered: RenderResult | None = None
        self._profile_name = "default"

    def set_inputs(
        self,
        contracts: ContractSet,
        rendered: RenderResult,
        profile_name: str,
    ) -> None:
        self._contracts = contracts
        self._rendered = rendered
        self._profile_name = profile_name
        self.rebuild()

    def rebuild(self) -> None:
        self.beginResetModel()
        if self._contracts is None or self._rendered is None:
            self.snapshot = None
        else:
            self.snapshot = build_string_snapshot(
                self._contracts,
                self._rendered,
                profile_name=self._profile_name,
                user_values=self.document.values,
            )
        self.endResetModel()

    def record(self, row: int) -> StringRecord | None:
        if self.snapshot is None or not 0 <= row < len(self.snapshot.records):
            return None
        return self.snapshot.records[row]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() or self.snapshot is None else len(self.snapshot.records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        record = self.record(index.row())
        if record is None:
            return None
        if role == self.RecordRole:
            return record
        values = (
            record.key,
            record.category.title(),
            record.stock or "",
            record.rendered or "",
            record.merged,
            f"{record.winner.source_id} ({record.winner.kind.value})",
            record.operation.title(),
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return _elide(values[index.column()])
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.EditRole):
            return values[index.column()]
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{self.HEADERS[index.column()]}: {values[index.column()]}"
        return None

def _elide(value: str, limit: int = 140) -> str:
    compact = value.replace("\n", " ↵ ").replace("\r", "")
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class StringFilterProxyModel(QSortFilterProxyModel):
    """Cached-record filters over a virtualized source table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.state_filter = "all"
        self.source_filter = "all"
        self.category_filter = "all"
        self.provider_filter = "all"
        self.setDynamicSortFilter(True)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_query(self, value: str) -> None:
        normalized = value.strip().casefold()
        if normalized != self.query:
            self.query = normalized
            self._refilter()

    def set_state_filter(self, value: str) -> None:
        self.state_filter = value
        self._refilter()

    def set_source_filter(self, value: str) -> None:
        self.source_filter = value
        self._refilter()

    def set_category_filter(self, value: str) -> None:
        self.category_filter = value
        self._refilter()

    def set_provider_filter(self, value: str) -> None:
        self.provider_filter = value
        self._refilter()

    def _refilter(self) -> None:
        self.beginFilterChange()
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        record = model.record(source_row) if isinstance(model, StringTableModel) else None
        if record is None:
            return False
        if self.query and self.query not in record.search_text:
            return False
        if self.state_filter == "modified" and not record.modified:
            return False
        if self.state_filter == "conflict" and not record.conflicted:
            return False
        if self.state_filter == "missing" and not record.missing:
            return False
        if self.state_filter == "invalid" and not record.invalid:
            return False
        if self.state_filter == "warning" and not record.issues:
            return False
        if self.source_filter != "all" and record.winner.kind.value != self.source_filter:
            return False
        if self.category_filter != "all" and record.category != self.category_filter:
            return False
        if self.provider_filter != "all" and self.provider_filter not in record.providers:
            return False
        return True


__all__ = [
    "StringEditorDocument",
    "StringEditorSnapshot",
    "StringFilterProxyModel",
    "StringRecord",
    "StringTableModel",
    "build_string_snapshot",
]
