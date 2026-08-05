"""Appearance tab: how the added information looks.

Everything here is phrased for someone who has never opened `global.ini`. The
game's tag names (`EM4`, `b`, …) are an implementation detail and never appear
on screen -- see `gui/labels.py`.

The per-field styling is real but rarely wanted, so it starts collapsed rather
than presenting seven dropdowns to someone who just wants it to look sensible.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..components import MetricTile, NoticeBanner, SectionCard, Tone
from ..labels import (
    FIELD_NAMES,
    INHERIT,
    PREFIX_CAPTION,
    STYLE_CAPTION,
    TEXT_STYLES,
    TITLE_PREFIXES,
    can_preview,
    preview_html,
    preview_note,
)
from ..state import AppState
from ..theme import SPACING


class FormattingTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        self.setAccessibleName("Presentation settings")
        self.setAccessibleDescription(
            "Control title structure, emphasis, and blueprint-list length."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.large)

        metrics = QGridLayout()
        metrics.setSpacing(SPACING.medium)
        self.style_metric = MetricTile("Text style")
        self.prefix_metric = MetricTile("Title prefix")
        self.length_metric = MetricTile("Blueprint list")
        metrics.addWidget(self.style_metric, 0, 0)
        metrics.addWidget(self.prefix_metric, 0, 1)
        metrics.addWidget(self.length_metric, 0, 2)
        for column in range(3):
            metrics.setColumnStretch(column, 1)
        layout.addLayout(metrics)

        sections = QVBoxLayout()
        sections.setSpacing(SPACING.large)
        self.style_section = self._build_style_box()
        self.title_section = self._build_title_box()
        sections.addWidget(self.style_section)
        sections.addWidget(self.title_section)
        self.length_box = self._build_length_box()
        sections.addWidget(self.length_box)
        layout.addLayout(sections)
        layout.addStretch(1)

        focus_order = [
            self.default_tag,
            self.per_field_toggle,
            *self.field_tags.values(),
            self.prefix,
            self.bracket_rep,
            self.bracket_bp,
            self.max_items,
        ]
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        state.profileChanged.connect(self.refresh)
        self.refresh()

    # --- text style ----------------------------------------------------------

    def _build_style_box(self) -> SectionCard:
        box = SectionCard(
            "Text emphasis",
            "Choose a game-supported emphasis style for generated mission facts.",
        )
        layout = box.body_layout

        self.default_tag = QComboBox()
        self.default_tag.setAccessibleName("Default generated-text style")
        self.default_tag.setAccessibleDescription(
            "Applies to generated information unless a field-specific style overrides it."
        )
        for tag, name, _hint in TEXT_STYLES:
            self.default_tag.addItem(name, tag)
        self.default_tag.currentIndexChanged.connect(self._set_default_style)

        form = QFormLayout()
        form.addRow("Style", self.default_tag)
        layout.addLayout(form)

        # A worked example beats a name: "Highlight 4" means nothing on its own.
        self.example = QLabel()
        self.example.setTextFormat(Qt.TextFormat.RichText)
        self.example.setWordWrap(True)
        self.example.setFrameShape(QFrame.Shape.StyledPanel)
        self.example.setMargin(10)
        self.example.setProperty("component", "preview")
        self.example.setAccessibleName("Generated text example")
        layout.addWidget(self.example)

        self.example_note = _muted("")
        layout.addWidget(self.example_note)
        layout.addWidget(_muted(STYLE_CAPTION))

        self.per_field_toggle = QCheckBox(
            "Use a different style for each kind of information"
        )
        self.per_field_toggle.setAccessibleName(
            "Use field-specific generated-text styles"
        )
        self.per_field_toggle.setAccessibleDescription(
            "Reveals one optional style selector for each generated field."
        )
        self.per_field_toggle.toggled.connect(self._toggle_per_field)
        layout.addWidget(self.per_field_toggle)

        self.per_field = QWidget()
        per_field_form = QFormLayout(self.per_field)
        per_field_form.setContentsMargins(24, 0, 0, 0)

        self.field_tags: dict[str, QComboBox] = {}
        for name, label in FIELD_NAMES.items():
            combo = QComboBox()
            combo.setAccessibleName(f"{label} style")
            combo.setAccessibleDescription(
                "Use the default style or override this generated field only."
            )
            combo.addItem(INHERIT, None)
            for tag, shown, _hint in TEXT_STYLES:
                combo.addItem(shown, tag)
            combo.currentIndexChanged.connect(
                lambda _index, field=name: self._set_field_style(field)
            )
            self.field_tags[name] = combo
            per_field_form.addRow(label, combo)

        self.per_field.setVisible(False)
        layout.addWidget(self.per_field)

        return box

    def _set_default_style(self, index: int) -> None:
        self._update_example(self.default_tag.itemData(index))
        if self._loading:
            return
        self.state.profile.formatting.emphasis = self.default_tag.itemData(index)
        self.state.touch_profile()

    def _update_example(self, tag: str | None) -> None:
        """Show the chosen style, but only when that can be done truthfully."""
        if not tag:
            return

        self.example_note.setText(preview_note(tag))

        if not can_preview(tag):
            # Four identical boxes would suggest the four styles are the same.
            self.example.setVisible(False)
            return

        styled = preview_html(tag, "Reputation Awarded: 250")
        self.example.setVisible(True)
        self.example.setText(
            "A contract description would read:<br><br>"
            "Deal with the outlaws at Shubin Mining Facility.<br><br>"
            f"{styled}"
        )

    def _toggle_per_field(self, checked: bool) -> None:
        self.per_field.setVisible(checked)
        if self._loading or checked:
            return

        # Unticking means "just use the one style everywhere".
        if self.state.profile.formatting.by_field:
            self.state.profile.formatting.by_field = {}
            self.state.touch_profile()
            self.refresh()

    def _set_field_style(self, field: str) -> None:
        if self._loading:
            return

        by_field = dict(self.state.profile.formatting.by_field)
        tag = self.field_tags[field].currentData()
        if tag is None:
            by_field.pop(field, None)
        else:
            by_field[field] = tag

        self.state.profile.formatting.by_field = by_field
        self.state.touch_profile()

    # --- titles --------------------------------------------------------------

    def _build_title_box(self) -> SectionCard:
        box = SectionCard(
            "Contract titles",
            "Make the in-game contract list easier to scan without changing its sort order.",
        )
        layout = box.body_layout

        self.prefix = QComboBox()
        self.prefix.setAccessibleName("Contract title prefix")
        self.prefix.setAccessibleDescription(
            "Choose whether mission giver, difficulty, both, or neither appears first."
        )
        for value, name, _hint in TITLE_PREFIXES:
            self.prefix.addItem(name, value)
        self.prefix.currentIndexChanged.connect(self._set_prefix)

        form = QFormLayout()
        form.addRow("Show at the front of each title", self.prefix)
        layout.addLayout(form)

        self.prefix_hint = _muted("")
        layout.addWidget(self.prefix_hint)
        layout.addWidget(_muted(PREFIX_CAPTION))

        self.bracket_rep = QCheckBox("Also show the reputation number in the title")
        self.bracket_rep.setAccessibleName("Show reputation in contract titles")
        self.bracket_rep.setAccessibleDescription(
            "Adds a compact reputation value when local evidence is available."
        )
        self.bracket_rep.toggled.connect(lambda v: self._set_title("bracket_rep", v))
        layout.addWidget(self.bracket_rep)

        self.bracket_bp = QCheckBox("Also mark titles that can award a blueprint")
        self.bracket_bp.setAccessibleName("Mark blueprint contracts in titles")
        self.bracket_bp.setAccessibleDescription(
            "Adds a blueprint marker when local evidence is available."
        )
        self.bracket_bp.toggled.connect(lambda v: self._set_title("bracket_bp", v))
        layout.addWidget(self.bracket_bp)

        self.reward_note = NoticeBanner(tone=Tone.WARNING)
        self.reward_note.setVisible(False)
        layout.addWidget(self.reward_note)

        return box

    def _set_prefix(self, index: int) -> None:
        value = self.prefix.itemData(index)
        self.prefix_hint.setText(
            next(hint for v, _n, hint in TITLE_PREFIXES if v == value)
        )
        if self._loading:
            return
        self.state.profile.formatting.title.prefix = value
        self.state.touch_profile()

    def _set_title(self, name: str, value: bool) -> None:
        if self._loading:
            return
        setattr(self.state.profile.formatting.title, name, value)
        self.state.touch_profile()

    # --- length --------------------------------------------------------------

    def _build_length_box(self) -> SectionCard:
        box = SectionCard(
            "Blueprint-list length",
            "Limit unusually large pools so contract descriptions remain readable.",
        )
        layout = QFormLayout()
        box.add_layout(layout)

        self.max_items = QSpinBox()
        self.max_items.setAccessibleName("Maximum blueprints per contract")
        self.max_items.setAccessibleDescription(
            "Zero shows every blueprint; other values cap the generated list."
        )
        self.max_items.setRange(0, 500)
        self.max_items.setSpecialValueText("Show them all")
        self.max_items.valueChanged.connect(self._set_max_items)
        layout.addRow("Most blueprints to list per contract", self.max_items)
        layout.addRow(
            _muted(
                "Some contracts can drop dozens of blueprints. Limiting the list "
                "keeps the description readable in game."
            )
        )

        return box

    def _set_max_items(self, value: int) -> None:
        if self._loading:
            return
        self.state.profile.formatting.max_pool_items = value or None
        self.state.touch_profile()

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        self._loading = True
        try:
            formatting = self.state.profile.formatting

            self.default_tag.setCurrentIndex(
                max(0, self.default_tag.findData(formatting.emphasis))
            )
            self._update_example(formatting.emphasis)

            has_overrides = bool(formatting.by_field)
            self.per_field_toggle.setChecked(has_overrides)
            self.per_field.setVisible(has_overrides)
            for name, combo in self.field_tags.items():
                tag = formatting.by_field.get(name)
                combo.setCurrentIndex(max(0, combo.findData(tag)))

            index = self.prefix.findData(formatting.title.prefix)
            self.prefix.setCurrentIndex(max(0, index))
            self.prefix_hint.setText(
                next(
                    hint
                    for value, _name, hint in TITLE_PREFIXES
                    if value == formatting.title.prefix
                )
            )

            self.bracket_rep.setChecked(formatting.title.bracket_rep)
            self.bracket_bp.setChecked(formatting.title.bracket_bp)
            self.max_items.setValue(formatting.max_pool_items or 0)
        finally:
            self._loading = False

        self.style_metric.set_value(self.default_tag.currentText())
        self.prefix_metric.set_value(self.prefix.currentText())
        self.length_metric.set_value(
            str(self.max_items.value()) if self.max_items.value() else "All"
        )
        self._update_reward_note()

    def _update_reward_note(self) -> None:
        """Say when a setting cannot do anything yet, rather than letting it
        look broken."""
        contracts = self.state.contracts
        has_rewards = bool(
            contracts and any(not c.reward.is_empty for c in contracts.contracts)
        )
        if has_rewards:
            self.reward_note.setText("")
            self.reward_note.setVisible(False)
        else:
            self.reward_note.setText(
                "Local reward facts have not been loaded. Return to Overview to read "
                "the game and run the local provider."
                if contracts is None
                else "This build has no matched reward facts. Review provider health "
                "under Data & provenance."
            )
            self.reward_note.setVisible(True)


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)
    return label
