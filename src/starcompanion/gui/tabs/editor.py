"""G1 virtualized advanced localization editor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...install import normalize_channel, normalize_language
from ...user_edits import EditCommand, EditSession, UserEditError, UserEditStore
from ..components import EmptyState, MetricTile, NoticeBanner, SectionCard, Tone
from ..jobs import QtOperationJob
from ..state import AppState
from ..string_editor import (
    StringEditorDocument,
    StringFilterProxyModel,
    StringRecord,
    StringTableModel,
)


@dataclass(frozen=True)
class PersistentEditorSnapshot:
    channel: str
    language: str
    path: Path
    values: dict[str, str]
    history_recovered: bool
    undo_count: int
    redo_count: int


class AdvancedStringEditorTab(QWidget):
    """Thousands-of-rows model/view editor with explicit background persistence."""

    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.document = StringEditorDocument()
        self.model = StringTableModel(self.document, self)
        self.proxy = StringFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self._jobs: set[QtOperationJob] = set()
        self._scope_key: tuple[str, str] | None = None
        self._loading_detail = False
        self._shutting_down = False
        self.setAccessibleName("Advanced string editor")
        self.setAccessibleDescription(
            "Virtualized stock, rendered, and merged localization editor with source "
            "provenance, operation-plan outcomes, validation, and model-level history."
        )

        self.status = NoticeBanner(
            "Load local contracts to build the in-memory source graph.", tone=Tone.INFO
        )

        self.total_metric = MetricTile("Strings")
        self.visible_metric = MetricTile("Visible")
        self.modified_metric = MetricTile("Modified")
        self.issue_metric = MetricTile("Validation")
        metrics = QGridLayout()
        metrics.setSpacing(8)
        for column, metric in enumerate(
            (self.total_metric, self.visible_metric, self.modified_metric, self.issue_metric)
        ):
            metrics.addWidget(metric, 0, column)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search keys, values, mission givers, sources, or providers")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search localization strings")
        self.search.setAccessibleDescription(
            "Debounced case-insensitive search across key, values, mission metadata, source, and provider."
        )
        self.search.textChanged.connect(self._queue_search)

        self.state_filter = self._filter_combo(
            "String state filter",
            (
                ("All states", "all"),
                ("Modified", "modified"),
                ("Conflicts", "conflict"),
                ("Missing source", "missing"),
                ("Validation errors", "invalid"),
                ("Any validation issue", "warning"),
            ),
        )
        self.source_filter = self._filter_combo(
            "Winning source filter", (("All sources", "all"),)
        )
        self.category_filter = self._filter_combo(
            "String category filter", (("All categories", "all"),)
        )
        self.provider_filter = self._filter_combo(
            "Evidence provider filter", (("All providers", "all"),)
        )
        self.state_filter.currentIndexChanged.connect(
            lambda: self._apply_combo_filter("state")
        )
        self.source_filter.currentIndexChanged.connect(
            lambda: self._apply_combo_filter("source")
        )
        self.category_filter.currentIndexChanged.connect(
            lambda: self._apply_combo_filter("category")
        )
        self.provider_filter.currentIndexChanged.connect(
            lambda: self._apply_combo_filter("provider")
        )

        filters = QGridLayout()
        filters.setSpacing(8)
        filters.addWidget(self.search, 0, 0, 1, 4)
        filters.addWidget(self.state_filter, 1, 0)
        filters.addWidget(self.source_filter, 1, 1)
        filters.addWidget(self.category_filter, 1, 2)
        filters.addWidget(self.provider_filter, 1, 3)
        self.filter_section = SectionCard(
            "Find and filter",
            "Filters operate on the in-memory model and never reopen the archive or game files.",
        )
        self.filter_section.add_layout(filters)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.setAccessibleName("Virtualized localization string table")
        self.table.setAccessibleDescription(
            "Stock, rendered, merged, winning-source, and operation-plan values. "
            "Select multiple rows for one safe reset command."
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(StringTableModel.KEY, 240)
        self.table.setColumnWidth(StringTableModel.CATEGORY, 90)
        self.table.setColumnWidth(StringTableModel.STOCK, 220)
        self.table.setColumnWidth(StringTableModel.RENDERED, 220)
        self.table.setColumnWidth(StringTableModel.MERGED, 240)
        self.table.setColumnWidth(StringTableModel.SOURCE, 170)
        self.table.setColumnWidth(StringTableModel.OUTCOME, 100)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

        table_section = SectionCard(
            "Localization strings",
            "Only visible cells are requested from the table model; complete values remain in the detail inspector.",
        )
        table_section.add_widget(self.table, 1)
        self.table_section = table_section

        self.detail_key = QLabel("No string selected")
        self.detail_key.setProperty("role", "section-title")
        self.detail_key.setWordWrap(True)
        self.detail_meta = QLabel()
        self.detail_meta.setProperty("role", "muted")
        self.detail_meta.setWordWrap(True)

        self.stock_view = self._text_view("Stock localization value", read_only=True)
        self.rendered_view = self._text_view("Profile-rendered value", read_only=True)
        self.merged_editor = self._text_view("Merged user-edit value", read_only=False)
        self.merged_editor.setAccessibleDescription(
            "Edit the final merged value. Changes stay in memory until Save user edits is chosen."
        )
        self.merged_editor.textChanged.connect(self._queue_value_edit)
        self.provenance_view = self._text_view("Source and evidence provenance", read_only=True)
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setAccessibleName("String value and provenance views")
        self.inspector_tabs.setAccessibleDescription(
            "Switch between the editable merged value, stock localization, "
            "profile-rendered value, and complete source provenance."
        )
        self.inspector_tabs.addTab(self.merged_editor, "Merged edit")
        self.inspector_tabs.addTab(self.stock_view, "Stock")
        self.inspector_tabs.addTab(self.rendered_view, "Rendered")
        self.inspector_tabs.addTab(self.provenance_view, "Provenance")
        self.validation = NoticeBanner("Select one string to inspect it.", tone=Tone.INFO)

        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.reset_button = QPushButton("Reset selected to source…")
        self.save_button = QPushButton("Save user edits")
        self.reload_button = QPushButton("Reload saved edits")
        self.save_button.setProperty("role", "primary")
        for button, name, description in (
            (self.undo_button, "Undo editor command", "Undo the previous in-memory edit command."),
            (self.redo_button, "Redo editor command", "Redo the next in-memory edit command."),
            (
                self.reset_button,
                "Reset selected strings to source",
                "Remove user overrides for selected rows as one undoable in-memory command.",
            ),
            (
                self.save_button,
                "Save user edits",
                "Persist one reviewed aggregate C3 command in a background worker.",
            ),
            (
                self.reload_button,
                "Reload saved user edits",
                "Read the channel-scoped user.ini and history in a background worker.",
            ),
        ):
            button.setAccessibleName(name)
            button.setAccessibleDescription(description)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.reset_button.clicked.connect(self.reset_selected)
        self.save_button.clicked.connect(self.save_user_edits)
        self.reload_button.clicked.connect(lambda: self.load_user_edits(explicit=True))

        history_actions = QHBoxLayout()
        history_actions.addWidget(self.undo_button)
        history_actions.addWidget(self.redo_button)
        history_actions.addWidget(self.reset_button)
        history_actions.addStretch(1)
        history_actions.addWidget(self.reload_button)
        history_actions.addWidget(self.save_button)

        detail = SectionCard(
            "String inspector",
            "Stock and rendered values are read-only. The merged editor creates a user-layer command.",
        )
        detail.add_widget(self.detail_key)
        detail.add_widget(self.detail_meta)
        detail.add_widget(self.inspector_tabs, 1)
        detail.add_widget(self.validation)
        self.detail_section = detail

        self.empty = EmptyState(
            "No localization strings",
            "Read local contracts on Overview. This editor never starts archive extraction itself.",
        )

        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.addWidget(table_section)
        self.workspace.addWidget(detail)
        self.workspace.setSizes([680, 360])

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addLayout(metrics)
        layout.addWidget(self.filter_section)
        layout.addWidget(self.empty)
        layout.addWidget(self.workspace, 1)
        layout.addLayout(history_actions)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(self._apply_search)
        self.edit_timer = QTimer(self)
        self.edit_timer.setSingleShot(True)
        self.edit_timer.setInterval(250)
        self.edit_timer.timeout.connect(self._apply_value_edit)
        self.rebuild_timer = QTimer(self)
        self.rebuild_timer.setSingleShot(True)
        self.rebuild_timer.setInterval(80)
        self.rebuild_timer.timeout.connect(self.rebuild)
        self.scope_timer = QTimer(self)
        self.scope_timer.setSingleShot(True)
        self.scope_timer.setInterval(0)
        self.scope_timer.timeout.connect(self.load_user_edits)

        QShortcut(QKeySequence.StandardKey.Undo, self).activated.connect(self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self).activated.connect(self.redo)
        focus_order = [
            self.search,
            self.state_filter,
            self.source_filter,
            self.category_filter,
            self.provider_filter,
            self.table,
            self.merged_editor,
            self.provenance_view,
            self.undo_button,
            self.redo_button,
            self.reset_button,
            self.reload_button,
            self.save_button,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.contractsChanged.connect(self._schedule_rebuild)
        state.profileChanged.connect(self._schedule_rebuild)
        state.pathsChanged.connect(self._scope_changed)
        self._scope_changed()
        self.rebuild()

    # --- model and filters ----------------------------------------------

    @staticmethod
    def _filter_combo(name: str, values) -> QComboBox:
        combo = QComboBox()
        combo.setAccessibleName(name)
        combo.setAccessibleDescription(f"Filter the virtualized table by {name.lower()}.")
        for label, value in values:
            combo.addItem(label, value)
        return combo

    @staticmethod
    def _text_view(name: str, *, read_only: bool) -> QPlainTextEdit:
        widget = QPlainTextEdit()
        widget.setReadOnly(read_only)
        widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        widget.setAccessibleName(name)
        widget.setAccessibleDescription(name)
        widget.setMinimumHeight(58)
        return widget

    def _schedule_rebuild(self, *_args) -> None:
        if self._shutting_down:
            return
        self.rebuild_timer.start()

    def rebuild(self) -> None:
        if self._shutting_down:
            return
        # An explicit rebuild supersedes any coalesced state-change request;
        # leaving the timer armed could clear a user's fresh selection while
        # its debounced value edit is pending.
        self.rebuild_timer.stop()
        selected = self._selected_keys()
        if self.state.contracts is None:
            self.model.beginResetModel()
            self.model.snapshot = None
            self.model.endResetModel()
            self._after_model_change()
            return
        try:
            rendered = self.state.render()
            self.model.set_inputs(
                self.state.contracts,
                rendered,
                self.state.profile.name,
            )
        except Exception as exc:
            self.status.set_tone(Tone.DANGER)
            self.status.setText(f"The in-memory editor could not be rebuilt: {exc}")
            return
        self._refresh_filter_choices()
        self._after_model_change()
        self._restore_selection(selected)

    def _refresh_filter_choices(self) -> None:
        snapshot = self.model.snapshot
        if snapshot is None:
            return
        self._replace_dynamic_choices(
            self.source_filter,
            "All sources",
            snapshot.source_names,
        )
        self._replace_dynamic_choices(
            self.category_filter,
            "All categories",
            snapshot.category_names,
        )
        self._replace_dynamic_choices(
            self.provider_filter,
            "All providers",
            snapshot.provider_names,
        )

    @staticmethod
    def _replace_dynamic_choices(combo: QComboBox, all_label: str, values) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "all")
        for value in values:
            combo.addItem(value.replace("-", " ").title(), value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _queue_search(self, _text: str) -> None:
        if self._shutting_down:
            return
        self.search_timer.start()

    def _apply_search(self) -> None:
        self.proxy.set_query(self.search.text())
        self._update_metrics()

    def _apply_combo_filter(self, kind: str) -> None:
        combo = getattr(self, f"{kind}_filter")
        getattr(self.proxy, f"set_{kind}_filter")(combo.currentData() or "all")
        self._update_metrics()

    def _after_model_change(self) -> None:
        ready = self.model.snapshot is not None
        self.empty.setVisible(not ready)
        self.workspace.setVisible(ready)
        self.filter_section.setEnabled(ready)
        if ready:
            plan = self.model.snapshot.plan
            self.status.set_tone(Tone.DANGER if plan.errors else Tone.SUCCESS)
            self.status.setText(
                f"In-memory C3 plan: {plan.summary()}. Filtering and editing perform no filesystem I/O."
            )
        self._update_metrics()
        self._update_actions()

    def _update_metrics(self) -> None:
        snapshot = self.model.snapshot
        if snapshot is None:
            for metric in (
                self.total_metric,
                self.visible_metric,
                self.modified_metric,
                self.issue_metric,
            ):
                metric.set_value("—")
            return
        self.total_metric.set_value(f"{len(snapshot.records):,}")
        self.visible_metric.set_value(f"{self.proxy.rowCount():,}")
        self.modified_metric.set_value(
            f"{snapshot.modified_count:,}", f"{snapshot.conflict_count:,} source conflicts"
        )
        self.issue_metric.set_value(
            "Blocked" if snapshot.invalid_count else "Valid",
            f"{snapshot.invalid_count:,} errors",
        )

    # --- selection and editing -----------------------------------------

    def _selected_records(self) -> list[StringRecord]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        records = []
        for row in rows:
            source = self.proxy.mapToSource(self.proxy.index(row, 0))
            record = self.model.record(source.row())
            if record is not None:
                records.append(record)
        return records

    def _selected_keys(self) -> tuple[str, ...]:
        return tuple(record.key for record in self._selected_records())

    def _selection_changed(
        self,
        _selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        self.edit_timer.stop()
        records = self._selected_records()
        self._loading_detail = True
        try:
            if len(records) != 1:
                self.detail_key.setText(
                    f"{len(records):,} strings selected" if records else "No string selected"
                )
                self.detail_meta.setText(
                    "Use Reset selected to source for one undoable multi-row command."
                    if records
                    else ""
                )
                for widget in (
                    self.stock_view,
                    self.rendered_view,
                    self.merged_editor,
                    self.provenance_view,
                ):
                    widget.clear()
                self.merged_editor.setEnabled(False)
                self.validation.set_tone(Tone.INFO)
                self.validation.setText("Select exactly one string to edit its merged value.")
            else:
                self._show_record(records[0])
        finally:
            self._loading_detail = False
        self._update_actions()

    def _show_record(self, record: StringRecord) -> None:
        self.detail_key.setText(record.key)
        providers = ", ".join(record.providers) or "no provider evidence"
        self.detail_meta.setText(
            f"{record.organization} / {record.family} / {record.category} / "
            f"plan {record.operation} / {providers}"
        )
        self.stock_view.setPlainText(record.stock or "")
        self.rendered_view.setPlainText(record.rendered or "")
        self.merged_editor.setEnabled(True)
        self.merged_editor.setPlainText(record.merged)
        lines = [
            f"Winner: {record.winner.source_id} ({record.winner.kind.value})",
            "",
            "Contributions:",
            *(
                f"• {item.source_id} ({item.kind.value})"
                + (f" — {', '.join(item.provenance)}" if item.provenance else "")
                for item in record.contributions
            ),
        ]
        if record.evidence:
            lines.extend(("", "Provider evidence:"))
            lines.extend(
                f"• {item.provider} / {item.record_id} / {item.record_path} / {item.field_path}"
                for item in record.evidence
            )
        self.provenance_view.setPlainText("\n".join(lines))
        if record.invalid:
            self.validation.set_tone(Tone.DANGER)
        elif record.issues:
            self.validation.set_tone(Tone.WARNING)
        else:
            self.validation.set_tone(Tone.SUCCESS)
        self.validation.setText(
            " / ".join(str(issue) for issue in record.issues)
            if record.issues
            else "Merged value passes operation-plan validation."
        )

    def _queue_value_edit(self) -> None:
        if (
            not self._shutting_down
            and not self._loading_detail
            and self.merged_editor.isEnabled()
        ):
            self.edit_timer.start()

    def _apply_value_edit(self) -> None:
        records = self._selected_records()
        if len(records) != 1:
            return
        key = records[0].key
        value = self.merged_editor.toPlainText()
        changed = self.document.set_value(key, value)
        if changed:
            self.model.rebuild()
            self._after_model_change()
            self._restore_selection((key,))

    def undo(self) -> None:
        keys = self.document.undo()
        if keys:
            self.model.rebuild()
            self._after_model_change()
            self._restore_selection(keys[:1])
            if not self.document.dirty:
                self._scope_changed()

    def redo(self) -> None:
        keys = self.document.redo()
        if keys:
            self.model.rebuild()
            self._after_model_change()
            self._restore_selection(keys[:1])

    def reset_selected(self) -> None:
        records = self._selected_records()
        keys = tuple(record.key for record in records if record.key in self.document.values)
        if not keys:
            return
        if QMessageBox.question(
            self,
            "Reset selected user overrides?",
            f"Remove {len(keys):,} user-layer value(s) and reveal the next winning source?\n\n"
            "This is one in-memory command and can be undone before or after saving.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.document.reset(keys)
        self.model.rebuild()
        self._after_model_change()
        self._restore_selection(keys)

    def _restore_selection(self, keys: tuple[str, ...]) -> None:
        if not keys or self.model.snapshot is None:
            return
        selection = self.table.selectionModel()
        selection.clearSelection()
        wanted = set(keys)
        first = None
        for source_row, record in enumerate(self.model.snapshot.records):
            if record.key not in wanted:
                continue
            proxy_index = self.proxy.mapFromSource(self.model.index(source_row, 0))
            if not proxy_index.isValid():
                continue
            selection.select(
                proxy_index,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            first = first or proxy_index
        if first is not None:
            self.table.setCurrentIndex(first)
            self.table.scrollTo(first)

    def _update_actions(self) -> None:
        scope = self._scope()
        snapshot = self.model.snapshot
        self.undo_button.setEnabled(self.document.can_undo)
        self.redo_button.setEnabled(self.document.can_redo)
        self.reset_button.setEnabled(
            any(record.key in self.document.values for record in self._selected_records())
        )
        self.save_button.setEnabled(
            scope is not None
            and scope == self._scope_key
            and self.document.dirty
            and snapshot is not None
            and not snapshot.invalid_count
            and not self._jobs
        )
        self.reload_button.setEnabled(scope is not None and not self._jobs)

    # --- background C3 persistence -------------------------------------

    def _scope(self) -> tuple[str, str] | None:
        target = self.state.target
        if target is None:
            return None
        try:
            return (
                normalize_channel(target.parents[3].name),
                normalize_language(target.parent.name),
            )
        except (IndexError, ValueError):
            return None

    def _scope_changed(self, *_args) -> None:
        if self._shutting_down:
            return
        scope = self._scope()
        if scope == self._scope_key:
            self._update_actions()
            return
        self.state.begin_user_override_scope(scope)
        if self.document.dirty:
            self.status.set_tone(Tone.WARNING)
            self.status.setText(
                "Channel or language changed while unsaved edits exist. Save or undo them before reloading scope."
            )
            self._update_actions()
            return
        self._scope_key = scope
        if scope is not None:
            self.scope_timer.start()
        else:
            self.status.set_tone(Tone.INFO)
            self.status.setText(
                "No installed channel/language scope is selected. Edits remain in memory only."
            )
        self._update_actions()

    @staticmethod
    def _load_snapshot(channel: str, language: str) -> PersistentEditorSnapshot:
        session = EditSession(UserEditStore(channel, language))
        return PersistentEditorSnapshot(
            channel,
            language,
            session.store.path,
            dict(session.values),
            session.history_recovered,
            session.cursor,
            len(session.commands) - session.cursor,
        )

    def load_user_edits(self, *, explicit: bool = False) -> None:
        if self._shutting_down:
            return
        scope = self._scope()
        if scope is None or self._jobs:
            return
        if self.document.dirty:
            if not explicit:
                return
            if QMessageBox.question(
                self,
                "Discard unsaved editor changes?",
                "Reloading replaces the in-memory user layer with the saved channel scope.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return
        channel, language = scope
        self.status.set_tone(Tone.INFO)
        self.status.setText(f"Loading {channel}/{language} user edits in the background…")
        self._start_job(
            lambda token, _reporter: (
                token.checkpoint(),
                self._load_snapshot(channel, language),
                token.checkpoint(),
            )[1],
            on_success=self._loaded,
        )

    def _loaded(self, snapshot: PersistentEditorSnapshot) -> None:
        if (snapshot.channel, snapshot.language) != self._scope():
            return
        self._scope_key = (snapshot.channel, snapshot.language)
        self.state.set_user_overrides(self._scope_key, snapshot.values)
        self.document.load(snapshot.values)
        self.model.rebuild()
        self._after_model_change()
        history = (
            f"{snapshot.undo_count:,} undo / {snapshot.redo_count:,} redo"
            if snapshot.history_recovered
            else "history reset because it did not match user.ini"
        )
        self.status.set_tone(Tone.SUCCESS if snapshot.history_recovered else Tone.WARNING)
        self.status.setText(
            f"Loaded {len(snapshot.values):,} saved user edits from {snapshot.path}. {history}."
        )

    def save_user_edits(self) -> None:
        scope = self._scope()
        snapshot = self.model.snapshot
        command = self.document.save_command()
        if scope is None or snapshot is None or command is None or self._jobs:
            return
        if snapshot.invalid_count:
            QMessageBox.warning(
                self,
                "Fix validation errors first",
                f"{snapshot.invalid_count:,} merged value(s) are invalid. Nothing was saved.",
            )
            return
        channel, language = scope
        baseline = dict(self.document.baseline_values)

        def save(token, _reporter):
            token.checkpoint()
            session = EditSession(UserEditStore(channel, language))
            if session.values != baseline:
                raise UserEditError(
                    "saved user.ini changed outside this editor; reload before saving"
                )
            session.execute(command, allow_empty=True)
            token.checkpoint()
            return self._load_snapshot(channel, language)

        self.status.set_tone(Tone.INFO)
        self.status.setText(
            f"Saving one reviewed command to {channel}/{language} in the background…"
        )
        self._start_job(save, on_success=self._saved)

    def _saved(self, snapshot: PersistentEditorSnapshot) -> None:
        self._loaded(snapshot)
        self.status.set_tone(Tone.SUCCESS)
        self.status.setText(
            f"Saved {len(snapshot.values):,} user-layer values as one C3 command. "
            "No game file was changed."
        )

    def _start_job(self, operation, *, on_success) -> None:
        if self._shutting_down:
            return
        job = QtOperationJob(operation, self)
        self._jobs.add(job)
        self._update_actions()
        job.succeeded.connect(on_success)
        job.failed.connect(self._job_failed)
        job.finished.connect(lambda: self._job_finished(job))
        job.start()

    def _job_failed(self, exc: Exception) -> None:
        self.status.set_tone(Tone.DANGER)
        self.status.setText(f"User-edit operation stopped safely: {exc}")

    def _job_finished(self, job: QtOperationJob) -> None:
        self._jobs.discard(job)
        job.deleteLater()
        if not self._shutting_down:
            self._update_actions()

    def shutdown_jobs(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.scope_timer.stop()
        self.search_timer.stop()
        self.edit_timer.stop()
        self.rebuild_timer.stop()
        for job in tuple(self._jobs):
            job.shutdown(5000)
        self._jobs.clear()


__all__ = ["AdvancedStringEditorTab", "PersistentEditorSnapshot"]
