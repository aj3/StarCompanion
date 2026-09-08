"""G2 blueprint ownership tracker over the C4 catalog and query APIs."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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
    OwnershipError,
    OwnershipRecoveryAvailable,
    OwnershipState,
    OwnershipStore,
    ScanCancelled,
    ScanResult,
    discover_logs,
    ownership_scope,
    scan_logs,
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

    linkLiveHotfixChanged = Signal(bool)

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
        actions = QHBoxLayout()
        actions.addWidget(self.scan_button)
        actions.addWidget(self.reload_button)
        actions.addWidget(self.recover_button)
        actions.addStretch(1)
        actions.addWidget(self.link_live_hotfix_toggle)

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
        QWidget.setTabOrder(self.reload_button, self.link_live_hotfix_toggle)

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
        self.link_live_hotfix_toggle.setEnabled(
            self.channel in {"LIVE", "HOTFIX"} and not self._jobs
        )

    def scan_logs(self) -> None:
        scope = self._scope()
        if not scope or not self.catalog or self.ownership is None or self._jobs:
            return
        channel, root = scope
        linked = self._link_active(channel)
        catalog = self.catalog
        baseline = self.ownership
        continuity_scopes = self._continuity_scopes
        self.status.set_tone(Tone.INFO)
        label = "LIVE and HOTFIX" if linked else channel
        self.status.setText(f"Scanning {label} local logs in the background…")

        def operation(token, reporter):
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
                        baseline,
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
