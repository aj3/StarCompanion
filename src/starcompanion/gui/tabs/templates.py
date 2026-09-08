"""Templates tab: per-mission-giver template editing with a live preview.

The preview renders a real contract from the loaded data. A broken template is
expected during editing, so failures are reported in the preview pane and never
raised at the user.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
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
from ..components import MetricTile, NoticeBanner, SectionCard, Tone
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
        self.setAccessibleName("Custom wording editor")
        self.setAccessibleDescription(
            "Advanced per-mission-giver template editor with a read-only live preview."
        )

        self.enable_advanced = QCheckBox("Enable advanced custom templates")
        self.enable_advanced.setAccessibleName("Enable advanced custom templates")
        self.enable_advanced.setAccessibleDescription(
            "Explicitly allow sandboxed Jinja templates stored in this output profile."
        )
        self.enable_advanced.toggled.connect(self._set_advanced_mode)

        self.advanced_notice = NoticeBanner(tone=Tone.INFO)

        self.org = QComboBox()
        self.org.setAccessibleName("Mission giver")
        self.org.setAccessibleDescription("Select which mission giver template to edit.")
        self.org.currentIndexChanged.connect(self._reload_editor)

        self.kind = QComboBox()
        self.kind.setAccessibleName("Contract string kind")
        self.kind.setAccessibleDescription("Choose title or description wording.")
        self.kind.addItem("Contract title", StringKind.TITLE)
        self.kind.addItem("Contract description", StringKind.DESC)
        self.kind.currentIndexChanged.connect(self._reload_editor)

        self.reset_button = QPushButton("Restore generated wording")
        self.reset_button.setAccessibleName("Restore generated wording")
        self.reset_button.setAccessibleDescription(
            "Remove this custom template and restore generated default wording."
        )
        self.reset_button.clicked.connect(self._reset)

        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Mission giver"))
        chooser.addWidget(self.org, 1)
        chooser.addWidget(QLabel("Text type"))
        chooser.addWidget(self.kind)
        chooser.addWidget(self.reset_button)

        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Custom wording pattern")
        self.editor.setAccessibleDescription(
            "Edit the Jinja template for the selected mission giver and string kind."
        )
        self.editor.setPlaceholderText(PLACEHOLDER)
        self.editor.textChanged.connect(self._on_edited)

        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("Rendered contract preview")
        self.preview.setAccessibleDescription(
            "Read-only preview generated from a real locally loaded contract."
        )
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        self.status = NoticeBanner(tone=Tone.INFO)

        self.editor_section = SectionCard(
            "Wording pattern",
            "Empty uses generated wording. Template syntax is validated live and never written directly.",
        )
        self.editor_section.add_widget(self.editor, 1)

        self.preview_section = SectionCard(
            "Rendered contract preview",
            "A read-only sample using the current profile and locally loaded contract data.",
        )
        self.preview_section.add_widget(self.preview, 1)
        self.preview_section.add_widget(self.status)

        # Start narrow so the page has a small minimum width; resizeEvent
        # promotes this to the side-by-side desktop layout when space permits.
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.editor_section)
        self.splitter.addWidget(self.preview_section)
        self.splitter.setSizes([320, 320])
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setMinimumHeight(640)

        intro = NoticeBanner(INTRO, tone=Tone.INFO)

        self.explainer = QLabel(EXPLAINER)
        self.explainer.setWordWrap(True)
        self.explainer.setEnabled(False)
        self.explainer.setVisible(False)

        self.show_help = QCheckBox("Show me how this works")
        self.show_help.setAccessibleName("Show custom-wording help")
        self.show_help.setAccessibleDescription("Reveal template syntax examples.")
        self.show_help.toggled.connect(self.explainer.setVisible)

        self.override_metric = MetricTile("Custom scopes")
        self.selection_metric = MetricTile("Selected wording")
        self.preview_metric = MetricTile("Preview length")
        metrics = QGridLayout()
        metrics.setSpacing(8)
        metrics.addWidget(self.override_metric, 0, 0)
        metrics.addWidget(self.selection_metric, 0, 1)
        metrics.addWidget(self.preview_metric, 0, 2)

        self.context_section = SectionCard(
            "Wording context",
            "Custom wording is scoped to one mission giver and title or description. "
            "Other contracts continue using the profile's generated wording.",
        )
        self.context_section.add_widget(self.enable_advanced)
        self.context_section.add_widget(self.advanced_notice)
        self.context_section.add_layout(chooser)
        self.context_section.add_widget(self.show_help)
        self.context_section.add_widget(self.explainer)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(metrics)
        layout.addWidget(self.context_section)
        layout.addWidget(self.splitter, 1)

        focus_order = [
            self.enable_advanced,
            self.show_help,
            self.org,
            self.kind,
            self.reset_button,
            self.editor,
            self.preview,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.contractsChanged.connect(self.refresh_orgs)
        state.profileChanged.connect(self.update_preview)
        self.refresh_orgs()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Use the shell width, not this page's current size hint: the wide
        # splitter itself can otherwise keep a narrow viewport artificially wide.
        wide = self.window().width() >= 1180
        orientation = (
            Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical
        )
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setMinimumHeight(300 if wide else 640)
            self.splitter.setSizes([480, 520] if wide else [320, 320])
        super().resizeEvent(event)

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

    def _set_advanced_mode(self, enabled: bool) -> None:
        if self._loading:
            return
        self.state.profile.wording.mode = "advanced" if enabled else "structured"
        self.state.touch_profile()

    def _apply_mode(self) -> bool:
        advanced = self.state.profile.wording.mode == "advanced"
        self.enable_advanced.blockSignals(True)
        self.enable_advanced.setChecked(advanced)
        self.enable_advanced.blockSignals(False)
        self.editor.setEnabled(advanced)
        self.reset_button.setEnabled(advanced)
        if advanced:
            self.advanced_notice.set_tone(Tone.WARNING)
            self.advanced_notice.setText(
                "Advanced mode is active. Templates are sandboxed and validated, but can override structured wording."
            )
        else:
            self.advanced_notice.set_tone(Tone.INFO)
            self.advanced_notice.setText(
                "Structured mode is active. Stored templates remain inactive until you explicitly enable them."
            )
        return advanced

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
        advanced = self._apply_mode()
        custom_count = sum(
            int(entry.title is not None) + int(entry.desc is not None)
            for entry in self.state.profile.templates.values()
        )
        self.override_metric.set_value(
            f"{custom_count:,}",
            "mission-giver/title scopes",
        )
        selected_templates = self.state.profile.templates.get(self.org_id or "")
        selected_source = (
            getattr(selected_templates, self.string_kind.value, None)
            if selected_templates
            else None
        )
        self.selection_metric.set_value(
            "Custom"
            if selected_source and advanced
            else "Stored (inactive)"
            if selected_source
            else "Generated",
            self.kind.currentText().lower(),
        )
        contract = self.state.sample_contract(self.org_id)
        if contract is None:
            self.preview.setPlainText("")
            self.preview_metric.set_value("—", "contract data not loaded")
            self.status.set_tone(Tone.INFO)
            self.status.setText("Read local contracts on Overview to activate this preview.")
            return

        key = contract.key(self.string_kind)
        if key is None:
            self.preview.setPlainText("")
            self.preview_metric.set_value("—", "no matching localization key")
            self.status.set_tone(Tone.WARNING)
            self.status.setText(
                f"{contract.id} has no {self.string_kind.value} string to preview."
            )
            return

        try:
            value = self.state.profile.build_renderer().render_key(contract, key)
        except TemplateRenderError as exc:
            # Expected while typing; show it rather than interrupting.
            self.preview.setPlainText("")
            self.preview_metric.set_value("Invalid", "nothing can be applied")
            self.status.set_tone(Tone.DANGER)
            self.status.setText(f"That pattern is not valid yet: {exc.cause}")
            return

        self.preview.setPlainText(value)
        self.preview_metric.set_value(f"{len(value):,}", "characters")
        self.status.set_tone(Tone.SUCCESS)
        source = (
            "custom pattern"
            if selected_source and advanced
            else "generated profile wording"
        )
        self.status.setText(f"Preview ready from {source}: {key}")
