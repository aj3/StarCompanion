"""Advanced C3 operation-plan review, confirmed apply, and guided recovery.

The page is presentation and orchestration only. Classification, validation,
fingerprinting, backup creation, atomic replacement, and recovery decisions
remain owned by the C0-C5 core modules.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...ini import LocalizationFile
from ...inject import (
    InjectionPlan,
    MergeMode,
    ValidationFailedError,
    apply,
    backup as create_backup,
    build_operation_plan,
    looks_like_game_install,
    restore,
)
from ...transactions import (
    TargetChangedError,
    TransactionJournal,
    bytes_sha256,
    fingerprint,
)
from ..components import (
    MetricTile,
    NoticeBanner,
    OperationPlanView,
    SectionCard,
    Tone,
)
from ..state import AppState

INTRO = (
    "You do not need this tab for normal use; use Update my game on Overview. "
    "This advanced workspace is for a "
    "manually selected localization file, a second game channel such as PTU, or an older backup. "
    "Previewing is read-only. Applying and restoring remain separate, confirmation-"
    "gated operations with target fingerprints and recovery journals."
)

MODES = (
    (
        MergeMode.MERGE,
        "Change only the contracts — leave anything else in the file alone",
    ),
    (
        MergeMode.OVERWRITE,
        "Start from a reviewed clean copy, discarding other packs' changes",
    ),
)


class ApplyTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.current_plan: InjectionPlan | None = None
        self._plan_baseline: LocalizationFile | None = None
        self._backup_fingerprints: dict[str, object] = {}
        self._plan_applied = False
        self.setAccessibleName("Apply an operation plan manually")
        self.setAccessibleDescription(
            "Advanced target selection, complete operation-plan diff, source provenance, "
            "fingerprint-locked apply, and guided backup recovery."
        )

        self.target_edit = QLineEdit()
        self.target_edit.setAccessibleName("Localization file to change")
        self.target_edit.setAccessibleDescription(
            "Target global.ini whose fingerprint will be bound into the operation plan."
        )
        self.target_edit.setPlaceholderText("Select the global.ini to review")
        self.target_edit.textChanged.connect(self._target_changed)
        self.target_browse = QPushButton("Browse…")
        self.target_browse.setAccessibleName("Browse for localization target")
        self.target_browse.setAccessibleDescription(
            "Choose the global.ini that a confirmed operation may replace."
        )
        self.target_browse.clicked.connect(self._browse_target)

        self.stock_edit = QLineEdit()
        self.stock_edit.setAccessibleName("Clean localization source")
        self.stock_edit.setAccessibleDescription(
            "Pristine global.ini used as the reviewed baseline only in overwrite mode."
        )
        self.stock_edit.setPlaceholderText("Required only when rebuilding from a clean copy")
        self.stock_edit.textChanged.connect(self._stock_changed)
        self.stock_browse = QPushButton("Browse…")
        self.stock_browse.setAccessibleName("Browse for clean localization source")
        self.stock_browse.setAccessibleDescription(
            "Choose a pristine localization file for overwrite mode."
        )
        self.stock_browse.clicked.connect(self._browse_stock)

        self.mode = QComboBox()
        self.mode.setAccessibleName("Manual merge mode")
        self.mode.setAccessibleDescription(
            "Choose whether unrelated target values are preserved or replaced from a clean copy."
        )
        for value, label in MODES:
            self.mode.addItem(label, value)
        self.mode.currentIndexChanged.connect(self._mode_changed)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target"))
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(self.target_browse)
        stock_row = QHBoxLayout()
        stock_row.setContentsMargins(0, 0, 0, 0)
        stock_row.addWidget(QLabel("Clean copy"))
        stock_row.addWidget(self.stock_edit, 1)
        stock_row.addWidget(self.stock_browse)
        self.stock_container = QWidget()
        self.stock_container.setLayout(stock_row)

        self.install_warning = NoticeBanner(tone=Tone.WARNING)
        self.install_warning.setVisible(False)
        self.target_section = SectionCard(
            "Target and baseline",
            "These reviewed inputs and the current target fingerprint become the plan identity.",
        )
        self.target_section.add_layout(target_row)
        self.target_section.add_widget(self.stock_container)
        self.target_section.add_widget(self.mode)
        self.target_section.add_widget(self.install_warning)

        self.refresh_button = QPushButton("Prepare reviewed preview")
        self.refresh_button.setProperty("role", "primary")
        self.refresh_button.setAccessibleName("Prepare reviewed operation plan")
        self.refresh_button.setAccessibleDescription(
            "Read the selected files, calculate the complete diff, validate it, and lock "
            "the plan to the target fingerprint without writing anything."
        )
        self.refresh_button.clicked.connect(self.refresh_plan)

        self.plan_view = OperationPlanView()
        self.plan_label = self.plan_view.summary_label
        self.plan_detail = self.plan_view.tree

        self.export_button = QPushButton("Export plan…")
        self.export_button.setAccessibleName("Export serialized operation plan")
        self.export_button.setAccessibleDescription(
            "Save the exact reviewed C3 plan, including its identity and fingerprints, as JSON."
        )
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_plan)

        self.apply_button = QPushButton("Review and apply…")
        self.apply_button.setProperty("role", "danger")
        self.apply_button.setAccessibleName("Review and apply operation plan")
        self.apply_button.setAccessibleDescription(
            "Confirm the exact reviewed plan before a fingerprint-checked backup and atomic write."
        )
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_changes)

        plan_actions = QHBoxLayout()
        plan_actions.addWidget(self.refresh_button)
        plan_actions.addWidget(self.export_button)
        plan_actions.addStretch(1)
        plan_actions.addWidget(self.apply_button)
        plan_panel = QWidget()
        plan_layout = QVBoxLayout(plan_panel)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.addWidget(self.plan_view, 1)
        plan_layout.addLayout(plan_actions)

        self.recovery_metric = MetricTile("Recovery state")
        self.backup_metric = MetricTile("Backups")
        self.last_operation_metric = MetricTile("Last operation")
        recovery_metrics = QGridLayout()
        recovery_metrics.setSpacing(8)
        recovery_metrics.addWidget(self.recovery_metric, 0, 0)
        recovery_metrics.addWidget(self.backup_metric, 0, 1)
        recovery_metrics.addWidget(self.last_operation_metric, 0, 2)

        self.recovery_notice = NoticeBanner(tone=Tone.INFO)
        self.backups = QListWidget()
        self.backups.setAccessibleName("Available localization backups")
        self.backups.setAccessibleDescription(
            "Target-scoped backups. Select one to preview a guarded restore."
        )
        self.restore_button = QPushButton("Review selected backup…")
        self.restore_button.setAccessibleName("Review selected backup recovery")
        self.restore_button.setAccessibleDescription(
            "Confirm a fingerprint-checked restore that first preserves the current target."
        )
        self.restore_button.clicked.connect(self.restore_backup)
        self.resolve_button = QPushButton("Resolve verified interruption")
        self.resolve_button.setAccessibleName("Resolve verified interrupted operation")
        self.resolve_button.setAccessibleDescription(
            "Finalize or discard only a recovery journal whose target still matches a known state."
        )
        self.resolve_button.clicked.connect(self.resolve_recovery)

        recovery_actions = QHBoxLayout()
        recovery_actions.addWidget(self.restore_button)
        recovery_actions.addWidget(self.resolve_button)
        self.recovery_section = SectionCard(
            "Backup and recovery",
            "Known interrupted states can be finalized; unknown states stay blocked.",
        )
        self.recovery_section.add_layout(recovery_metrics)
        self.recovery_section.add_widget(self.recovery_notice)
        self.recovery_section.add_widget(self.backups, 1)
        self.recovery_section.add_layout(recovery_actions)

        # Begin in the narrow stacked form to avoid imposing a desktop-width
        # minimum on the scroll viewport. resizeEvent promotes it when possible.
        workspace = QSplitter(Qt.Orientation.Vertical)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(plan_panel)
        workspace.addWidget(self.recovery_section)
        workspace.setSizes([480, 410])
        workspace.setMinimumHeight(900)
        self.workspace_splitter = workspace

        self.intro_label = QLabel(INTRO)
        self.intro_label.setProperty("role", "muted")
        self.intro_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro_label)
        layout.addWidget(self.target_section)
        layout.addWidget(workspace, 1)

        focus_order = [
            self.target_edit,
            self.target_browse,
            self.stock_edit,
            self.stock_browse,
            self.mode,
            self.refresh_button,
            self.plan_view.filter,
            self.plan_view.tree,
            self.export_button,
            self.apply_button,
            self.backups,
            self.restore_button,
            self.resolve_button,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.profileChanged.connect(self._sync_mode)
        state.profileChanged.connect(self._invalidate_plan)
        state.contractsChanged.connect(self._invalidate_plan)
        state.pathsChanged.connect(self._invalidate_plan)
        state.pathsChanged.connect(self.refresh_recovery)
        self._sync_mode()
        self.refresh_recovery()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Use the shell width, not this page's current size hint: the wide
        # splitter itself can otherwise keep a narrow viewport artificially wide.
        wide = self.window().width() >= 1180
        orientation = (
            Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical
        )
        if self.workspace_splitter.orientation() != orientation:
            self.workspace_splitter.setOrientation(orientation)
            self.workspace_splitter.setMinimumHeight(360 if wide else 900)
            self.workspace_splitter.setSizes([650, 420] if wide else [480, 410])
        super().resizeEvent(event)

    # --- target and plan -------------------------------------------------

    def _browse_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select global.ini", "", "INI (*.ini)")
        if path:
            self.target_edit.setText(path)

    def _browse_stock(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select pristine global.ini", "", "INI (*.ini)"
        )
        if path:
            self.stock_edit.setText(path)

    def _target_changed(self, text: str) -> None:
        path = Path(text.strip()) if text.strip() else None
        self.state.set_target(path)
        self._update_install_warning(path)

    def _stock_changed(self, text: str) -> None:
        self.state.set_stock(Path(text.strip()) if text.strip() else None)

    def _update_install_warning(self, path: Path | None) -> None:
        install = looks_like_game_install(path) if path else None
        self.install_warning.setVisible(install is not None)
        if install:
            self.install_warning.setText(
                f"This target is inside the Star Citizen install at {install}. "
                "The reviewed write remains confirmation-gated and creates a backup first."
            )

    def _mode_changed(self, index: int) -> None:
        value = self.mode.itemData(index)
        if value is None:
            return
        self.state.profile.injection.mode = value.value
        self.state.touch_profile()

    def _sync_mode(self) -> None:
        index = self.mode.findData(self.state.profile.injection.merge_mode)
        if index >= 0 and index != self.mode.currentIndex():
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(index)
            self.mode.blockSignals(False)
        overwrite = self.state.profile.injection.merge_mode is MergeMode.OVERWRITE
        self.stock_container.setVisible(overwrite)
        self.stock_edit.setEnabled(overwrite)
        self.stock_browse.setEnabled(overwrite)

    def _invalidate_plan(self, *_args) -> None:
        self.current_plan = None
        self._plan_baseline = None
        self._plan_applied = False
        self.export_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.plan_view.clear_plan("Inputs changed. Prepare a fresh preview before applying.")

    def refresh_plan(self):
        self.current_plan = None
        self._plan_baseline = None
        self._plan_applied = False
        self.export_button.setEnabled(False)
        self.apply_button.setEnabled(False)

        if self.state.contracts is None:
            message = "Read your contracts on the Start tab (Overview) first."
            self.plan_view.clear_plan(message)
            return None
        if not self.state.user_overrides_ready:
            message = (
                "Channel-scoped user edits are still loading. Preview waits so no saved "
                "winner is omitted or mixed across scopes."
            )
            self.plan_view.clear_plan(message)
            return None
        target = self.state.target
        if target is None or not target.is_file():
            message = "Choose which file to change, above. Nothing was read or written."
            self.plan_view.clear_plan(message)
            return None

        mode = self.state.profile.injection.merge_mode
        if mode is MergeMode.OVERWRITE and (
            self.state.stock is None or not self.state.stock.is_file()
        ):
            message = "Choose a readable clean copy before previewing overwrite mode."
            self.plan_view.clear_plan(message)
            return None

        try:
            effective = LocalizationFile.load(target)
            baseline = (
                LocalizationFile.load(self.state.stock)
                if mode is MergeMode.OVERWRITE
                else LocalizationFile.loads(effective.dumps())
            )
            replacements = self.state.effective_values()
            result, desired_data = build_operation_plan(
                baseline,
                effective,
                replacements,
            )
            report = self.state.source_report()
            install = looks_like_game_install(target)
            channel = self._channel_label(target, install)
            language = target.parent.name or "unknown"
            result.bind(
                channel=channel,
                language=language,
                mode=mode,
                baseline_source="clean-copy" if mode is MergeMode.OVERWRITE else "override",
                target=target,
                target_fingerprint=fingerprint(target),
                baseline_sha256=bytes_sha256(baseline.dumps().encode("utf-8")),
                desired_sha256=bytes_sha256(desired_data),
                source_report=report,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            self.plan_view.clear_plan(f"Preview failed safely: {exc}")
            return None

        self.current_plan = result
        self._plan_baseline = baseline
        self.plan_view.set_plan(result)
        self.export_button.setEnabled(True)
        has_changes = bool(result.added or result.updated or result.removed)
        recovery_clean = self._recovery_report().status == "clean"
        self.apply_button.setEnabled(result.is_valid and has_changes and recovery_clean)
        return result

    @staticmethod
    def _channel_label(target: Path, install: Path | None) -> str:
        if install is None:
            return "manual"
        try:
            return target.parents[3].name or install.name
        except IndexError:
            return install.name or "manual"

    def export_plan(self) -> None:
        if self.current_plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export operation plan",
            f"starcompanion-{self.current_plan.plan_id or 'plan'}.json",
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            self.current_plan.save(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not export plan", str(exc))

    # --- confirmed write ------------------------------------------------

    def apply_changes(self) -> None:
        if (
            self.state.profile.injection.merge_mode is MergeMode.OVERWRITE
            and (self.state.stock is None or not self.state.stock.is_file())
        ):
            QMessageBox.warning(
                self,
                "A clean copy is needed",
                "Overwrite mode needs a readable, unmodified localization file. "
                "Choose one above before preparing or applying a plan.",
            )
            return
        result = self.current_plan or self.refresh_plan()
        if result is None:
            return
        if self._plan_applied or self._plan_baseline is None:
            QMessageBox.information(
                self, "Prepare a fresh preview", "This reviewed plan is no longer active."
            )
            return
        if not result.is_valid:
            QMessageBox.critical(
                self,
                "Cannot apply",
                f"{len(result.errors)} value(s) failed validation. Nothing was written.",
            )
            return
        recovery = self._recovery_report()
        if recovery.status != "clean":
            QMessageBox.warning(
                self,
                "Recovery must be resolved first",
                f"{recovery.status}: {recovery.message}",
            )
            self.refresh_recovery()
            return
        if not self.confirm(result):
            return

        try:
            apply(
                self.state.target,
                self.state.effective_values(),
                confirmed=True,
                source=self._plan_baseline,
                backup_dir=self._backup_directory(),
                expected_fingerprint=result.target_fingerprint,
                operation_plan=result,
                journal=self._journal(),
            )
        except (ValidationFailedError, TargetChangedError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Write stopped safely",
                f"{exc}\n\nReview recovery status and prepare a fresh plan before retrying.",
            )
            self.refresh_recovery()
            return

        self._plan_applied = True
        self._plan_baseline = None
        self.apply_button.setEnabled(False)
        self.plan_view.set_plan(result)
        self.refresh_recovery()
        QMessageBox.information(
            self,
            "Operation verified",
            f"The atomic write completed and the result fingerprint was verified.\n\n"
            f"Plan: {result.plan_id}\nBackup: {result.backup}",
        )

    def confirm(self, result: InjectionPlan) -> bool:
        install = looks_like_game_install(self.state.target)
        warning = f"\n\nStar Citizen install:\n{install}" if install else ""
        target_id = (
            result.target_fingerprint.sha256[:12]
            if result.target_fingerprint and result.target_fingerprint.sha256
            else "missing"
        )
        answer = QMessageBox.question(
            self,
            "Apply the reviewed operation plan?",
            f"Target:\n{self.state.target}\n\n{result.summary()}\n\n"
            f"Plan ID: {result.plan_id}\nTarget fingerprint: {target_id}…\n"
            f"A backup is created before the atomic replacement.{warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    # --- guided recovery ------------------------------------------------

    def _backup_directory(self) -> Path:
        if self.state.backup_dir is not None:
            return self.state.backup_dir
        if self.state.target is None:
            return Path("backups")
        return self.state.target.parent / "backups"

    def _journal(self) -> TransactionJournal:
        directory = self._backup_directory()
        return TransactionJournal(
            directory / ".apply-journal.json",
            directory / "last-operation.json",
        )

    def _recovery_report(self):
        if self.state.target is None:
            return None
        return self._journal().inspect(self.state.target)

    def refresh_backups(self) -> None:
        self.backups.clear()
        self._backup_fingerprints.clear()
        target = self.state.target
        if target is None:
            return
        pattern = f"{target.stem}.*{target.suffix}"
        directory = self._backup_directory()
        if not directory.is_dir():
            return
        for path in sorted(directory.glob(pattern), reverse=True):
            scoped = self._scoped_backup(path)
            if scoped is None:
                continue
            try:
                identity = fingerprint(scoped)
            except OSError:
                continue
            resolved = str(scoped)
            self._backup_fingerprints[resolved] = identity
            digest = f"{identity.sha256[:10]}…" if identity.sha256 else "unreadable"
            item = QListWidgetItem(f"{resolved}  [{identity.size:,} bytes / {digest}]")
            item.setData(Qt.ItemDataRole.UserRole, resolved)
            self.backups.addItem(item)

    def _scoped_backup(self, path: Path) -> Path | None:
        """Resolve only ordinary files directly inside the active backup root."""
        try:
            if path.is_symlink() or not path.is_file():
                return None
            directory = self._backup_directory().resolve()
            resolved = path.resolve()
        except OSError:
            return None
        return resolved if resolved.parent == directory else None

    def refresh_recovery(self) -> None:
        self.refresh_backups()
        count = self.backups.count()
        self.backup_metric.set_value(f"{count:,}", "target-scoped restore points")
        if self.state.target is None:
            self.recovery_metric.set_value("Not ready")
            self.last_operation_metric.set_value("—")
            self.recovery_notice.set_tone(Tone.INFO)
            self.recovery_notice.setText("Choose a target to inspect its recovery state.")
            self.resolve_button.setEnabled(False)
            self.restore_button.setEnabled(False)
            return

        report = self._recovery_report()
        last = self._journal().last_operation()
        last_name = str(last.get("operation", "write")) if last else "None recorded"
        last_stage = str(last.get("stage", "")) if last else ""
        self.last_operation_metric.set_value(last_name.title(), last_stage)
        self.recovery_metric.set_value(report.status.replace("-", " ").title())
        self.recovery_notice.setText(f"{report.status}: {report.message}")
        if report.needs_attention:
            self.recovery_notice.set_tone(Tone.DANGER)
        elif report.status == "clean":
            self.recovery_notice.set_tone(Tone.SUCCESS if last else Tone.INFO)
        else:
            self.recovery_notice.set_tone(Tone.WARNING)
        self.resolve_button.setEnabled(report.status != "clean" and not report.needs_attention)
        self.restore_button.setEnabled(count > 0 and report.status == "clean")
        if self.current_plan is not None and not self._plan_applied:
            has_changes = bool(
                self.current_plan.added
                or self.current_plan.updated
                or self.current_plan.removed
            )
            self.apply_button.setEnabled(
                self.current_plan.is_valid and has_changes and report.status == "clean"
            )

    def resolve_recovery(self) -> None:
        if self.state.target is None:
            return
        report = self._recovery_report()
        if report.needs_attention:
            QMessageBox.critical(
                self,
                "Automatic recovery is blocked",
                report.message,
            )
            return
        resolved = self._journal().inspect(self.state.target, resolve_safe=True)
        self.refresh_recovery()
        QMessageBox.information(
            self,
            "Recovery state resolved",
            f"{resolved.status}: {resolved.message}\n\nNo unknown target state was overwritten.",
        )

    def _selected_backup(self) -> tuple[Path, object] | None:
        item = self.backups.currentItem()
        if item is None:
            return None
        resolved = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(resolved, str):
            return None
        identity = self._backup_fingerprints.get(resolved)
        if identity is None:
            return None
        return Path(resolved), identity

    def restore_backup(self) -> None:
        selected = self._selected_backup()
        target = self.state.target
        if selected is None or target is None:
            return
        report = self._recovery_report()
        if report.status != "clean":
            QMessageBox.warning(
                self,
                "Recovery must be resolved first",
                f"{report.status}: {report.message}",
            )
            return
        backup_path, backup_fingerprint = selected
        if self._scoped_backup(backup_path) != backup_path:
            QMessageBox.critical(
                self,
                "Backup is no longer safe to restore",
                "The selected backup is no longer an ordinary file inside the active "
                "backup directory. Refresh the recovery list and inspect the directory.",
            )
            self.refresh_recovery()
            return
        before = fingerprint(target)
        backup_id = backup_fingerprint.sha256[:12] if backup_fingerprint.sha256 else "missing"
        target_id = before.sha256[:12] if before.sha256 else "missing"
        if QMessageBox.question(
            self,
            "Restore this reviewed backup?",
            f"Target:\n{target}\nFingerprint: {target_id}…\n\n"
            f"Backup:\n{backup_path}\nFingerprint: {backup_id}…\n\n"
            "The current target is backed up first, then the selected backup is restored "
            "and verified.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        journal = self._journal()
        rollback_id = bytes_sha256(
            f"rollback\0{target.resolve()}\0{before.sha256}\0{backup_fingerprint.sha256}".encode(
                "utf-8"
            )
        )
        try:
            journal.begin(
                operation="rollback",
                plan_id=rollback_id,
                target=target,
                before=before,
                after_sha256=backup_fingerprint.sha256,
            )
            recovery_backup = create_backup(target, self._backup_directory())
            journal.record_backup(recovery_backup)
            if fingerprint(target) != before:
                raise TargetChangedError(
                    "target changed while the recovery backup was being created"
                )
            restore(
                backup_path,
                target,
                expected_backup_fingerprint=backup_fingerprint,
                expected_target_fingerprint=before,
            )
            journal.record_replaced()
            final = fingerprint(target)
            if final.sha256 != backup_fingerprint.sha256:
                raise OSError("restored target fingerprint does not match the reviewed backup")
            journal.complete(final=final)
        except (TargetChangedError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Restore stopped safely",
                f"{exc}\n\nThe recovery journal was retained for guided inspection.",
            )
            self.refresh_recovery()
            return

        self._invalidate_plan()
        self.refresh_recovery()
        QMessageBox.information(
            self,
            "Backup restored and verified",
            f"The selected backup now matches the target.\n\n"
            f"Recovery backup: {recovery_backup}",
        )
