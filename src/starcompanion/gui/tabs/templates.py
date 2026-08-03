"""Templates tab: per-mission-giver template editing with a live preview.

The preview renders a real contract from the loaded data. A broken template is
expected during editing, so failures are reported in the preview pane and never
raised at the user.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ...config import OrgTemplates
from ...model import StringKind
from ...render import TemplateRenderError
from ..state import AppState

INTRO = (
    "Most people never need this tab. It is for writing the contract wording "
    "yourself, one mission giver at a time, instead of using the settings on "
    "the other tabs."
)

EXPLAINER = (
    "Pick a mission giver, then type a pattern. Anything inside double braces "
    "is filled in when the text is written:\n"
    "\n"
    "    {{ base }}            the contract's own title\n"
    "    {{ org.name }}        who is offering it, e.g. Foxwell\n"
    "    {{ contract.rank }}   how hard it is, 1 to 6\n"
    "\n"
    "So typing this:\n"
    "    [{{ org.name }} {{ contract.rank }}] {{ base }}\n"
    "\n"
    "produces this:\n"
    "    [Foxwell 3] Orange Level Contract: Spring a Trap\n"
    "\n"
    "The box underneath shows a real contract from your game, updating as you "
    "type. Nothing is written to your game until you press 'Update my game', "
    "and 'Use the normal wording' puts this mission giver back."
)

PLACEHOLDER = (
    "Leave this empty to use the normal wording.\n"
    "\n"
    "Or type a pattern, for example:\n"
    "  [{{ org.name }} {{ contract.rank }}] {{ base }}"
)


class TemplatesTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False

        self.org = QComboBox()
        self.org.currentIndexChanged.connect(self._reload_editor)

        self.kind = QComboBox()
        self.kind.addItem("Contract title", StringKind.TITLE)
        self.kind.addItem("Contract description", StringKind.DESC)
        self.kind.currentIndexChanged.connect(self._reload_editor)

        reset = QPushButton("Use the normal wording")
        reset.clicked.connect(self._reset)

        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Mission giver"))
        chooser.addWidget(self.org, 1)
        chooser.addWidget(QLabel("Change the"))
        chooser.addWidget(self.kind)
        chooser.addWidget(reset)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(PLACEHOLDER)
        self.editor.textChanged.connect(self._on_edited)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        self.status = QLabel()
        self.status.setWordWrap(True)

        editor_box = QGroupBox("Your pattern")
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.addWidget(self.editor)

        preview_box = QGroupBox("What a real contract will say")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(self.status)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(editor_box)
        splitter.addWidget(preview_box)
        splitter.setSizes([200, 300])

        intro = QLabel(INTRO)
        intro.setWordWrap(True)

        self.explainer = QLabel(EXPLAINER)
        self.explainer.setWordWrap(True)
        self.explainer.setEnabled(False)
        self.explainer.setVisible(False)

        self.show_help = QCheckBox("Show me how this works")
        self.show_help.toggled.connect(self.explainer.setVisible)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.show_help)
        layout.addWidget(self.explainer)
        layout.addLayout(chooser)
        layout.addWidget(splitter, 1)

        state.contractsChanged.connect(self.refresh_orgs)
        state.profileChanged.connect(self.update_preview)
        self.refresh_orgs()

    # --- org list ------------------------------------------------------------

    def refresh_orgs(self) -> None:
        self._loading = True
        try:
            current = self.org.currentData()
            self.org.clear()
            for org_id in self.state.org_ids():
                self.org.addItem(self.state.contracts.orgs[org_id].name, org_id)
            if current:
                index = self.org.findData(current)
                if index >= 0:
                    self.org.setCurrentIndex(index)
        finally:
            self._loading = False
        self._reload_editor()

    @property
    def org_id(self) -> str | None:
        return self.org.currentData()

    @property
    def string_kind(self) -> StringKind:
        return self.kind.currentData() or StringKind.TITLE

    # --- editing -------------------------------------------------------------

    def _reload_editor(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            templates = self.state.profile.templates.get(self.org_id or "")
            source = getattr(templates, self.string_kind.value, None) if templates else None
            self.editor.setPlainText(source or "")
        finally:
            self._loading = False
        self.update_preview()

    def _on_edited(self) -> None:
        if self._loading:
            return
        self._store(self.editor.toPlainText().strip() or None)
        self.update_preview()

    def _reset(self) -> None:
        self._loading = True
        try:
            self.editor.setPlainText("")
        finally:
            self._loading = False
        self._store(None)
        self.update_preview()

    def _store(self, source: str | None) -> None:
        org_id = self.org_id
        if not org_id:
            return

        templates = dict(self.state.profile.templates)
        entry = templates.get(org_id) or OrgTemplates()
        entry = entry.model_copy(update={self.string_kind.value: source})

        if entry.title is None and entry.desc is None:
            templates.pop(org_id, None)
        else:
            templates[org_id] = entry

        self.state.profile.templates = templates
        self.state.touch_profile()

    # --- preview -------------------------------------------------------------

    def update_preview(self) -> None:
        contract = self.state.sample_contract(self.org_id)
        if contract is None:
            self.preview.setPlainText("")
            self.status.setText("Read your contracts on the Start tab to see a preview here.")
            return

        key = contract.key(self.string_kind)
        if key is None:
            self.preview.setPlainText("")
            self.status.setText(
                f"{contract.id} has no {self.string_kind.value} string to preview."
            )
            return

        try:
            value = self.state.profile.build_renderer().render_key(contract, key)
        except TemplateRenderError as exc:
            # Expected while typing; show it rather than interrupting.
            self.preview.setPlainText("")
            self.status.setText(f"That pattern is not valid yet: {exc.cause}")
            return

        self.preview.setPlainText(value)
        self.status.setText(f"{key}  ({len(value)} characters)")
