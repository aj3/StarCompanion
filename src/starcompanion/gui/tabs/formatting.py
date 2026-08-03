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
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...render.renderer import TitlePrefix
from ..labels import (
    FIELD_NAMES,
    INHERIT,
    PREFIX_CAPTION,
    STYLE_CAPTION,
    TEXT_STYLES,
    TITLE_PREFIXES,
    preview_html,
    preview_note,
)
from ...features import community_rewards_enabled
from ..state import AppState


class FormattingTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_style_box())
        layout.addWidget(self._build_title_box())
        self.length_box = self._build_length_box()
        self.length_box.setVisible(community_rewards_enabled())
        layout.addWidget(self.length_box)
        layout.addStretch(1)

        state.profileChanged.connect(self.refresh)
        self.refresh()

    # --- text style ----------------------------------------------------------

    def _build_style_box(self) -> QGroupBox:
        box = QGroupBox("How the added text looks")
        layout = QVBoxLayout(box)

        self.default_tag = QComboBox()
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
        layout.addWidget(self.example)

        self.example_note = _muted("")
        layout.addWidget(self.example_note)
        layout.addWidget(_muted(STYLE_CAPTION))

        self.per_field_toggle = QCheckBox(
            "Use a different style for each kind of information"
        )
        self.per_field_toggle.toggled.connect(self._toggle_per_field)
        layout.addWidget(self.per_field_toggle)

        self.per_field = QWidget()
        per_field_form = QFormLayout(self.per_field)
        per_field_form.setContentsMargins(24, 0, 0, 0)

        self.field_tags: dict[str, QComboBox] = {}
        for name, label in FIELD_NAMES.items():
            combo = QComboBox()
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
        """Show a real contract line in the chosen style."""
        if not tag:
            return

        styled = preview_html(tag, "Reputation Awarded: 250")
        self.example.setText(
            "A contract description would read:<br><br>"
            "Deal with the outlaws at Shubin Mining Facility.<br><br>"
            f"{styled}"
        )
        self.example_note.setText(preview_note(tag))

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

    def _build_title_box(self) -> QGroupBox:
        box = QGroupBox("Contract titles")
        layout = QVBoxLayout(box)

        self.prefix = QComboBox()
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
        self.bracket_rep.toggled.connect(lambda v: self._set_title("bracket_rep", v))
        self.bracket_rep.setVisible(community_rewards_enabled())
        layout.addWidget(self.bracket_rep)

        self.bracket_bp = QCheckBox("Also mark titles that can award a blueprint")
        self.bracket_bp.toggled.connect(lambda v: self._set_title("bracket_bp", v))
        self.bracket_bp.setVisible(community_rewards_enabled())
        layout.addWidget(self.bracket_bp)

        self.reward_note = _muted("")
        self.reward_note.setVisible(community_rewards_enabled())
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

    def _build_length_box(self) -> QGroupBox:
        box = QGroupBox("Keep it short")
        layout = QFormLayout(box)

        self.max_items = QSpinBox()
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
        else:
            self.reward_note.setText(
                "These two need reward numbers, which are not in your game files. "
                "Add a contract list on the Start tab to use them."
            )


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)
    return label
