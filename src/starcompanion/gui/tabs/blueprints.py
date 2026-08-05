"""G2 blueprint ownership tracker over the C4 catalog and query APIs."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
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
    query_blueprints,
    reward_sources,
)
from ...ownership import (
    OwnershipRecoveryAvailable,
    OwnershipState,
    OwnershipStore,
    ScanCancelled,
    ScanResult,
    discover_log_files,
    scan_logs,
)
from ..components import EmptyState, MetricTile, NoticeBanner, SectionCard, Tone
from ..jobs import QtOperationJob
from ..state import AppState


@dataclass(frozen=True)
class OwnershipSnapshot:
    channel: str
    state: OwnershipState


class BlueprintTableModel(QAbstractTableModel):
    """Virtual read-only projection returned by the C4 query service."""

    HEADERS = ("Blueprint", "Category", "Owned", "Acquired", "Evidence", "Reward sources")

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
        values = (
            row.entry.name,
            row.entry.category.title(),
            "Owned" if row.owned else "Not owned",
            row.acquired_at or "—",
            sources or "—",
            rewards or "—",
        )
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{row.entry.blueprint_id}\n"
                f"{len(row.entry.reward_sources):,} reward source(s) / "
                f"{len(row.entry.evidence):,} evidence link(s)"
            )
        return None


class BlueprintTrackerTab(QWidget):
    """Channel-isolated ownership search and incremental log-scan UI."""

    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.catalog: BlueprintCatalog | None = None
        self.ownership: OwnershipState | None = None
        self.channel: str | None = None
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
        for combo in (self.category_filter, self.reward_filter):
            combo.addItem("All", "")
            combo.currentIndexChanged.connect(self.refresh_query)
            combo.setAccessibleDescription("Filter the local C4 catalog without reading files.")
        self.ownership_filter.currentIndexChanged.connect(self.refresh_query)

        filter_layout = QGridLayout()
        filter_layout.addWidget(self.search, 0, 0, 1, 3)
        filter_layout.addWidget(self.ownership_filter, 1, 0)
        filter_layout.addWidget(self.category_filter, 1, 1)
        filter_layout.addWidget(self.reward_filter, 1, 2)
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
        self.table.setAccessibleName("Blueprint ownership results")
        self.table.setAccessibleDescription(
            "Virtualized local blueprint catalog joined to acquisition evidence."
        )
        self.table.setColumnWidth(0, 250)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 170)
        self.table.setColumnWidth(4, 110)
        self.table.horizontalHeader().setStretchLastSection(True)
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
        self.scan_button.clicked.connect(self.scan_logs)
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
        actions = QHBoxLayout()
        actions.addWidget(self.scan_button)
        actions.addWidget(self.reload_button)
        actions.addWidget(self.recover_button)
        actions.addStretch(1)

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
        QWidget.setTabOrder(self.ownership_filter, self.category_filter)
        QWidget.setTabOrder(self.category_filter, self.reward_filter)
        QWidget.setTabOrder(self.reward_filter, self.table)
        QWidget.setTabOrder(self.table, self.scan_button)
        QWidget.setTabOrder(self.scan_button, self.reload_button)

        state.contractsChanged.connect(self.rebuild_catalog)
        state.pathsChanged.connect(self.scope_changed)
        self.rebuild_catalog()
        self.scope_changed()

    def _scope(self) -> tuple[str, object] | None:
        target = self.state.target
        if target is None:
            return None
        try:
            root = target.parents[3]
            return root.name.upper(), root
        except IndexError:
            return None

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

    def scope_changed(self, *_args) -> None:
        if self._shutting_down:
            return
        scope = self._scope()
        channel = scope[0] if scope else None
        if channel == self.channel and self.ownership is not None:
            return
        self.channel = channel
        self.ownership = None
        self.model.set_rows(())
        if channel:
            self.status.set_tone(Tone.INFO)
            self.status.setText(
                f"{channel} selected. Open this page or choose Reload ownership to read local state."
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
        self.status.set_tone(Tone.INFO)
        self.status.setText(f"Loading {channel} ownership in the background…")
        self._start_job(
            lambda token, _reporter: self._load_snapshot(token, channel),
            self._ownership_loaded,
        )

    @staticmethod
    def _load_snapshot(token, channel: str) -> OwnershipSnapshot:
        token.checkpoint()
        state = OwnershipStore(channel).load()
        token.checkpoint()
        return OwnershipSnapshot(channel, state)

    def _ownership_loaded(self, snapshot: OwnershipSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self.ownership = snapshot.state
        self.recover_button.setVisible(False)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Loaded {len(snapshot.state.records):,} owned blueprint records for {snapshot.channel}."
        )
        self.refresh_query()

    def refresh_query(self) -> None:
        if not self.catalog:
            self.model.set_rows(())
            self.empty.setVisible(True)
            self.results_section.setVisible(False)
            self._update_metrics()
            return
        ownership = self.ownership or OwnershipState(self.channel or "LIVE")
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
        self.scan_button.setEnabled(ready)
        self.reload_button.setEnabled(bool(self.channel and not self._jobs))

    def scan_logs(self) -> None:
        scope = self._scope()
        if not scope or not self.catalog or self.ownership is None or self._jobs:
            return
        channel, root = scope
        catalog = self.catalog
        baseline = self.ownership
        self.status.set_tone(Tone.INFO)
        self.status.setText(f"Scanning {channel} local logs in the background…")

        def operation(token, reporter):
            paths = discover_log_files(root)
            try:
                return scan_logs(
                    paths,
                    catalog,
                    baseline,
                    cancel=lambda: token.is_cancelled,
                    progress=lambda current, total, name: reporter((current, total, name)),
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

    def _scan_preview(self, result: ScanResult) -> None:
        if result.acquisitions_added == 0 and result.unresolved_added == 0:
            self.status.set_tone(Tone.SUCCESS)
            self.status.setText(
                f"Scan complete: {result.files_seen:,} files, {result.bytes_read:,} bytes, no new acquisitions."
            )
            return
        if QMessageBox.question(
            self,
            "Save scanned blueprint evidence?",
            f"Channel: {self.channel}\nFiles seen: {result.files_seen:,}\n"
            f"Bytes read: {result.bytes_read:,}\nOwned acquisitions: +{result.acquisitions_added:,}\n"
            f"Unresolved names: +{result.unresolved_added:,}\n\n"
            "Only acquisition evidence and scan cursors are stored locally.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            self.status.set_tone(Tone.INFO)
            self.status.setText("Scan preview discarded. Ownership and scan cursors were unchanged.")
            return
        channel = self.channel
        candidate = result.state
        self._start_job(
            lambda token, _reporter: self._save_state(token, channel, candidate),
            self._ownership_loaded,
        )

    @staticmethod
    def _save_state(token, channel: str, state: OwnershipState) -> OwnershipSnapshot:
        token.checkpoint()
        OwnershipStore(channel).save(state)
        token.checkpoint()
        return OwnershipSnapshot(channel, state)

    def recover_ownership(self) -> None:
        if not self.channel or self._jobs:
            return
        if QMessageBox.question(
            self,
            "Recover ownership backup?",
            f"Replace the damaged {self.channel} ownership store with its validated last-known-good backup?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        channel = self.channel
        self._start_job(
            lambda token, _reporter: self._recover(token, channel),
            self._ownership_loaded,
        )

    @staticmethod
    def _recover(token, channel: str) -> OwnershipSnapshot:
        token.checkpoint()
        state = OwnershipStore(channel).recover()
        token.checkpoint()
        return OwnershipSnapshot(channel, state)

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


__all__ = ["BlueprintTableModel", "BlueprintTrackerTab", "OwnershipSnapshot"]
