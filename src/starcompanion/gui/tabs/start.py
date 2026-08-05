"""The Overview dashboard: the whole job for someone new to local game tools.

Design rules here, deliberately different from the other tabs:

- **Never ask for a path we can work out.** The game is found automatically;
  the file to modify is derived from it.
- **Say what things are, not what they are called.** "Your Star Citizen game",
  not "target global.ini".
- **One obvious button.** Everything else is optional.
- **Explain the consequence before it happens**, not in a dialog afterwards.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import install as installs
from ... import store
from ...features import community_rewards_enabled
from ..labels import PREFIX_CAPTION, TITLE_PREFIXES
from ...inject import restore as restore_backup
from ...model import ProviderStatus
from ...operations import PreparedUpdate, prepare_update, read_contracts
from ...sources import contracts_ini
from ...tasks import ProgressEvent
from ..jobs import Operation, QtOperationJob
from ..state import AppState
from ..components import DashboardHero, EmptyState, NoticeBanner, StatusCard, Tone

CONTRACTS_URL = (
    "https://github.com/MrKraken/StarStrings/blob/master/src/For_Tool_Creators/contracts.ini"
)

OK = "✅"
TODO = "⬜"
WARN = "⚠"

LOOKS = (
    ("default", "Show everything", "Reputation, blueprints and event points on every contract."),
    ("minimal", "Just the essentials", "Only a rep number and a blueprint marker in the title."),
    ("rank-first", "Sort-friendly", "Puts the mission giver and difficulty at the front of every title."),
)


class StartTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.install: installs.GameInstall | None = None
        self.installs: list[installs.GameInstall] = []
        self.load_error: str | None = None
        self.operation_status: str | None = None
        self._jobs: set[QtOperationJob] = set()
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        self.preference_warning = NoticeBanner(tone=Tone.DANGER)
        self.preference_warning.setVisible(False)
        layout.addWidget(self.preference_warning)

        self.go = QPushButton("Update my game")
        self.go.setProperty("role", "primary")
        self.go.setAccessibleDescription(
            "Reads local data if needed, prepares an operation plan, and asks before writing."
        )
        self.go.setMinimumHeight(44)
        font = self.go.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.go.setFont(font)
        self.go.clicked.connect(self.update_game)
        self.undo = QPushButton("Undo my last change")
        self.undo.setProperty("role", "danger")
        self.undo.setAccessibleDescription(
            "Restores the newest local backup after explicit confirmation."
        )
        self.undo.clicked.connect(self.undo_last)

        self.hero = DashboardHero()
        self.hero.add_action(self.go)
        self.hero.add_action(self.undo)
        layout.addWidget(self.hero)

        self.footer = NoticeBanner(tone=Tone.INFO)
        layout.addWidget(self.footer)

        cards = QGridLayout()
        cards.setHorizontalSpacing(16)
        cards.setVerticalSpacing(16)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        cards.addWidget(self._build_game_step(), 0, 0)
        cards.addWidget(self._build_contract_step(), 0, 1)
        self.data_step = self._build_data_step()
        cards.addWidget(self.data_step, 1, 0)
        cards.addWidget(self._build_look_step(), 1, 1)
        layout.addLayout(cards)

        layout.addStretch(1)

        focus_order = [
            self.go,
            self.undo,
            self.channel_selector,
            self.discover_channels_button,
            self.find_game_button,
            self.choose_game_button,
            self.read_button,
            self.look,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.contractsChanged.connect(self.refresh)
        state.profileChanged.connect(self.refresh)
        state.userOverridesChanged.connect(self.refresh)
        self.detect_game()
        self.refresh()

    # --- step 1: the game ----------------------------------------------------

    def _build_game_step(self) -> StatusCard:
        card = StatusCard("Game installation")

        self.game_status = QLabel()
        self.game_status.setWordWrap(True)
        self.game_status.setProperty("role", "muted")
        card.add_widget(self.game_status)

        self.language_warning = NoticeBanner(tone=Tone.WARNING)
        self.language_warning.setVisible(False)
        card.add_widget(self.language_warning)

        self.channel_selector = QComboBox()
        self.channel_selector.setAccessibleName("Installed Star Citizen channel")
        self.channel_selector.setAccessibleDescription(
            "Choose one locally discovered installation. Channel data, edits, ownership, and backups remain isolated."
        )
        self.channel_selector.setPlaceholderText("No installed channel found")
        self.channel_selector.currentIndexChanged.connect(self._channel_selected)
        card.add_widget(self.channel_selector)

        self.discover_channels_button = QPushButton("Discover installed channels")
        self.discover_channels_button.setAccessibleName("Discover installed Star Citizen channels")
        self.discover_channels_button.setAccessibleDescription(
            "Search standard local launcher locations in a background worker without network access."
        )
        self.discover_channels_button.clicked.connect(self.discover_channels)

        self.find_game_button = QPushButton("Find my game again")
        self.find_game_button.setAccessibleDescription(
            "Repeat automatic discovery of installed Star Citizen channels."
        )
        self.find_game_button.clicked.connect(lambda: (self.detect_game(), self.refresh()))
        self.choose_game_button = QPushButton("Choose folder…")
        self.choose_game_button.setAccessibleDescription(
            "Choose a channel folder containing Data.p4k."
        )
        self.choose_game_button.clicked.connect(self.choose_game)
        card.add_action(self.discover_channels_button)
        card.add_action(self.find_game_button)
        card.add_action(self.choose_game_button)

        self.game_card = card
        return card

    def _build_contract_step(self) -> StatusCard:
        card = StatusCard("Contract data")

        self.contracts_status = QLabel()
        self.contracts_status.setWordWrap(True)
        self.contracts_status.setProperty("role", "muted")
        card.add_widget(self.contracts_status)

        self.contracts_empty = EmptyState(
            "Nothing loaded yet",
            "Read the localization and mission records from your local archive. "
            "The first pass usually takes about half a minute.",
        )
        card.add_widget(self.contracts_empty)

        self.read_button = QPushButton("Read contracts from my game")
        self.read_button.setAccessibleDescription(
            "Read and cache contract data from the selected local game archive."
        )
        self.read_button.clicked.connect(lambda: self.read_game(force=True))
        card.add_action(self.read_button)

        self.contract_card = card
        return card

    def detect_game(self) -> None:
        found = installs.find_default()
        self._set_installs([found] if found is not None else [])

    def discover_channels(self) -> None:
        """Run the potentially broad launcher-location scan outside Qt's UI thread."""
        self._run_operation(
            "Discovering installed channels…",
            lambda token, _reporter: self._discover_channels(token),
            on_success=self._set_installs,
            on_failure=lambda exc: QMessageBox.warning(
                self, "Could not discover installed channels", str(exc)
            ),
        )

    @staticmethod
    def _discover_channels(token) -> list[installs.GameInstall]:
        token.checkpoint()
        found = installs.find_installs(checkpoint=token.checkpoint)
        token.checkpoint()
        return found

    def _set_installs(self, found) -> None:
        unique = {item.root.resolve(): item for item in found if item is not None}
        self.installs = sorted(
            unique.values(),
            key=lambda item: (installs.CHANNELS.index(item.channel), str(item.root)),
        )
        previous = self.install.root.resolve() if self.install is not None else None
        self.channel_selector.blockSignals(True)
        self.channel_selector.clear()
        for item in self.installs:
            self.channel_selector.addItem(item.label, item)
        selected = next(
            (
                index
                for index, item in enumerate(self.installs)
                if previous is not None and item.root.resolve() == previous
            ),
            0 if self.installs else -1,
        )
        self.channel_selector.setCurrentIndex(selected)
        self.channel_selector.blockSignals(False)
        self.install = self.installs[selected] if selected >= 0 else None
        self._adopt_install()
        self.operation_status = (
            f"Discovered {len(self.installs):,} installed channel(s)."
            if self.installs
            else "No installed channels were found automatically. Choose a folder to continue."
        )
        self.refresh()

    def _channel_selected(self, index: int) -> None:
        item = self.channel_selector.itemData(index)
        if not isinstance(item, installs.GameInstall):
            return
        self.install = item
        self._adopt_install()
        self.operation_status = f"Selected {item.channel}; channel-scoped state is loading."
        self.refresh()

    def choose_game(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Find your Star Citizen folder (the one containing Data.p4k)"
        )
        if not chosen:
            return

        found = installs.identify(Path(chosen))
        if found is None:
            QMessageBox.warning(
                self,
                "That does not look like Star Citizen",
                "No Data.p4k was found there.\n\n"
                "Look for a folder called LIVE, usually inside\n"
                "Roberts Space Industries\\StarCitizen.",
            )
            return

        self.install = found
        self._set_installs([*self.installs, found])

    def _adopt_install(self) -> None:
        """Derive everything else from the install, including the contracts.

        The game's own strings are the source. Reading them here is what makes
        the second step optional rather than required.
        """
        if self.install is None:
            self.state.set_target(None)
            return

        self.state.set_target(self.install.localization())

        # Only ever the cache here. Reading the archive takes ~30 seconds on a
        # real install, which must never happen during startup.
        cached = store.load(self.install)
        if cached is not None:
            self.state.set_contracts(cached)
            self.load_error = None

    def read_game(self, *, force: bool = False, after=None) -> None:
        """Read contracts from the archive, with progress, and cache them.

        `force` re-reads even when a cached copy exists, which is what the
        button does: if someone presses it deliberately, they want the archive
        looked at again.
        """
        if self.install is None:
            return

        if not force:
            cached = store.load(self.install)
            if cached is not None:
                self.state.set_contracts(cached)
                self.load_error = None
                self.refresh()
                if after is not None:
                    after()
                return

        install = self.install
        succeeded = {"value": False}

        def loaded(contracts) -> None:
            succeeded["value"] = True
            self.load_error = None
            store.save(install, contracts)
            self.state.set_contracts(contracts)
            self.refresh()

        def failed(exc: Exception) -> None:
            self.load_error = str(exc)
            QMessageBox.warning(self, "Could not read your game", str(exc))

        job = self._run_operation(
            "Reading your game files…",
            lambda token, reporter: read_contracts(
                install,
                token=token,
                reporter=reporter,
            ),
            on_success=loaded,
            on_failure=failed,
        )
        if job is not None and after is not None:
            job.finished.connect(
                lambda: after() if succeeded["value"] else None
            )

    # --- step 2: contract data -----------------------------------------------

    def _build_data_step(self) -> StatusCard:
        card = StatusCard("Local enhancements")

        self.data_status = QLabel()
        self.data_status.setWordWrap(True)
        self.data_status.setProperty("role", "muted")
        card.add_widget(self.data_status)

        card.add_widget(
            _muted(
                "Reputation, blueprint pools, and direct item rewards are read from "
                "Data/Game2.dcb in your local archive. No community download or "
                "network connection is required."
            )
        )

        download = QPushButton("Get a contract list…")
        download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CONTRACTS_URL)))
        pick = QPushButton("Add reward numbers…")
        pick.clicked.connect(self.choose_contracts)
        download.setVisible(community_rewards_enabled())
        pick.setVisible(community_rewards_enabled())
        card.add_action(download)
        card.add_action(pick)

        self.data_card = card
        return card

    def choose_contracts(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose contracts.ini", "", "Contract list (*.ini);;All files (*)"
        )
        if not chosen:
            return

        try:
            self.state.set_contracts(contracts_ini.load(Path(chosen)))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Could not read that file",
                f"{exc}\n\nIt should be the contracts.ini from StarStrings.",
            )

    # --- step 3: the look ----------------------------------------------------

    def _build_look_step(self) -> StatusCard:
        card = StatusCard("Quick presentation")

        self.look = QComboBox()
        self.look.setAccessibleName("Quick contract-title presentation")
        self.look.setAccessibleDescription(
            "Choose what identifying information appears at the front of contract titles."
        )
        for value, label, _hint in TITLE_PREFIXES:
            self.look.addItem(label, value)
        self.look.currentIndexChanged.connect(self._look_changed)
        card.add_widget(self.look)

        self.look_hint = _muted(TITLE_PREFIXES[0][2])
        card.add_widget(self.look_hint)
        card.add_widget(_muted(PREFIX_CAPTION))

        # "Both" is the useful default: it is what makes a long list scannable.
        self.look.setCurrentIndex(self.look.findData("org_rank"))

        self.look_card = card
        return card

    def _look_changed(self, index: int) -> None:
        value = self.look.itemData(index)
        self.look_hint.setText(
            next(hint for v, _n, hint in TITLE_PREFIXES if v == value)
        )
        self.state.profile.formatting.title.prefix = value
        self.state.touch_profile()

    # --- doing it ------------------------------------------------------------

    def update_game(self) -> None:
        if self.install is None:
            QMessageBox.information(
                self, "Find your game first",
                "StarCompanion could not find Star Citizen automatically.\n\n"
                "Use 'Choose folder…' in Step 1.",
            )
            return
        if self.state.contracts is None:
            self.read_game(after=self.update_game)
            return
        if not self.state.user_overrides_ready:
            QMessageBox.information(
                self,
                "User edits are still loading",
                "Wait for the channel-scoped user.ini load to finish before preparing "
                "an update. This prevents edits from another channel being omitted or mixed.",
            )
            return

        try:
            replacements = self.state.effective_values()
            sources = self.state.source_report()
        except Exception as exc:
            QMessageBox.critical(self, "Could not prepare your contracts", str(exc))
            return

        install = self.install
        mode = self.state.profile.injection.merge_mode
        self._run_operation(
            "Preparing your contracts…",
            lambda token, reporter: prepare_update(
                install,
                replacements,
                mode=mode,
                token=token,
                reporter=reporter,
                source_report=sources,
            ),
            on_success=self._confirm_prepared_update,
            on_failure=lambda exc: QMessageBox.critical(
                self, "Something went wrong", str(exc)
            ),
        )

    def _confirm_prepared_update(self, prepared: PreparedUpdate) -> None:
        try:
            self._use_prepared_update(prepared)
        finally:
            prepared.cleanup()

    def _use_prepared_update(self, prepared: PreparedUpdate) -> None:
        result = prepared.plan

        if not result.updated:
            QMessageBox.information(
                self, "Nothing to change",
                "Your game text already matches these settings.",
            )
            return

        confirmed = QMessageBox.question(
            self,
            "Ready to update your game?",
            f"{len(result.updated):,} contract strings will be updated.\n\n"
            f"Game: {self.install.channel}\n"
            f"A backup is saved first, and 'Undo my last change' puts it back.\n\n"
            f"Star Citizen must be closed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            written = prepared.localization.commit(
                prepared.replacements,
                confirmed=True,
                backup_dir=self.state.backup_dir,
            )
        except Exception as exc:  # surfaced, never swallowed
            QMessageBox.critical(
                self, "Could not update your game",
                f"{exc}\n\nYour game text was not changed.",
            )
            return

        self.refresh()
        QMessageBox.information(
            self,
            "Done",
            f"{len(written.updated):,} contract strings updated.\n\n"
            f"Start Star Citizen and open the contract manager to see it.\n\n"
            f"Remember: run this again after every game patch.",
        )

    # --- background operations ---------------------------------------------

    def _run_operation(
        self,
        title: str,
        operation: Operation,
        *,
        on_success,
        on_failure,
    ) -> QtOperationJob | None:
        if self._jobs:
            return None

        dialog = QProgressDialog(title, "Cancel", 0, 1000, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)

        job = QtOperationJob(operation, self)
        self._jobs.add(job)
        self._busy = True
        self.operation_status = None
        self.refresh()

        job.progress.connect(lambda event: self._show_progress(dialog, event))
        job.succeeded.connect(lambda value: (dialog.close(), on_success(value)))
        job.failed.connect(lambda exc: (dialog.close(), on_failure(exc)))
        job.cancelled.connect(
            lambda: (
                dialog.close(),
                setattr(
                    self,
                    "operation_status",
                    "Cancelled safely. Nothing was changed.",
                ),
            )
        )
        job.finished.connect(lambda: self._operation_finished(job, dialog))
        dialog.canceled.connect(
            lambda: (
                dialog.setLabelText("Cancelling safely…"),
                dialog.setCancelButton(None),
                job.cancel(),
            )
        )
        job.start()
        return job

    @staticmethod
    def _show_progress(dialog: QProgressDialog, event: ProgressEvent) -> None:
        dialog.setLabelText(event.message)
        dialog.setValue(round(event.fraction * 1000))

    def _operation_finished(
        self,
        job: QtOperationJob,
        dialog: QProgressDialog,
    ) -> None:
        dialog.close()
        self._jobs.discard(job)
        job.deleteLater()
        self._busy = bool(self._jobs)
        self.refresh()

    def wait_for_jobs(self, timeout_ms: int = 5000) -> bool:
        """Pump queued results while waiting; intended for tests and smoke tools."""
        deadline = time.monotonic() + (timeout_ms / 1000)
        while self._jobs and time.monotonic() < deadline:
            QApplication.processEvents()
            for job in list(self._jobs):
                job.wait(5)
        QApplication.processEvents()
        return not self._jobs

    def shutdown_jobs(self) -> None:
        """Cancel and visibly join workers before their window disappears."""
        jobs = list(self._jobs)
        if not jobs:
            return

        self.operation_status = "Waiting for game-file work to stop safely…"
        dialog = QProgressDialog(
            self.operation_status,
            "",
            0,
            0,
            self.window(),
        )
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.show()
        QApplication.processEvents()

        started = time.monotonic()
        remaining = jobs
        while remaining:
            still_running = []
            for job in remaining:
                if not job.shutdown(25):
                    still_running.append(job)
            remaining = still_running
            elapsed = time.monotonic() - started
            if elapsed >= 0.5:
                dialog.setLabelText(
                    "Still waiting for a safe game-file checkpoint…\n"
                    "No file will be left half-written."
                )
            QApplication.processEvents()

        dialog.close()
        self._jobs.clear()
        self._busy = False
        self.operation_status = "Background game-file work stopped safely."

    def undo_last(self) -> None:
        backups = self.state.backups()
        if not backups:
            QMessageBox.information(
                self, "Nothing to undo",
                "No backup was found, so there is nothing to put back.",
            )
            return
        if self.state.target is None:
            return

        newest = backups[0]
        if QMessageBox.question(
            self, "Undo the last change?",
            f"This restores your game text from:\n{newest.name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        restore_backup(newest, self.state.target)
        self.refresh()
        QMessageBox.information(self, "Undone", "Your game text was put back.")

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        profile_prefix = self.state.profile.formatting.title.prefix
        prefix_index = self.look.findData(profile_prefix)
        if prefix_index >= 0 and prefix_index != self.look.currentIndex():
            self.look.blockSignals(True)
            self.look.setCurrentIndex(prefix_index)
            self.look.blockSignals(False)
        self.look_hint.setText(
            next(hint for value, _name, hint in TITLE_PREFIXES if value == profile_prefix)
        )

        self.game_status.setText(_without_status_marker(self.game_status_text()))
        self.contracts_status.setText(_without_status_marker(self.contracts_status_text()))
        self.data_status.setText(_without_status_marker(self.data_status_text()))

        contracts = self.state.contracts
        self.contracts_empty.setVisible(contracts is None)
        self.contracts_status.setVisible(contracts is not None or bool(self.load_error))

        if self._busy:
            self.hero.set_message(
                "Reading local game data",
                "Processing is cancellable and no game file is changed during extraction.",
                Tone.INFO,
            )
        elif self.install is None:
            self.hero.set_message(
                "Connect your Star Citizen install",
                "Choose an installed channel to begin. StarCompanion reads it locally and never uploads game data.",
                Tone.WARNING,
            )
        elif contracts is None:
            self.hero.set_message(
                "Ready to build your local contract index",
                "The primary action will read your archive first, then prepare a confirm-before-write update.",
                Tone.INFO,
            )
        elif not self.state.user_overrides_ready:
            self.hero.set_message(
                "Loading channel-specific wording",
                "The update action will become available after the local user layer is safely loaded.",
                Tone.INFO,
            )
        else:
            self.hero.set_message(
                "Your contract workspace is ready",
                f"{len(contracts.contracts):,} contracts are loaded. Prepare the update to review changes before anything is written.",
                Tone.SUCCESS,
            )

        if self.install is None:
            self.game_card.set_status("Install required", Tone.WARNING)
        else:
            version = f" {self.install.version}" if self.install.version else ""
            self.game_card.set_status(
                f"{self.install.channel}{version} detected",
                Tone.SUCCESS,
            )

        if self.load_error:
            self.contract_card.set_status("Read failed", Tone.DANGER)
        elif contracts is None:
            self.contract_card.set_status("Not loaded", Tone.NEUTRAL)
        else:
            self.contract_card.set_status(
                f"{len(contracts.contracts):,} contracts ready",
                Tone.SUCCESS,
                f"{len(contracts.orgs):,} mission givers / {self.state.key_count:,} localization keys",
            )

        capability = next(
            (
                item
                for item in (contracts.capabilities if contracts else ())
                if item.provider == "local-dataforge-missions"
            ),
            None,
        )
        if capability is not None and capability.status is ProviderStatus.UNAVAILABLE:
            self.data_card.set_status("Provider unavailable", Tone.DANGER)
        elif capability is not None and capability.status is ProviderStatus.AVAILABLE:
            self.data_card.set_status("Provider ready", Tone.SUCCESS)
        elif capability is not None:
            self.data_card.set_status("Provider degraded", Tone.WARNING)
        elif contracts and any(not contract.reward.is_empty for contract in contracts.contracts):
            self.data_card.set_status("Reward facts ready", Tone.SUCCESS)
        else:
            self.data_card.set_status("Awaiting local mission facts", Tone.NEUTRAL)

        self.look_card.set_status(
            self.look.currentText(),
            Tone.INFO,
            "A quick title preset; detailed controls remain under Presentation.",
        )

        self.read_button.setText(
            "Read my game again" if self.state.contracts else "Read contracts from my game"
        )

        needs_language = self.install is not None and not self.install.language_configured
        self.language_warning.setVisible(needs_language)
        if needs_language:
            self.language_warning.setText(
                "Your game needs one setting before it will show custom text.\n"
                f"Add this line to {self.install.user_cfg.name} in your game folder:\n"
                f"    g_language = english"
            )

        self.go.setEnabled(
            self.install is not None
            and not self._busy
            and self.state.user_overrides_ready
        )
        self.read_button.setEnabled(self.install is not None and not self._busy)
        self.channel_selector.setEnabled(bool(self.installs) and not self._busy)
        self.discover_channels_button.setEnabled(not self._busy)
        self.undo.setEnabled(bool(self.state.backups()))
        self.footer.setText(
            self.operation_status
            or "Nothing is changed until you confirm, and a backup is always saved first."
        )

    def set_ui_preference_warning(self, message: str | None) -> None:
        self.preference_warning.setText(message or "")
        self.preference_warning.setVisible(bool(message))

    def game_status_text(self) -> str:
        if self.install is None:
            return (
                f"{WARN} Star Citizen was not found automatically.\n"
                f"Use 'Choose folder…' and pick your LIVE folder."
            )

        modified = " — already has custom text" if self.install.has_override else ""
        version = f" {self.install.version}" if self.install.version else ""
        return (
            f"{OK} Found Star Citizen {self.install.channel}{version}{modified}\n"
            f"{self.install.root}"
        )

    def contracts_status_text(self) -> str:
        """Whether the game's own contracts have been read yet."""
        if self.install is None:
            return ""
        if self.load_error:
            return f"{WARN} {self.load_error}"

        contracts = self.state.contracts
        if contracts is None:
            return (
                f"{TODO} Contracts not read yet. Press 'Read contracts from my "
                f"game' below — it takes about half a minute the first time."
            )

        return (
            f"{OK} Read {len(contracts.contracts):,} contracts from your game "
            f"files, across {len(contracts.orgs):,} mission givers."
        )

    def data_status_text(self) -> str:
        """Local enhancement-provider health and provenance coverage."""
        contracts = self.state.contracts
        with_rewards = (
            sum(1 for c in contracts.contracts if not c.reward.is_empty)
            if contracts
            else 0
        )
        capability = next(
            (
                item
                for item in (contracts.capabilities if contracts else ())
                if item.provider == "local-dataforge-missions"
            ),
            None,
        )
        if capability is not None and capability.status is ProviderStatus.UNAVAILABLE:
            reason = capability.diagnostics[0] if capability.diagnostics else "unsupported build"
            return f"{WARN} Local mission provider unavailable: {reason}"
        if capability is not None:
            label = "ready" if capability.status is ProviderStatus.AVAILABLE else "degraded"
            return (
                f"{OK if capability.status is ProviderStatus.AVAILABLE else WARN} "
                f"Local mission provider {label}: {with_rewards:,} contracts enhanced, "
                f"{capability.evidence_links:,} evidence links; "
                f"{capability.matched_facts:,}/{capability.reward_facts:,} reward facts matched."
            )
        if with_rewards:
            return f"{OK} Reward facts added for {with_rewards:,} contracts."
        return (
            f"{TODO} Local mission facts have not been read yet."
        )


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)
    return label


def _without_status_marker(text: str) -> str:
    for marker in (OK, TODO, WARN):
        if text.startswith(marker):
            return text[len(marker) :].lstrip()
    return text
