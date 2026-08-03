"""Formatting tab: emphasis tags, title tags, and pool length.

The tag list is deliberately short because it is the complete set the game can
render -- there is no arbitrary colour, so this is as close to colour-coding as
a localization override can get.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...render.renderer import Field, TitlePrefix
from ...validate import EMPHASIS_TAGS
from ..state import AppState

INHERIT = "(same as default)"

PREFIXES = (
    (TitlePrefix.NONE, "None"),
    (TitlePrefix.ORG, "Mission giver"),
    (TitlePrefix.RANK, "Difficulty rank"),
    (TitlePrefix.ORG_RANK, "Mission giver + rank"),
)

FIELD_LABELS = (
    (Field.REPUTATION, "Reputation"),
    (Field.POOLS, "Blueprint pools"),
    (Field.GATES, "Rank gates"),
    (Field.REGIONAL, "Regional variants"),
    (Field.SCENARIO, "Scenario points"),
    (Field.SCRIP, "MG Scrip"),
    (Field.TITLE, "Title tags"),
)


def _tags() -> list[str]:
    return sorted(EMPHASIS_TAGS)


class FormattingTab(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._loading = False

        self.default_tag = QComboBox()
        self.default_tag.addItems(_tags())
        self.default_tag.currentTextChanged.connect(self._set_default_tag)

        self.field_tags: dict[str, QComboBox] = {}
        per_field = QFormLayout()
        for name, label in FIELD_LABELS:
            combo = QComboBox()
            combo.addItem(INHERIT)
            combo.addItems(_tags())
            combo.currentTextChanged.connect(lambda text, n=name: self._set_field_tag(n, text))
            self.field_tags[name] = combo
            per_field.addRow(label, combo)

        emphasis_box = QGroupBox("Emphasis")
        emphasis_layout = QFormLayout(emphasis_box)
        emphasis_layout.addRow("Default tag", self.default_tag)
        note = QLabel(
            "These are the only tags Star Citizen renders. There is no custom "
            "colour, so emphasis level is the closest available substitute."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        emphasis_layout.addRow(note)
        emphasis_layout.addRow(QLabel("Per-field overrides:"))
        emphasis_layout.addRow(_wrap(per_field))

        self.bracket_rep = QCheckBox("Show rep in title, e.g. [100 Rep]")
        self.bracket_rep.toggled.connect(lambda v: self._set_title("bracket_rep", v))
        self.bracket_bp = QCheckBox("Show blueprint flag in title, e.g. [BP]")
        self.bracket_bp.toggled.connect(lambda v: self._set_title("bracket_bp", v))

        self.prefix = QComboBox()
        for value, label in PREFIXES:
            self.prefix.addItem(label, value)
        self.prefix.currentIndexChanged.connect(self._set_prefix)

        title_box = QGroupBox("Titles")
        title_layout = QFormLayout(title_box)
        title_layout.addRow(self.bracket_rep)
        title_layout.addRow(self.bracket_bp)
        title_layout.addRow("Prefix", self.prefix)
        prefix_note = QLabel(
            "A prefix cannot re-sort the in-game contract list, but it makes a "
            "flat list scannable by giver or tier."
        )
        prefix_note.setWordWrap(True)
        prefix_note.setEnabled(False)
        title_layout.addRow(prefix_note)

        self.max_items = QSpinBox()
        self.max_items.setRange(0, 500)
        self.max_items.setSpecialValueText("No limit")
        self.max_items.valueChanged.connect(self._set_max_items)

        length_box = QGroupBox("Length")
        length_layout = QFormLayout(length_box)
        length_layout.addRow("Max blueprint items per pool", self.max_items)

        layout = QVBoxLayout(self)
        layout.addWidget(emphasis_box)
        layout.addWidget(title_box)
        layout.addWidget(length_box)
        layout.addStretch(1)

        state.profileChanged.connect(self.refresh)
        self.refresh()

    # --- edits ---------------------------------------------------------------

    def _set_default_tag(self, tag: str) -> None:
        if self._loading:
            return
        self.state.profile.formatting.emphasis = tag
        self.state.touch_profile()

    def _set_field_tag(self, name: str, text: str) -> None:
        if self._loading:
            return
        by_field = dict(self.state.profile.formatting.by_field)
        if text == INHERIT:
            by_field.pop(name, None)
        else:
            by_field[name] = text
        self.state.profile.formatting.by_field = by_field
        self.state.touch_profile()

    def _set_title(self, name: str, value: bool) -> None:
        if self._loading:
            return
        setattr(self.state.profile.formatting.title, name, value)
        self.state.touch_profile()

    def _set_prefix(self, index: int) -> None:
        if self._loading:
            return
        self.state.profile.formatting.title.prefix = self.prefix.itemData(index)
        self.state.touch_profile()

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
            self.default_tag.setCurrentText(formatting.emphasis)
            for name, combo in self.field_tags.items():
                combo.setCurrentText(formatting.by_field.get(name, INHERIT))
            self.bracket_rep.setChecked(formatting.title.bracket_rep)
            self.bracket_bp.setChecked(formatting.title.bracket_bp)
            self.prefix.setCurrentIndex(self.prefix.findData(formatting.title.prefix))
            self.max_items.setValue(formatting.max_pool_items or 0)
        finally:
            self._loading = False


def _wrap(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
