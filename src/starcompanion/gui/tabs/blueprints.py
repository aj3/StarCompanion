"""G2 blueprint ownership tracker over the C4 catalog and query APIs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...blueprints import (
    BlueprintCatalog,
    BlueprintQuery,
    BlueprintRow,
    OwnershipFilter,
    build_catalog,
    categories,
    grades,
    item_classes,
    item_types,
    missions,
    query_blueprints,
    reward_sources,
    sizes,
)
from ...ownership import (
    ImportPlan,
    ManualOwnershipPlan,
    OwnershipConflictError,
    OwnershipError,
    OwnershipDecision,
    OwnershipRecoveryAvailable,
    OwnershipState,
    OwnershipStore,
    ResolutionPlan,
    ScanCancelled,
    ScanResult,
    apply_import,
    apply_manual_ownership,
    apply_resolution,
    discover_logs,
    export_csv,
    export_json,
    ownership_scope,
    plan_import,
    plan_manual_ownership,
    plan_resolution,
    scan_logs,
    write_export,
)
from ..components import EmptyState, MetricTile, NoticeBanner, SectionCard, Tone
from ..jobs import QtOperationJob
from ..state import AppState


@dataclass(frozen=True)
class OwnershipSnapshot:
    channel: str
    link_live_hotfix: bool
    state: OwnershipState
    continuity_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnershipScanSnapshot:
    channel: str
    link_live_hotfix: bool
    result: ScanResult
    continuity_scopes: tuple[str, ...] = ()


class BlueprintTableModel(QAbstractTableModel):
    """Virtual read-only projection returned by the C4 query service."""

    HEADERS = (
        "Blueprint",
        "Category",
        "Type",
        "Class",
        "Size",
        "Grade",
        "Mission",
        "Owned",
        "Acquired",
        "Evidence",
        "Reward giver",
    )
    RecordRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: tuple[BlueprintRow, ...] = ()

    def set_rows(self, rows: tuple[BlueprintRow, ...]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        sources = ", ".join(row.acquisition_sources)
        rewards = ", ".join(sorted({item.org for item in row.entry.reward_sources}))
        mission_names = ", ".join(
            sorted(
                {item.contract_id for item in row.entry.reward_sources},
                key=str.casefold,
            )
        )
        values = (
            row.entry.name,
            row.entry.category.title(),
            row.entry.item_type.title(),
            row.entry.item_class.title() or "—",
            row.entry.size.upper() or "—",
            row.entry.grade.upper() or "—",
            mission_names or "—",
            "Owned" if row.owned else "Not owned",
            row.acquired_at or "—",
            sources or "—",
            rewards or "—",
        )
        if role == self.RecordRole:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{self.HEADERS[index.column()]}: {values[index.column()]}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{row.entry.blueprint_id}\n"
                f"{len(row.entry.reward_sources):,} reward source(s) / "
                f"{len(row.entry.evidence):,} evidence link(s)"
            )
        return None


class BlueprintTrackerTab(QWidget):
    """Channel-isolated ownership search and incremental log-scan UI."""

    linkLiveHotfixChanged = Signal(bool)
    DEFAULT_COLUMN_WIDTHS = (240, 105, 105, 105, 70, 70, 220, 90, 165, 100, 150)

    def __init__(
        self,
        state: AppState,
        parent: QWidget | None = None,
        *,
        link_live_hotfix: bool = True,
    ):
        super().__init__(parent)
        self.state = state
        self.catalog: BlueprintCatalog | None = None
        self.ownership: OwnershipState | None = None
        self.channel: str | None = None
        self.scope_name: str | None = None
        self.link_live_hotfix = bool(link_live_hotfix)
        self._continuity_scopes: tuple[str, ...] = ()
        self._recovery_target: tuple[str, bool] | None = None
        self._jobs: set[QtOperationJob] = set()
        self._shutting_down = False

        self.status = NoticeBanner(
            "Load local contract data to build the blueprint catalog.", tone=Tone.INFO
        )
        self.catalog_metric = MetricTile("Catalog")
        self.owned_metric = MetricTile("Owned")
        self.unresolved_metric = MetricTile("Unresolved")
        self.visible_metric = MetricTile("Visible")
        metrics = QGridLayout()
        for column, metric in enumerate(
            (self.catalog_metric, self.owned_metric, self.unresolved_metric, self.visible_metric)
        ):
            metrics.addWidget(metric, 0, column)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search blueprint names and exact aliases")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search blueprint catalog")
        self.search.setAccessibleDescription(
            "Debounced local search using the C4 blueprint query API."
        )
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.ownership_filter = QComboBox()
        self.ownership_filter.setAccessibleName("Blueprint ownership filter")
        self.ownership_filter.setAccessibleDescription(
            "Show all, owned, or not-owned entries using the C4 ownership join."
        )
        for label, value in (
            ("All ownership", OwnershipFilter.ALL),
            ("Owned", OwnershipFilter.OWNED),
            ("Not owned", OwnershipFilter.UNOWNED),
        ):
            self.ownership_filter.addItem(label, value)
        self.category_filter = QComboBox()
        self.category_filter.setAccessibleName("Blueprint category filter")
        self.reward_filter = QComboBox()
        self.reward_filter.setAccessibleName("Blueprint reward-source filter")
        self.mission_filter = QComboBox()
        self.mission_filter.setAccessibleName("Blueprint mission filter")
        self.type_filter = QComboBox()
        self.type_filter.setAccessibleName("Blueprint item type filter")
        self.class_filter = QComboBox()
        self.class_filter.setAccessibleName("Blueprint item class filter")
        self.size_filter = QComboBox()
        self.size_filter.setAccessibleName("Blueprint item size filter")
        self.grade_filter = QComboBox()
        self.grade_filter.setAccessibleName("Blueprint item grade filter")
        for combo in (
            self.category_filter,
            self.reward_filter,
            self.mission_filter,
            self.type_filter,
            self.class_filter,
            self.size_filter,
            self.grade_filter,
        ):
            combo.addItem("All", "")
            combo.currentIndexChanged.connect(self.refresh_query)
            combo.setAccessibleDescription("Filter the local C4 catalog without reading files.")
        self.ownership_filter.currentIndexChanged.connect(self.refresh_query)

        filter_layout = QGridLayout()
        filter_layout.addWidget(self.search, 0, 0, 1, 4)
        filter_layout.addWidget(self.ownership_filter, 1, 0)
        filter_layout.addWidget(self.category_filter, 1, 1)
        filter_layout.addWidget(self.type_filter, 1, 2)
        filter_layout.addWidget(self.class_filter, 1, 3)
        filter_layout.addWidget(self.size_filter, 2, 0)
        filter_layout.addWidget(self.grade_filter, 2, 1)
        filter_layout.addWidget(self.mission_filter, 2, 2)
        filter_layout.addWidget(self.reward_filter, 2, 3)
        filters = SectionCard(
            "Search ownership",
            "Filters query the channel-scoped C4 catalog and ownership state in memory.",
        )
        filters.add_layout(filter_layout)
        self.filter_section = filters

        self.model = BlueprintTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAccessibleName("Blueprint ownership results")
        self.table.setAccessibleDescription(
            "Virtualized local blueprint catalog joined to acquisition evidence."
        )
        for column, width in enumerate(self.DEFAULT_COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        results = SectionCard(
            "Blueprint catalog",
            "Ownership is personal local state; rebuilding game data never deletes it.",
        )
        results.add_widget(self.table, 1)
        self.results_section = results

        self.scan_button = QPushButton("Scan local game logs")
        self.scan_button.setProperty("role", "primary")
        self.scan_button.setAccessibleName("Scan local game logs for blueprint acquisitions")
        self.scan_button.setAccessibleDescription(
            "Incrementally scans the selected channel's local logs in a cancellable worker, "
            "then asks before saving ownership state."
        )
        self.scan_button.clicked.connect(lambda: self.scan_logs(full_rescan=False))
        self.full_scan_button = QPushButton("Full rescan…")
        self.full_scan_button.setAccessibleName("Fully rescan local game logs")
        self.full_scan_button.setAccessibleDescription(
            "Asks first, then rereads selected-channel logs from byte zero in a cancellable worker."
        )
        self.full_scan_button.clicked.connect(self.full_rescan)
        self.link_live_hotfix_toggle = QCheckBox(
            "Review LIVE and HOTFIX logs together"
        )
        self.link_live_hotfix_toggle.setChecked(self.link_live_hotfix)
        self.link_live_hotfix_toggle.setAccessibleName(
            "Use shared LIVE and HOTFIX blueprint ownership"
        )
        self.link_live_hotfix_toggle.setAccessibleDescription(
            "Explicitly combines only LIVE and HOTFIX acquisition logs in one shared ownership scope."
        )
        self.link_live_hotfix_toggle.setToolTip(
            "When enabled, a scan reviews current and rotated logs from both sibling production channels. "
            "PTU, EPTU, and TECH-PREVIEW always remain isolated."
        )
        self.link_live_hotfix_toggle.toggled.connect(self.set_link_live_hotfix)
        self.reload_button = QPushButton("Reload ownership")
        self.reload_button.setAccessibleName("Reload channel ownership")
        self.reload_button.setAccessibleDescription(
            "Read the channel-scoped ownership store in a background worker."
        )
        self.reload_button.clicked.connect(self.load_ownership)
        self.recover_button = QPushButton("Recover validated backup…")
        self.recover_button.setAccessibleName("Recover validated ownership backup")
        self.recover_button.setAccessibleDescription(
            "Explicitly replaces a damaged ownership store with its validated backup."
        )
        self.recover_button.clicked.connect(self.recover_ownership)
        self.recover_button.setVisible(False)
        self.mark_owned_button = QPushButton("Mark selected owned…")
        self.mark_owned_button.setAccessibleName("Mark selected blueprints owned")
        self.mark_owned_button.setAccessibleDescription(
            "Previews one revision-bound manual ownership command for every selected row."
        )
        self.mark_owned_button.clicked.connect(
            lambda: self.mark_selected(OwnershipDecision.OWNED)
        )
        self.mark_unowned_button = QPushButton("Mark selected unowned…")
        self.mark_unowned_button.setAccessibleName("Mark selected blueprints unowned")
        self.mark_unowned_button.setAccessibleDescription(
            "Previews removal of local ownership records for every selected row."
        )
        self.mark_unowned_button.clicked.connect(
            lambda: self.mark_selected(OwnershipDecision.UNOWNED)
        )
        self.resolve_button = QPushButton("Resolve exact match…")
        self.resolve_button.setAccessibleName("Resolve unmatched acquisition by exact catalog match")
        self.resolve_button.setAccessibleDescription(
            "Choose one unresolved acquisition and one exact catalog alias, then preview "
            "the revision-bound resolution before saving."
        )
        self.resolve_button.clicked.connect(self.resolve_unmatched)
        self.import_button = QPushButton("Import ownership…")
        self.import_button.setAccessibleName("Preview ownership import")
        self.import_button.setAccessibleDescription(
            "Validate a bounded JSON or CSV file in a background worker, preview exact "
            "matches, and save only after confirmation."
        )
        self.import_button.clicked.connect(self.preview_import)
        self.export_button = QPushButton("Export ownership…")
        self.export_button.setAccessibleName("Export channel ownership")
        self.export_button.setAccessibleDescription(
            "Confirm and write the current revision of channel-scoped ownership as JSON or CSV."
        )
        self.export_button.clicked.connect(self.export_ownership)

        actions = QGridLayout()
        actions.addWidget(self.scan_button, 0, 0)
        actions.addWidget(self.full_scan_button, 0, 1)
        actions.addWidget(self.reload_button, 0, 2)
        actions.addWidget(self.recover_button, 0, 3)
        actions.addWidget(self.link_live_hotfix_toggle, 0, 4)
        actions.addWidget(self.mark_owned_button, 1, 0)
        actions.addWidget(self.mark_unowned_button, 1, 1)
        actions.addWidget(self.resolve_button, 1, 2)
        actions.addWidget(self.import_button, 1, 3)
        actions.addWidget(self.export_button, 1, 4)

        self.empty = EmptyState(
            "No blueprint catalog yet",
            "Read contracts from the selected game channel. Blueprint identity comes from local C4 data.",
        )
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addLayout(metrics)
        layout.addWidget(filters)
        layout.addWidget(self.empty)
        layout.addWidget(results, 1)
        layout.addLayout(actions)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(140)
        self.search_timer.timeout.connect(self.refresh_query)
        self.scope_timer = QTimer(self)
        self.scope_timer.setSingleShot(True)
        self.scope_timer.timeout.connect(self.load_ownership)

        QWidget.setTabOrder(self.search, self.ownership_filter)
        focus_order = (
            self.search,
            self.ownership_filter,
            self.category_filter,
            self.type_filter,
            self.class_filter,
            self.size_filter,
            self.grade_filter,
            self.mission_filter,
            self.reward_filter,
            self.table,
            self.scan_button,
            self.full_scan_button,
            self.reload_button,
            self.mark_owned_button,
            self.mark_unowned_button,
            self.resolve_button,
            self.import_button,
            self.export_button,
            self.link_live_hotfix_toggle,
        )
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.contractsChanged.connect(self.rebuild_catalog)
        state.pathsChanged.connect(self.scope_changed)
        self.rebuild_catalog()
        self.scope_changed()

    def reset_layout(self) -> None:
        """Restore only machine-local blueprint table column defaults."""

        for column, width in enumerate(self.DEFAULT_COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)

    def _scope(self) -> tuple[str, object] | None:
        target = self.state.target
        if target is None:
            return None
        try:
            root = target.parents[3]
            channel = ownership_scope(root.name)
            return channel, root
        except (IndexError, OwnershipError):
            return None

    def _link_active(self, channel: str | None = None) -> bool:
        selected = channel or self.channel
        return bool(
            self.link_live_hotfix and selected in {"LIVE", "HOTFIX"}
        )

    def set_link_live_hotfix(self, enabled: bool, *, persist: bool = True) -> None:
        """Switch scopes without carrying state or a late worker result across."""

        enabled = bool(enabled)
        changed = enabled != self.link_live_hotfix
        self.link_live_hotfix = enabled
        self.link_live_hotfix_toggle.blockSignals(True)
        self.link_live_hotfix_toggle.setChecked(enabled)
        self.link_live_hotfix_toggle.blockSignals(False)
        if changed:
            self.scope_changed(force=True)
            if self.channel and self.isVisible() and not self._jobs:
                self.scope_timer.start(0)
            if persist:
                self.linkLiveHotfixChanged.emit(enabled)

    def rebuild_catalog(self) -> None:
        if self._shutting_down:
            return
        self.catalog = build_catalog(self.state.contracts) if self.state.contracts else None
        self._replace_filter_values()
        self.refresh_query()

    def _replace_filter_values(self) -> None:
        for combo, values in (
            (self.category_filter, categories(self.catalog) if self.catalog else ()),
            (self.reward_filter, reward_sources(self.catalog) if self.catalog else ()),
            (self.mission_filter, missions(self.catalog) if self.catalog else ()),
            (self.type_filter, item_types(self.catalog) if self.catalog else ()),
            (self.class_filter, item_classes(self.catalog) if self.catalog else ()),
            (self.size_filter, sizes(self.catalog) if self.catalog else ()),
            (self.grade_filter, grades(self.catalog) if self.catalog else ()),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("All", "")
            for value in values:
                combo.addItem(value.title(), value)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def scope_changed(self, *_args, force: bool = False) -> None:
        if self._shutting_down:
            return
        scope = self._scope()
        channel = scope[0] if scope else None
        linked = self._link_active(channel)
        scope_name = ownership_scope(channel, link_live_hotfix=linked) if channel else None
        self.link_live_hotfix_toggle.setEnabled(
            channel in {"LIVE", "HOTFIX"} and not self._jobs
        )
        if not force and scope_name == self.scope_name and self.ownership is not None:
            return
        self.channel = channel
        self.scope_name = scope_name
        self.ownership = None
        self.state.begin_ownership_scope(scope_name)
        self._continuity_scopes = ()
        self._recovery_target = None
        self.model.set_rows(())
        if channel:
            self.status.set_tone(Tone.INFO)
            label = "shared LIVE-HOTFIX" if linked else channel
            self.status.setText(
                f"{channel} selected with {label} ownership. Open this page or choose Reload ownership to read local state."
            )
        self.refresh_query()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            not self._shutting_down
            and self.channel
            and self.ownership is None
            and not self._jobs
        ):
            self.scope_timer.start(0)
        super().showEvent(event)

    def load_ownership(self) -> None:
        if self._shutting_down or not self.channel or self._jobs:
            return
        channel = self.channel
        linked = self._link_active(channel)
        label = "LIVE-HOTFIX" if linked else channel
        self.status.set_tone(Tone.INFO)
        self.status.setText(f"Loading {label} ownership in the background…")
        self._start_job(
            lambda token, _reporter: self._load_snapshot(token, channel, linked),
            self._ownership_loaded,
        )

    @staticmethod
    def _load_snapshot(
        token,
        channel: str,
        link_live_hotfix: bool,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        loaded = OwnershipStore(
            channel,
            link_live_hotfix=link_live_hotfix,
        ).load_details()
        token.checkpoint()
        return OwnershipSnapshot(
            channel,
            link_live_hotfix,
            loaded.state,
            loaded.continuity_scopes,
        )

    def _ownership_loaded(self, snapshot: OwnershipSnapshot) -> None:
        if (
            snapshot.channel != self.channel
            or snapshot.link_live_hotfix != self._link_active()
        ):
            return
        if not self.scope_name or not self.state.set_ownership(
            self.scope_name, snapshot.state
        ):
            return
        self.ownership = snapshot.state
        self._continuity_scopes = snapshot.continuity_scopes
        self._recovery_target = None
        self.recover_button.setVisible(False)
        if snapshot.continuity_scopes:
            self.status.set_tone(Tone.WARNING)
            self.status.setText(
                f"Loaded {len(snapshot.state.records):,} owned blueprint records, including "
                f"existing {' and '.join(snapshot.continuity_scopes)} evidence. Nothing was "
                "moved or deleted; a confirmed scan will consolidate a shared copy."
            )
        else:
            self.status.set_tone(Tone.SUCCESS)
            self.status.setText(
                f"Loaded {len(snapshot.state.records):,} owned blueprint records for {snapshot.state.scope}."
            )
        self.refresh_query()

    def refresh_query(self) -> None:
        if not self.catalog:
            self.model.set_rows(())
            self.empty.setVisible(True)
            self.results_section.setVisible(False)
            self._update_metrics()
            return
        ownership = self.ownership or OwnershipState(
            self.scope_name or self.channel or "LIVE"
        )
        rows = query_blueprints(
            self.catalog,
            ownership,
            BlueprintQuery(
                search=self.search.text(),
                ownership=OwnershipFilter(
                    self.ownership_filter.currentData() or OwnershipFilter.ALL
                ),
                category=self.category_filter.currentData() or "",
                reward_source=self.reward_filter.currentData() or "",
                mission=self.mission_filter.currentData() or "",
                item_type=self.type_filter.currentData() or "",
                item_class=self.class_filter.currentData() or "",
                size=self.size_filter.currentData() or "",
                grade=self.grade_filter.currentData() or "",
            ),
        )
        self.model.set_rows(rows)
        self.empty.setVisible(False)
        self.results_section.setVisible(True)
        self._update_metrics()

    def _update_metrics(self) -> None:
        catalog_count = len(self.catalog.entries) if self.catalog else 0
        owned = len(self.ownership.records) if self.ownership else 0
        unresolved = len(self.ownership.unresolved) if self.ownership else 0
        self.catalog_metric.set_value(f"{catalog_count:,}")
        self.owned_metric.set_value(f"{owned:,}")
        self.unresolved_metric.set_value(f"{unresolved:,}")
        self.visible_metric.set_value(f"{self.model.rowCount():,}")
        ready = bool(self.catalog and self.ownership is not None and self.channel and not self._jobs)
        selected = bool(self.table.selectionModel().selectedRows())
        self.scan_button.setEnabled(ready)
        self.full_scan_button.setEnabled(ready)
        self.reload_button.setEnabled(bool(self.channel and not self._jobs))
        self.mark_owned_button.setEnabled(ready and selected)
        self.mark_unowned_button.setEnabled(ready and selected)
        self.resolve_button.setEnabled(ready and unresolved > 0)
        self.import_button.setEnabled(ready)
        self.export_button.setEnabled(ready)
        self.recover_button.setEnabled(bool(self.channel and not self._jobs))
        self.link_live_hotfix_toggle.setEnabled(
            self.channel in {"LIVE", "HOTFIX"} and not self._jobs
        )

    def _selection_changed(self, *_args) -> None:
        self._update_metrics()

    def _selected_blueprint_ids(self) -> tuple[str, ...]:
        selected = []
        for index in self.table.selectionModel().selectedRows():
            row = self.model.data(index, BlueprintTableModel.RecordRole)
            if isinstance(row, BlueprintRow):
                selected.append(row.entry.blueprint_id)
        return tuple(sorted(set(selected)))

    def mark_selected(self, decision: OwnershipDecision) -> None:
        if not self.catalog or self.ownership is None or self._jobs:
            return
        selected = self._selected_blueprint_ids()
        if not selected:
            return
        try:
            plan = plan_manual_ownership(
                self.catalog, self.ownership, selected, decision
            )
        except OwnershipError as exc:
            self._job_failed(exc)
            return
        if not plan.changes:
            self.status.set_tone(Tone.SUCCESS)
            self.status.setText(
                f"No ownership write is needed; every selected blueprint is already {decision.value}."
            )
            return
        preview = "\n".join(
            f"• {change.name}: "
            f"{'owned' if change.before_owned else 'unowned'} → "
            f"{'owned' if change.after_owned else 'unowned'}"
            for change in plan.changes[:20]
        )
        if len(plan.changes) > 20:
            preview += f"\n• +{len(plan.changes) - 20:,} more"
        if QMessageBox.question(
            self,
            "Apply manual blueprint ownership?",
            f"Scope: {plan.scope}\nRevision: {plan.expected_revision}\n"
            f"Changes: {plan.summary()}\n\n{preview}\n\n"
            "This changes only StarCompanion's local ownership store and can be reversed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        channel = self.channel
        linked = self._link_active(channel)
        if channel is None:
            return
        self._start_job(
            lambda token, _reporter: self._apply_manual_plan(
                token, channel, linked, plan
            ),
            lambda snapshot: self._ownership_saved(
                snapshot, f"Applied {plan.summary()} as one reviewed command."
            ),
        )

    @staticmethod
    def _apply_manual_plan(
        token,
        channel: str,
        link_live_hotfix: bool,
        plan: ManualOwnershipPlan,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        store = OwnershipStore(channel, link_live_hotfix=link_live_hotfix)
        loaded = store.load_details()
        updated = apply_manual_ownership(plan, loaded.state)
        token.checkpoint()
        store.save(updated)
        token.checkpoint()
        refreshed = store.load_details()
        return OwnershipSnapshot(
            channel,
            link_live_hotfix,
            refreshed.state,
            refreshed.continuity_scopes,
        )

    def _ownership_saved(self, snapshot: OwnershipSnapshot, message: str) -> None:
        self._ownership_loaded(snapshot)
        if self.ownership is snapshot.state:
            self.status.set_tone(Tone.SUCCESS)
            self.status.setText(message)

    def full_rescan(self) -> None:
        if self.ownership is None or self._jobs:
            return
        if QMessageBox.question(
            self,
            "Reread every local log?",
            "A full rescan ignores saved byte cursors and rereads every discovered log in the "
            "selected ownership scope. Existing manual/import evidence is retained and duplicate "
            "log events remain deduplicated. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes:
            self.scan_logs(full_rescan=True)

    def scan_logs(self, *, full_rescan: bool = False) -> None:
        scope = self._scope()
        if not scope or not self.catalog or self.ownership is None or self._jobs:
            return
        channel, root = scope
        linked = self._link_active(channel)
        catalog = self.catalog
        expected_revision = self.ownership.revision
        continuity_scopes = self._continuity_scopes
        self.status.set_tone(Tone.INFO)
        label = "LIVE and HOTFIX" if linked else channel
        mode = "fully rescanning" if full_rescan else "scanning"
        self.status.setText(f"{mode.title()} {label} local logs in the background…")

        def operation(token, reporter):
            loaded = OwnershipStore(
                channel, link_live_hotfix=linked
            ).load_details()
            if loaded.state.revision != expected_revision:
                raise OwnershipConflictError(
                    "ownership changed before the scan began; reload and review it again"
                )
            discovery = discover_logs(
                root,
                link_live_hotfix=linked,
                cancel=lambda: token.is_cancelled,
            )
            try:
                return OwnershipScanSnapshot(
                    channel,
                    linked,
                    scan_logs(
                        discovery.paths,
                        catalog,
                        loaded.state,
                        full_rescan=full_rescan,
                        cancel=lambda: token.is_cancelled,
                        progress=lambda current, total, name: reporter((current, total, name)),
                        initial_diagnostics=discovery.diagnostics,
                    ),
                    continuity_scopes,
                )
            except ScanCancelled:
                token.checkpoint()
                raise

        self._start_job(operation, self._scan_preview, progress=self._scan_progress)

    def _scan_progress(self, value) -> None:
        current, total, name = value
        self.status.setText(
            f"Scanning local logs {current:,}/{total:,}" + (f": {name}" if name else "…")
        )

    def _scan_preview(self, snapshot: OwnershipScanSnapshot) -> None:
        if (
            snapshot.channel != self.channel
            or snapshot.link_live_hotfix != self._link_active()
        ):
            self.status.set_tone(Tone.INFO)
            self.status.setText(
                "Scan preview discarded because the selected ownership scope changed."
            )
            return
        result = snapshot.result
        diagnostic_text = self._diagnostic_summary(result.diagnostics)
        needs_write = bool(snapshot.continuity_scopes) or result.state != self.ownership
        if not needs_write:
            self.status.set_tone(
                Tone.WARNING if result.diagnostics else Tone.SUCCESS
            )
            self.status.setText(
                f"Scan complete: {result.files_seen:,} files, {result.bytes_read:,} bytes, "
                f"no new acquisitions.{diagnostic_text}"
            )
            return
        continuity_text = ""
        if snapshot.continuity_scopes:
            continuity_text = (
                "\nExisting separate ownership: "
                + " and ".join(snapshot.continuity_scopes)
                + " (copied into the shared scope; source stores remain unchanged)\n"
            )
        if QMessageBox.question(
            self,
            "Save scanned blueprint evidence?",
            f"Channel: {self.channel}\nFiles seen: {result.files_seen:,}\n"
            f"Bytes read: {result.bytes_read:,}\nOwned acquisitions: +{result.acquisitions_added:,}\n"
            f"Unresolved names: +{result.unresolved_added:,}\n"
            f"Resolved earlier names: {result.unresolved_reconciled:,}\n\n"
            f"Warnings: {len(result.diagnostics):,}{diagnostic_text}"
            f"{continuity_text}\n"
            "Only acquisition evidence and scan cursors are stored locally. Saving scan progress "
            "prevents unchanged logs from being read again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            self.status.set_tone(Tone.INFO)
            self.status.setText("Scan preview discarded. Ownership and scan cursors were unchanged.")
            return
        channel = snapshot.channel
        linked = snapshot.link_live_hotfix
        candidate = result.state
        self._start_job(
            lambda token, _reporter: self._save_state(
                token,
                channel,
                linked,
                candidate,
            ),
            lambda loaded: self._ownership_saved_after_scan(
                loaded, result.diagnostics
            ),
        )

    @staticmethod
    def _diagnostic_summary(diagnostics) -> str:
        if not diagnostics:
            return ""
        samples = "; ".join(
            f"{item.source_name}: {item.code}" for item in diagnostics[:3]
        )
        extra = len(diagnostics) - min(3, len(diagnostics))
        if extra:
            samples += f"; +{extra:,} more"
        return f" ({samples})."

    def _ownership_saved_after_scan(self, snapshot, diagnostics) -> None:
        self._ownership_loaded(snapshot)
        if diagnostics:
            self.status.set_tone(Tone.WARNING)
            self.status.setText(
                "Ownership and safe scan progress were saved with warnings"
                + self._diagnostic_summary(diagnostics)
            )

    @staticmethod
    def _save_state(
        token,
        channel: str,
        link_live_hotfix: bool,
        state: OwnershipState,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        OwnershipStore(
            channel,
            link_live_hotfix=link_live_hotfix,
        ).save(state)
        token.checkpoint()
        return OwnershipSnapshot(channel, link_live_hotfix, state)

    def preview_import(self) -> None:
        if not self.catalog or self.ownership is None or self._jobs:
            return
        filename, _selected = QFileDialog.getOpenFileName(
            self,
            "Import blueprint ownership",
            "",
            "Ownership files (*.json *.csv)",
        )
        if not filename:
            return
        channel = self.channel
        if channel is None:
            return
        linked = self._link_active(channel)
        catalog = self.catalog
        source = Path(filename)
        self.status.set_tone(Tone.INFO)
        self.status.setText("Validating the ownership import in a background worker…")
        self._start_job(
            lambda token, _reporter: self._plan_import(
                token, channel, linked, catalog, source
            ),
            lambda plan: self._import_preview_ready(
                channel, linked, catalog, plan
            ),
        )

    @staticmethod
    def _plan_import(
        token,
        channel: str,
        link_live_hotfix: bool,
        catalog: BlueprintCatalog,
        source: Path,
    ) -> ImportPlan:
        token.checkpoint()
        state = OwnershipStore(
            channel, link_live_hotfix=link_live_hotfix
        ).load_details().state
        token.checkpoint()
        result = plan_import(source, catalog, state)
        token.checkpoint()
        return result

    def _import_preview_ready(
        self,
        channel: str,
        link_live_hotfix: bool,
        catalog: BlueprintCatalog,
        plan: ImportPlan,
    ) -> None:
        if (
            channel != self.channel
            or link_live_hotfix != self._link_active()
            or catalog is not self.catalog
        ):
            self.status.set_tone(Tone.INFO)
            self.status.setText("Import preview discarded because the selected scope or catalog changed.")
            return
        sample = "\n".join(f"• {item.name}" for item in plan.candidates[:20])
        if len(plan.candidates) > 20:
            sample += f"\n• +{len(plan.candidates) - 20:,} more"
        summary = (
            f"Source: {plan.source_name}\nExact additions: {plan.additions:,}\n"
            f"Already owned: {len(plan.already_owned):,}\n"
            f"Unmatched (not imported): {len(plan.unmatched_names):,}"
        )
        if not plan.candidates:
            self.status.set_tone(
                Tone.WARNING if plan.unmatched_names else Tone.SUCCESS
            )
            self.status.setText(summary.replace("\n", " · ") + ". Nothing was written.")
            return
        if QMessageBox.question(
            self,
            "Import exact blueprint matches?",
            f"{summary}\n\n{sample}\n\n"
            "Only exact stable IDs or unambiguous normalized names are accepted. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            self.status.set_tone(Tone.INFO)
            self.status.setText("Import preview discarded. Nothing was written.")
            return
        self._start_job(
            lambda token, _reporter: self._apply_import_plan(
                token, channel, link_live_hotfix, plan
            ),
            lambda snapshot: self._ownership_saved(
                snapshot, f"Imported {plan.additions:,} exact blueprint match(es)."
            ),
        )

    @staticmethod
    def _apply_import_plan(
        token,
        channel: str,
        link_live_hotfix: bool,
        plan: ImportPlan,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        store = OwnershipStore(channel, link_live_hotfix=link_live_hotfix)
        loaded = store.load_details()
        updated = apply_import(plan, loaded.state)
        token.checkpoint()
        store.save(updated)
        token.checkpoint()
        refreshed = store.load_details()
        return OwnershipSnapshot(
            channel,
            link_live_hotfix,
            refreshed.state,
            refreshed.continuity_scopes,
        )

    def export_ownership(self) -> None:
        if not self.catalog or self.ownership is None or self._jobs:
            return
        filename, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export blueprint ownership",
            "starcompanion-ownership.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not filename:
            return
        destination = Path(filename)
        if destination.suffix.casefold() not in {".json", ".csv"}:
            destination = destination.with_suffix(
                ".csv" if selected_filter.startswith("CSV") else ".json"
            )
        if QMessageBox.question(
            self,
            "Export local ownership?",
            f"Export {len(self.ownership.records):,} owned blueprint record(s) as "
            f"{destination.suffix[1:].upper()}? If the selected file exists, it will be replaced.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        channel = self.channel
        if channel is None:
            return
        linked = self._link_active(channel)
        catalog = self.catalog
        expected_revision = self.ownership.revision
        self.status.set_tone(Tone.INFO)
        self.status.setText("Writing the ownership export in a background worker…")
        self._start_job(
            lambda token, _reporter: self._write_export(
                token,
                channel,
                linked,
                catalog,
                expected_revision,
                destination,
            ),
            self._export_written,
        )

    @staticmethod
    def _write_export(
        token,
        channel: str,
        link_live_hotfix: bool,
        catalog: BlueprintCatalog,
        expected_revision: int,
        destination: Path,
    ) -> tuple[Path, int]:
        token.checkpoint()
        store = OwnershipStore(channel, link_live_hotfix=link_live_hotfix)
        state = store.load_details().state
        if state.revision != expected_revision:
            raise OwnershipConflictError(
                "ownership changed after export confirmation; reload and try again"
            )
        payload = (
            export_json(state, catalog)
            if destination.suffix.casefold() == ".json"
            else export_csv(state, catalog)
        )
        token.checkpoint()
        write_export(destination, payload, store_path=store.path)
        token.checkpoint()
        return destination, len(state.records)

    def _export_written(self, result: tuple[Path, int]) -> None:
        destination, count = result
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Exported {count:,} owned blueprint record(s) to {destination}."
        )

    def resolve_unmatched(self) -> None:
        if not self.catalog or self.ownership is None or self._jobs:
            return
        resolvable = [
            item
            for item in self.ownership.unresolved
            if self.catalog.resolve_name_candidates(item.name)
        ]
        if not resolvable:
            self.status.set_tone(Tone.WARNING)
            self.status.setText(
                "No unresolved acquisition currently has an exact catalog-name candidate. "
                "StarCompanion will not guess or substitute a fuzzy match."
            )
            return
        acquisition_labels = [
            f"{item.acquisition.acquisition_id[:16]} — {item.name} ({item.reason})"
            for item in resolvable
        ]
        selected_label, accepted = QInputDialog.getItem(
            self,
            "Choose unresolved acquisition",
            "Logged acquisition:",
            acquisition_labels,
            0,
            False,
        )
        if not accepted:
            return
        unresolved = resolvable[acquisition_labels.index(selected_label)]
        candidate_ids = self.catalog.resolve_name_candidates(unresolved.name)
        candidate_labels = [
            f"{self.catalog.by_id[item].name} — {item}" for item in candidate_ids
        ]
        candidate_label, accepted = QInputDialog.getItem(
            self,
            "Choose exact blueprint identity",
            "Exact catalog candidate:",
            candidate_labels,
            0,
            False,
        )
        if not accepted:
            return
        blueprint_id = candidate_ids[candidate_labels.index(candidate_label)]
        try:
            plan = plan_resolution(
                self.ownership,
                self.catalog,
                unresolved.acquisition.acquisition_id,
                blueprint_id,
            )
        except OwnershipError as exc:
            self._job_failed(exc)
            return
        if QMessageBox.question(
            self,
            "Apply exact acquisition resolution?",
            f"Logged name: {plan.unresolved.name}\n"
            f"Exact blueprint: {plan.blueprint_name}\nID: {plan.blueprint_id}\n\n"
            "This moves only that acquisition from unresolved evidence to the selected stable ID.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        channel = self.channel
        if channel is None:
            return
        linked = self._link_active(channel)
        self._start_job(
            lambda token, _reporter: self._apply_resolution_plan(
                token, channel, linked, plan
            ),
            lambda snapshot: self._ownership_saved(
                snapshot, f"Resolved {plan.unresolved.name} to {plan.blueprint_name}."
            ),
        )

    @staticmethod
    def _apply_resolution_plan(
        token,
        channel: str,
        link_live_hotfix: bool,
        plan: ResolutionPlan,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        store = OwnershipStore(channel, link_live_hotfix=link_live_hotfix)
        loaded = store.load_details()
        updated = apply_resolution(plan, loaded.state)
        token.checkpoint()
        store.save(updated)
        token.checkpoint()
        refreshed = store.load_details()
        return OwnershipSnapshot(
            channel,
            link_live_hotfix,
            refreshed.state,
            refreshed.continuity_scopes,
        )

    def recover_ownership(self) -> None:
        if not self.channel or self._jobs:
            return
        recovery_label = (
            ownership_scope(
                self._recovery_target[0],
                link_live_hotfix=self._recovery_target[1],
            )
            if self._recovery_target
            else self.scope_name or self.channel
        )
        if QMessageBox.question(
            self,
            "Recover ownership backup?",
            f"Replace the damaged {recovery_label} ownership store with its validated last-known-good backup?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        channel = self.channel
        linked = self._link_active(channel)
        recovery_channel, recovery_linked = self._recovery_target or (
            channel,
            linked,
        )
        self._start_job(
            lambda token, _reporter: self._recover(
                token,
                channel,
                linked,
                recovery_channel,
                recovery_linked,
            ),
            self._ownership_loaded,
        )

    @staticmethod
    def _recover(
        token,
        channel: str,
        link_live_hotfix: bool,
        recovery_channel: str,
        recovery_link_live_hotfix: bool,
    ) -> OwnershipSnapshot:
        token.checkpoint()
        OwnershipStore(
            recovery_channel,
            link_live_hotfix=recovery_link_live_hotfix,
        ).recover()
        token.checkpoint()
        loaded = OwnershipStore(
            channel, link_live_hotfix=link_live_hotfix
        ).load_details()
        return OwnershipSnapshot(
            channel,
            link_live_hotfix,
            loaded.state,
            loaded.continuity_scopes,
        )

    def _start_job(self, operation, success, *, progress=None) -> None:
        if self._shutting_down:
            return
        job = QtOperationJob(operation, self)
        self._jobs.add(job)
        if progress is not None:
            job.progress.connect(progress)
        job.succeeded.connect(success)
        job.failed.connect(self._job_failed)
        job.finished.connect(lambda: self._job_finished(job))
        job.start()
        self._update_metrics()

    def _job_failed(self, exc: Exception) -> None:
        self.status.set_tone(Tone.DANGER)
        self.status.setText(f"Ownership operation stopped safely: {exc}")
        self._recovery_target = (
            (exc.channel, exc.link_live_hotfix)
            if isinstance(exc, OwnershipRecoveryAvailable) and exc.channel
            else None
        )
        self.recover_button.setVisible(isinstance(exc, OwnershipRecoveryAvailable))

    def _job_finished(self, job: QtOperationJob) -> None:
        self._jobs.discard(job)
        job.deleteLater()
        if not self._shutting_down:
            self._update_metrics()

    def shutdown_jobs(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.search_timer.stop()
        self.scope_timer.stop()
        for job in tuple(self._jobs):
            job.shutdown(5000)
        self._jobs.clear()


__all__ = [
    "BlueprintTableModel",
    "BlueprintTrackerTab",
    "OwnershipScanSnapshot",
    "OwnershipSnapshot",
]
