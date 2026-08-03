"""Templates tab: per-mission-giver template editing with a live preview.

The preview renders a real contract from the loaded data. A broken template is
expected during editing, so failures are reported in the preview pane and never
raised at the user.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
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

PLACEHOLDER = (
    "Using the built-in template.\n\n"
    "Type here to override it for this mission giver.\n"
    "Available: base, contract, org, reward, pools, opts, em()\n\n"
    "Example:\n"
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
        self.kind.addItem("Title", StringKind.TITLE)
        self.kind.addItem("Description", StringKind.DESC)
        self.kind.currentIndexChanged.connect(self._reload_editor)

        reset = QPushButton("Use built-in")
        reset.clicked.connect(self._reset)

        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Mission giver"))
        chooser.addWidget(self.org, 1)
        chooser.addWidget(QLabel("String"))
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

        editor_box = QGroupBox("Template")
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.addWidget(self.editor)

        preview_box = QGroupBox("Preview (real contract, as written to global.ini)")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(self.status)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(editor_box)
        splitter.addWidget(preview_box)
        splitter.setSizes([200, 300])

        layout = QVBoxLayout(self)
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
            self.status.setText("Load contract data to see a preview.")
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
            self.status.setText(f"Template error: {exc.cause}")
            return

        self.preview.setPlainText(value)
        self.status.setText(f"{key}  ({len(value)} characters)")
