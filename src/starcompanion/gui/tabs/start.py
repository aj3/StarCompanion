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

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
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
from ...game_files import (
    GameFilePlan,
    apply_game_file_plan,
    plan_language_activation,
    plan_restore_stock,
)
from ..labels import PREFIX_CAPTION, TITLE_PREFIXES
from ...model import ProviderStatus
from ...operations import PreparedUpdate, prepare_update, read_contracts
from ...sources import contracts_ini
from ...tasks import ProgressEvent
from ...transactions import TransactionJournal
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


@dataclass(frozen=True)
class GameScopeSnapshot:
    contracts: object | None
    configured_language: str | None
    override_present: bool


class StartTab(QWidget):
    recoveryRequested = Signal()
    languageChanged = Signal(str)
    scopeStatusChanged = Signal()

    def __init__(
        self,
        state: AppState,
        parent: QWidget | None = None,
        *,
        language: str = "english",
    ):
        super().__init__(parent)
        self.state = state
        self.install: installs.GameInstall | None = None
        self.installs: list[installs.GameInstall] = []
        self.load_error: str | None = None
        self.operation_status: str | None = None
        self.selection_status: str | None = None
        self.selected_language = installs.normalize_language(language)
        self.available_languages: tuple[str, ...] = (self.selected_language,)
        self.verified_languages: tuple[str, ...] = ()
        self.active_language: str | None = None
        self.override_present = False
        self._jobs: set[QtOperationJob] = set()
        self._busy = False
        self._shutting_down = False
        self._pending_cache_install: tuple[installs.GameInstall, str] | None = None
        self._pending_operation_after = None
        self._pending_prepared: PreparedUpdate | None = None
        self._pending_game_file_plan: tuple[GameFilePlan, Path, TransactionJournal] | None = None
        self._contracts_install_key: tuple[str, str] | None = None
        self._discovery_timer = QTimer(self)
        self._discovery_timer.setSingleShot(True)
        self._discovery_timer.timeout.connect(self.detect_game)
        self._continuation_timer = QTimer(self)
        self._continuation_timer.setSingleShot(True)
        self._continuation_timer.timeout.connect(self._run_pending_continuation)

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
        self.game_step = self._build_game_step()
        self.contract_step = self._build_contract_step()
        cards.addWidget(self.game_step, 0, 0)
        cards.addWidget(self.contract_step, 0, 1)
        self.data_step = self._build_data_step()
        cards.addWidget(self.data_step, 1, 0)
        self.look_step = self._build_look_step()
        cards.addWidget(self.look_step, 1, 1)
        layout.addLayout(cards)

        layout.addStretch(1)

        focus_order = [
            self.go,
            self.undo,
            self.channel_selector,
            self.language_selector,
            self.discover_languages_button,
            self.activate_language_button,
            self.restore_stock_button,
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
        self._discovery_timer.start(0)
        self.refresh()

    def set_simple_mode(self, enabled: bool) -> None:
        """Show only the existing safe Update and Undo workflow."""

        for card in (
            self.game_step,
            self.contract_step,
            self.data_step,
            self.look_step,
        ):
            card.setVisible(not enabled)

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

        self.language_selector = QComboBox()
        self.language_selector.setAccessibleName("Installed game language")
        self.language_selector.setAccessibleDescription(
            "Select one language from the current channel; cache, user wording, and game override remain isolated to it."
        )
        self.language_selector.currentIndexChanged.connect(self._language_selected)
        card.add_widget(self.language_selector)

        self.discover_languages_button = QPushButton("Discover installed languages")
        self.discover_languages_button.setAccessibleName("Discover installed languages")
        self.discover_languages_button.setAccessibleDescription(
            "Read only the archive index in a background worker and list installed localization languages."
        )
        self.discover_languages_button.clicked.connect(self.discover_languages)
        self.activate_language_button = QPushButton("Activate selected language…")
        self.activate_language_button.setAccessibleName("Activate selected game language")
        self.activate_language_button.setAccessibleDescription(
            "Preview and confirm a fingerprint-bound USER.cfg change that preserves unrelated settings."
        )
        self.activate_language_button.clicked.connect(self.activate_selected_language)
        self.restore_stock_button = QPushButton("Restore stock localization…")
        self.restore_stock_button.setProperty("role", "danger")
        self.restore_stock_button.setAccessibleName("Restore selected localization to stock")
        self.restore_stock_button.setAccessibleDescription(
            "Back up and remove only the selected language's loose global.ini after confirmation."
        )
        self.restore_stock_button.clicked.connect(self.restore_stock_localization)

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
        self.find_game_button.clicked.connect(self.discover_channels)
        self.choose_game_button = QPushButton("Choose folder…")
        self.choose_game_button.setAccessibleDescription(
            "Choose a channel folder containing Data.p4k."
        )
        self.choose_game_button.clicked.connect(self.choose_game)
        card.add_action(self.discover_channels_button)
        card.add_action(self.discover_languages_button)
        card.add_action(self.activate_language_button)
        card.add_action(self.restore_stock_button)
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
        """Compatibility entry point for background-only installation discovery."""

        self.discover_channels()

    @staticmethod
    def _install_key(install: installs.GameInstall | None) -> str | None:
        if install is None:
            return None
        return os.path.normcase(os.path.abspath(install.root))

    def _selection_key(
        self,
        install: installs.GameInstall | None = None,
        language: str | None = None,
    ) -> tuple[str, str] | None:
        selected = install or self.install
        key = self._install_key(selected)
        if key is None:
            return None
        return key, installs.normalize_language(language or self.selected_language)

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
        unique = {
            self._install_key(item): item for item in found if item is not None
        }
        self.installs = sorted(unique.values(), key=installs.install_rank)
        previous = self._install_key(self.install)
        self.channel_selector.blockSignals(True)
        self.channel_selector.clear()
        for item in self.installs:
            self.channel_selector.addItem(item.label, item)
            item_index = self.channel_selector.count() - 1
            self.channel_selector.setItemData(
                item_index,
                installs.selection_evidence(item, self.installs),
                Qt.ItemDataRole.ToolTipRole,
            )
        selected = next(
            (
                index
                for index, item in enumerate(self.installs)
                if previous is not None and self._install_key(item) == previous
            ),
            0 if self.installs else -1,
        )
        self.channel_selector.setCurrentIndex(selected)
        self.channel_selector.blockSignals(False)
        self.install = self.installs[selected] if selected >= 0 else None
        if previous != self._install_key(self.install):
            self.verified_languages = ()
        retained = previous is not None and self._install_key(self.install) == previous
        self.selection_status = (
            f"Retained your previous selection. {self.install.freshness_evidence}"
            if retained and self.install is not None
            else installs.selection_evidence(self.install, self.installs)
            if self.install is not None
            else None
        )
        self._adopt_install()
        self.operation_status = (
            f"Discovered {len(self.installs):,} install candidate(s). "
            f"{self.selection_status}"
            if self.installs
            else "No installed channels were found automatically. Choose a folder to continue."
        )
        self.refresh()

    def _channel_selected(self, index: int) -> None:
        item = self.channel_selector.itemData(index)
        if not isinstance(item, installs.GameInstall):
            return
        self.install = item
        self.verified_languages = ()
        self.selection_status = f"Selected explicitly. {item.freshness_evidence}"
        self._adopt_install()
        self.operation_status = (
            f"{self.selection_status} "
            "Channel-scoped state is loading."
        )
        self.refresh()

    def _set_language_options(self, languages) -> None:
        normalized = []
        for value in languages:
            try:
                value = installs.normalize_language(value)
            except ValueError:
                continue
            if value not in normalized:
                normalized.append(value)
        if self.selected_language not in normalized:
            normalized.append(self.selected_language)
        if "english" not in normalized:
            normalized.append("english")
        self.available_languages = tuple(sorted(normalized))
        self.language_selector.blockSignals(True)
        self.language_selector.clear()
        for value in self.available_languages:
            self.language_selector.addItem(value.replace("_", " ").title(), value)
        self.language_selector.setCurrentIndex(
            max(0, self.language_selector.findData(self.selected_language))
        )
        self.language_selector.blockSignals(False)

    def set_selected_language(self, language: str) -> None:
        """Select a normalized language through the ordinary scope-change path."""

        selected = installs.normalize_language(language)
        self._set_language_options((*self.available_languages, selected))
        self.language_selector.setCurrentIndex(
            self.language_selector.findData(selected)
        )

    def discover_languages(self) -> None:
        install = self.install
        if install is None:
            return
        self._run_operation(
            "Reading installed languages from the local archive…",
            lambda token, _reporter: self._discover_languages(install, token),
            on_success=lambda values: self._languages_discovered(install, values),
            on_failure=lambda exc: QMessageBox.warning(
                self, "Could not discover installed languages", str(exc)
            ),
        )

    @staticmethod
    def _discover_languages(install: installs.GameInstall, token) -> tuple[str, ...]:
        token.checkpoint()
        values = install.languages()
        token.checkpoint()
        return values

    def _languages_discovered(
        self,
        install: installs.GameInstall,
        values: tuple[str, ...],
    ) -> None:
        if self._install_key(install) != self._install_key(self.install):
            return
        self._set_language_options(values)
        self.verified_languages = tuple(
            installs.normalize_language(value) for value in values
        )
        self.operation_status = f"Found {len(values):,} installed language(s)."
        self.refresh()

    def _language_selected(self, index: int) -> None:
        value = self.language_selector.itemData(index)
        if not isinstance(value, str):
            return
        selected = installs.normalize_language(value)
        if selected == self.selected_language:
            return
        self.selected_language = selected
        self.languageChanged.emit(selected)
        self._adopt_install()
        self.operation_status = (
            f"Selected {selected}. Language-scoped cache and user wording are loading."
        )
        self.refresh()
        self.scopeStatusChanged.emit()

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
        """Publish the path now and queue cache I/O on a worker."""

        if self.install is None:
            self._pending_cache_install = None
            self._contracts_install_key = None
            self.state.set_target(None)
            self.state.set_contracts(None)
            return

        self._set_language_options(self.available_languages)
        selected_key = self._selection_key()
        if selected_key != self._contracts_install_key:
            self.state.set_contracts(None)
            self.active_language = None
            self.override_present = False
        self.state.set_target(self.install.localization(self.selected_language))
        self._pending_cache_install = (self.install, self.selected_language)
        if not self._jobs:
            self._start_pending_cache_load()

    def _start_pending_cache_load(self) -> None:
        if self._shutting_down or self._jobs or self._pending_cache_install is None:
            return
        install, language = self._pending_cache_install
        self._pending_cache_install = None
        job = QtOperationJob(
            lambda token, _reporter: (
                token.checkpoint(),
                GameScopeSnapshot(
                    store.load(install, language),
                    install.configured_language,
                    install.localization(language).is_file(),
                ),
                token.checkpoint(),
            )[1],
            self,
        )
        self._jobs.add(job)
        self._busy = True
        self.operation_status = (
            f"Loading the {install.channel}/{language} local cache in the background…"
        )
        self.refresh()
        job.succeeded.connect(
            lambda snapshot: self._cache_loaded(install, language, snapshot)
        )
        job.failed.connect(
            lambda exc: self._cache_load_failed(install, language, exc)
        )
        job.cancelled.connect(
            lambda: setattr(
                self, "operation_status", "Cache loading cancelled safely."
            )
        )
        job.finished.connect(lambda: self._operation_finished(job, None))
        job.start()

    def _cache_loaded(
        self,
        install: installs.GameInstall,
        language: str,
        snapshot: GameScopeSnapshot,
    ) -> None:
        if self._selection_key(install, language) != self._selection_key():
            return
        self.active_language = snapshot.configured_language
        self.override_present = snapshot.override_present
        self._set_language_options(
            (*self.available_languages, *((snapshot.configured_language,) if snapshot.configured_language else ()))
        )
        if snapshot.contracts is not None:
            self.state.set_contracts(snapshot.contracts)
            self._contracts_install_key = self._selection_key(install, language)
            self.load_error = None
            self.operation_status = (
                f"Loaded the verified {install.channel}/{language} contract cache in the background."
            )
        else:
            self.operation_status = (
                f"No current {install.channel} cache was found. Read contracts to build it."
            )
        self.scopeStatusChanged.emit()

    def _cache_load_failed(
        self,
        install: installs.GameInstall,
        language: str,
        exc: Exception,
    ) -> None:
        if self._selection_key(install, language) != self._selection_key():
            return
        self.load_error = str(exc)
        self.operation_status = f"The {install.channel} cache could not be loaded safely: {exc}"

    def read_game(self, *, force: bool = False, after=None) -> None:
        """Read contracts from the archive, with progress, and cache them.

        `force` re-reads even when a cached copy exists, which is what the
        button does: if someone presses it deliberately, they want the archive
        looked at again.
        """
        if self.install is None:
            return

        install = self.install
        language = self.selected_language

        def loaded(contracts) -> None:
            if self._selection_key(install, language) != self._selection_key():
                return
            self.load_error = None
            self.state.set_contracts(contracts)
            self._contracts_install_key = self._selection_key(install, language)
            self._pending_operation_after = after
            if after is not None:
                self._continuation_timer.start(0)
            self.refresh()

        def failed(exc: Exception) -> None:
            self.load_error = str(exc)
            QMessageBox.warning(self, "Could not read your game", str(exc))

        self._run_operation(
            "Reading your game files…",
            lambda token, reporter: self._load_or_read_contracts(
                install,
                language,
                force=force,
                token=token,
                reporter=reporter,
            ),
            on_success=loaded,
            on_failure=failed,
        )

    @staticmethod
    def _load_or_read_contracts(
        install: installs.GameInstall,
        language: str,
        *,
        force: bool,
        token,
        reporter,
    ):
        token.checkpoint()
        if not force:
            cached = store.load(install, language)
            token.checkpoint()
            if cached is not None:
                return cached
        contracts = read_contracts(
            install,
            language=language,
            token=token,
            reporter=reporter,
        )
        token.checkpoint()
        store.save(install, contracts, language)
        token.checkpoint()
        return contracts

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

        source = Path(chosen)
        install_key = self._install_key(self.install)

        def loaded(contracts) -> None:
            if install_key != self._install_key(self.install):
                return
            self.state.set_contracts(contracts)
            self._contracts_install_key = install_key

        self._run_operation(
            "Reading the selected contract list…",
            lambda token, _reporter: (
                token.checkpoint(),
                contracts_ini.load(source),
                token.checkpoint(),
            )[1],
            on_success=loaded,
            on_failure=lambda exc: QMessageBox.warning(
                self,
                "Could not read that file",
                f"{exc}\n\nIt should be the contracts.ini from StarStrings.",
            ),
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

    def activate_selected_language(self) -> None:
        install = self.install
        language = self.selected_language
        if install is None:
            return
        if language not in self.verified_languages:
            QMessageBox.information(
                self,
                "Verify installed languages first",
                "Use Discover installed languages before changing USER.cfg. "
                "StarCompanion will not activate an unverified archive language.",
            )
            return
        self._run_operation(
            "Preparing a safe USER.cfg language plan…",
            lambda token, _reporter: (
                token.checkpoint(),
                plan_language_activation(install, language),
                token.checkpoint(),
            )[1],
            on_success=lambda plan: self._confirm_game_file_plan(
                install, language, plan
            ),
            on_failure=lambda exc: QMessageBox.warning(
                self, "Could not prepare language activation", str(exc)
            ),
        )

    def restore_stock_localization(self) -> None:
        install = self.install
        language = self.selected_language
        if install is None:
            return
        if language not in self.verified_languages:
            QMessageBox.information(
                self,
                "Verify installed languages first",
                "Use Discover installed languages before restoring stock. "
                "StarCompanion will not remove an override unless matching stock "
                "localization is verified in the archive.",
            )
            return
        self._run_operation(
            "Preparing a restore-to-stock plan…",
            lambda token, _reporter: (
                token.checkpoint(),
                plan_restore_stock(install, language),
                token.checkpoint(),
            )[1],
            on_success=lambda plan: self._confirm_game_file_plan(
                install, language, plan
            ),
            on_failure=lambda exc: QMessageBox.warning(
                self, "Could not prepare restore to stock", str(exc)
            ),
        )

    def _game_file_journal(
        self,
        plan: GameFilePlan,
        backup_dir: Path,
    ) -> TransactionJournal:
        if plan.target.name.casefold() != "user.cfg":
            return self._journal()
        return TransactionJournal(
            backup_dir / ".usercfg-journal.json",
            backup_dir / "last-usercfg-operation.json",
        )

    def _confirm_game_file_plan(
        self,
        install: installs.GameInstall,
        language: str,
        plan: GameFilePlan,
    ) -> None:
        if self._selection_key(install, language) != self._selection_key():
            self.operation_status = "The selected channel or language changed; the plan was discarded."
            self.refresh()
            return
        if not plan.changed:
            QMessageBox.information(
                self,
                "Nothing to change",
                "The selected game control file already matches this reviewed outcome.",
            )
            return
        if plan.operation == "activate-language":
            detail = (
                f"Language: {language}\nEncoding: {plan.encoding}\n"
                f"Line endings: {plan.newline}\n\n{plan.summary}"
            )
            title = "Activate the selected game language?"
        else:
            detail = (
                f"Language: {language}\nTarget: {plan.target.name}\n\n"
                f"{plan.summary}\nA verified backup is created first."
            )
            title = "Restore stock localization?"
        if QMessageBox.question(
            self,
            title,
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        backup_dir = (
            install.root / "backups"
            if plan.target.name.casefold() == "user.cfg"
            else self._backup_directory()
        )
        self._pending_game_file_plan = (
            plan,
            backup_dir,
            self._game_file_journal(plan, backup_dir),
        )
        self._pending_operation_after = self._commit_pending_game_file_plan
        self._continuation_timer.start(0)

    def _commit_pending_game_file_plan(self) -> None:
        pending = self._pending_game_file_plan
        self._pending_game_file_plan = None
        if pending is None:
            return
        plan, backup_dir, journal = pending
        self._run_operation(
            "Applying the reviewed game-file plan…",
            lambda token, _reporter: (
                token.checkpoint(),
                apply_game_file_plan(
                    plan,
                    confirmed=True,
                    backup_dir=backup_dir,
                    journal=journal,
                    backup_retention=self.state.profile.injection.backup_retention,
                ),
            )[1],
            on_success=lambda result: self._game_file_plan_applied(plan, result),
            on_failure=lambda exc: QMessageBox.critical(
                self,
                "Could not apply the game-file plan",
                f"{exc}\n\nUnknown or externally changed state was left untouched.",
            ),
        )

    def _game_file_plan_applied(self, plan: GameFilePlan, _result) -> None:
        if plan.operation == "activate-language":
            self.active_language = self.selected_language
        else:
            self.override_present = False
        self.operation_status = (
            "Selected game language activated safely."
            if plan.operation == "activate-language"
            else "Loose localization override removed; the game will load stock archive data."
        )
        self.refresh()
        self.scopeStatusChanged.emit()
        QMessageBox.information(self, "Done", self.operation_status)

    def _confirm_prepared_update(self, prepared: PreparedUpdate) -> None:
        if not self._use_prepared_update(prepared):
            prepared.cleanup()

    def _use_prepared_update(self, prepared: PreparedUpdate) -> bool:
        result = prepared.plan

        if not (result.added or result.updated or result.removed):
            QMessageBox.information(
                self, "Nothing to change",
                "Your game text already matches these settings.",
            )
            return False

        recovery = self._journal().inspect(prepared.localization.target)
        if recovery.status != "clean":
            QMessageBox.warning(
                self,
                "Recovery must be reviewed first",
                f"{recovery.status}: {recovery.message}\n\n"
                "Open Backup & recovery to resolve this without overwriting unknown state.",
            )
            self.recoveryRequested.emit()
            return False

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
            return False

        # The prepare result arrives before that worker's finished signal. Queue
        # the write so it starts only after the first thread has fully stopped.
        self._pending_prepared = prepared
        self._pending_operation_after = self._commit_pending_prepared
        self._continuation_timer.start(0)
        return True

    def _commit_pending_prepared(self) -> None:
        prepared = self._pending_prepared
        self._pending_prepared = None
        if prepared is None:
            return
        backup_dir = self.state.backup_dir
        journal = self._journal()
        backup_retention = self.state.profile.injection.backup_retention

        def commit(token, _reporter):
            try:
                token.checkpoint()
                return prepared.commit(
                    confirmed=True,
                    backup_dir=backup_dir,
                    journal=journal,
                    backup_retention=backup_retention,
                )
            finally:
                prepared.cleanup()

        job = self._run_operation(
            "Applying the verified update safely…",
            commit,
            on_success=self._prepared_update_committed,
            on_failure=lambda exc: QMessageBox.critical(
                self,
                "Could not update your game",
                f"{exc}\n\nYour game text was not changed.",
            ),
        )
        if job is None:
            prepared.cleanup()
            QMessageBox.critical(
                self,
                "Could not start the update",
                "Another game-file operation is still active. Nothing was changed.",
            )

    def _prepared_update_committed(self, written) -> None:
        self.override_present = True
        self.refresh()
        self.scopeStatusChanged.emit()
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
        if self._shutting_down or self._jobs:
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
        job.succeeded.connect(
            lambda value: (
                dialog.close(),
                None if self._shutting_down else on_success(value),
            )
        )
        job.failed.connect(
            lambda exc: (
                dialog.close(),
                None if self._shutting_down else on_failure(exc),
            )
        )
        job.cancelled.connect(
            lambda: (
                dialog.close(),
                None
                if self._shutting_down
                else setattr(
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
        dialog: QProgressDialog | None,
    ) -> None:
        if dialog is not None:
            dialog.close()
        self._jobs.discard(job)
        job.deleteLater()
        self._busy = bool(self._jobs)
        self.refresh()
        if self._shutting_down:
            return
        if not self._jobs and self._pending_cache_install is not None:
            self._start_pending_cache_load()
            return
        if self._pending_operation_after is not None:
            self._continuation_timer.start(0)

    def _run_pending_continuation(self) -> None:
        if self._shutting_down:
            return
        if self._jobs:
            self._continuation_timer.start(10)
            return
        after = self._pending_operation_after
        self._pending_operation_after = None
        if after is not None:
            after()

    def wait_for_jobs(self, timeout_ms: int = 5000) -> bool:
        """Pump queued results while waiting; intended for tests and smoke tools."""
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            QApplication.processEvents()
            for job in list(self._jobs):
                job.wait(5)
            pending = bool(
                self._jobs
                or self._pending_cache_install is not None
                or self._pending_operation_after is not None
                or self._discovery_timer.isActive()
                or self._continuation_timer.isActive()
            )
            if not pending:
                QApplication.processEvents()
                if not self._jobs and self._pending_operation_after is None:
                    return True
            time.sleep(0.001)
        return False

    def shutdown_jobs(self) -> None:
        """Cancel and visibly join workers before their window disappears."""
        self._shutting_down = True
        self._discovery_timer.stop()
        self._continuation_timer.stop()
        self._pending_cache_install = None
        self._pending_game_file_plan = None
        pending_prepared = self._pending_prepared
        self._pending_prepared = None
        self._pending_operation_after = None
        if pending_prepared is not None:
            pending_prepared.cleanup()
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
        self._pending_cache_install = None
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

        self.operation_status = (
            "Choose the reviewed restore point in Backup & recovery. "
            "The current file will be backed up before restoration."
        )
        self.recoveryRequested.emit()

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

        configured_language = self.active_language
        needs_language = (
            self.install is not None
            and configured_language != self.selected_language
        )
        self.language_warning.setVisible(needs_language)
        if needs_language:
            verification = (
                "Use Activate selected language to preview a safe, backup-first change."
                if self.selected_language in self.verified_languages
                else "Discover installed languages before activation; unverified names cannot be written."
            )
            self.language_warning.setText(
                f"Selected localization: {self.selected_language}. "
                f"Active g_language in USER.cfg: {configured_language or 'not configured'}.\n"
                + verification
            )

        self.go.setEnabled(
            self.install is not None
            and not self._busy
            and self.state.user_overrides_ready
        )
        self.read_button.setEnabled(self.install is not None and not self._busy)
        self.channel_selector.setEnabled(bool(self.installs) and not self._busy)
        self.language_selector.setEnabled(self.install is not None and not self._busy)
        self.discover_channels_button.setEnabled(not self._busy)
        self.discover_languages_button.setEnabled(
            self.install is not None and not self._busy
        )
        self.activate_language_button.setEnabled(
            self.install is not None and needs_language and not self._busy
            and self.selected_language in self.verified_languages
        )
        self.restore_stock_button.setEnabled(
            self.install is not None
            and self.state.target is not None
            and self.override_present
            and self.selected_language in self.verified_languages
            and not self._busy
        )
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

        modified = " — selected language has custom text" if self.override_present else ""
        version = f" {self.install.version}" if self.install.version else ""
        active = self.active_language or "not configured"
        return (
            f"{OK} Found Star Citizen {self.install.channel}{version}{modified}\n"
            f"{self.install.root}\n"
            f"Selected localization: {self.selected_language}; active game language: {active}.\n"
            f"{self.selection_status or installs.selection_evidence(self.install, self.installs or [self.install])}"
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
