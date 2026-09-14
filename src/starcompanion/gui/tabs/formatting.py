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
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...render.renderer import (
    ENTITY_TAG_FIELDS,
    ENTITY_TAG_KINDS,
    MISSION_FACT_GROUPS,
)
from ..components import MetricTile, NoticeBanner, SectionCard, Tone
from ..labels import (
    FIELD_NAMES,
    INHERIT,
    PREFIX_CAPTION,
    REPUTATION_SEPARATORS,
    STYLE_CAPTION,
    TEXT_STYLES,
    TITLE_PREFIXES,
    WORDING_ORDERS,
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
        self.tag_metric = MetricTile("Mission tags")
        metrics.addWidget(self.style_metric, 0, 0)
        metrics.addWidget(self.prefix_metric, 0, 1)
        metrics.addWidget(self.length_metric, 0, 2)
        metrics.addWidget(self.tag_metric, 0, 3)
        for column in range(4):
            metrics.setColumnStretch(column, 1)
        layout.addLayout(metrics)

        sections = QVBoxLayout()
        sections.setSpacing(SPACING.large)
        self.style_section = self._build_style_box()
        self.title_section = self._build_title_box()
        sections.addWidget(self.style_section)
        sections.addWidget(self.title_section)
        self.tag_builder_section = self._build_tag_builder_box()
        sections.addWidget(self.tag_builder_section)
        self.wording_section = self._build_wording_box()
        sections.addWidget(self.wording_section)
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
            self.mission_details,
            self.tag_builder_enabled,
            *self.tag_field_boxes.values(),
            self.tag_placement,
            self.tag_separator,
            self.tag_max_characters,
            self.route_titles_enabled,
            self.route_title_mode,
            self.route_arrow,
            self.route_location_detail,
            self.mining_signature_enabled,
            self.legacy_mining_pack_enabled,
            self.entity_tag_builder_enabled,
            *self.entity_kind_boxes.values(),
            *self.entity_field_boxes.values(),
            self.entity_tag_placement,
            self.entity_tag_max_characters,
            self.wording_order,
            self.stat_block_placement,
            self.reputation_separator,
            self.thousands_separator,
            *self.wording_labels.values(),
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

    # --- mission presentation -----------------------------------------------

    def _build_tag_builder_box(self) -> SectionCard:
        box = SectionCard(
            "Mission Tag Builder",
            "Build bounded title tags from cached G5 facts. Missing or disabled facts add nothing.",
        )

        self.mission_details = QCheckBox(
            "Add enabled mission facts to contract descriptions"
        )
        self.mission_details.setAccessibleDescription(
            "Adds a structured Mission Details block using only enabled local facts."
        )
        self.mission_details.toggled.connect(self._set_mission_details)
        box.add_widget(self.mission_details)

        self.tag_builder_enabled = QCheckBox(
            "Add selected mission facts to contract titles"
        )
        self.tag_builder_enabled.setAccessibleDescription(
            "Enables typed title tags; it does not enable advanced templates."
        )
        self.tag_builder_enabled.toggled.connect(self._set_tag_builder_enabled)
        box.add_widget(self.tag_builder_enabled)

        labels = {
            "mission_type": "Mission type",
            "difficulty": "Difficulty",
            "friendly_spawns": "Friendly spawns",
            "hostile_spawns": "Hostile spawns",
            "ace": "Ace marker",
            "turrets": "Turrets",
            "engagement": "Engagement",
        }
        self.tag_field_boxes: dict[str, QCheckBox] = {}
        fields = QWidget()
        field_layout = QGridLayout(fields)
        field_layout.setContentsMargins(24, 0, 0, 0)
        for index, name in enumerate(MISSION_FACT_GROUPS):
            check = QCheckBox(labels[name])
            check.setAccessibleName(f"Use {labels[name].lower()} in mission title tags")
            check.toggled.connect(
                lambda _checked, field=name: self._set_tag_builder_fields(field)
            )
            self.tag_field_boxes[name] = check
            field_layout.addWidget(check, index // 2, index % 2)
        box.add_widget(fields)

        self.tag_placement = QComboBox()
        self.tag_placement.addItem("Before the original title", "prefix")
        self.tag_placement.addItem("After the original title", "suffix")
        self.tag_placement.setAccessibleName("Mission tag placement")
        self.tag_placement.currentIndexChanged.connect(self._set_tag_placement)

        self.tag_separator = QComboBox()
        self.tag_separator.addItem("Spaces", " ")
        self.tag_separator.addItem("Bullets", " • ")
        self.tag_separator.setAccessibleName("Mission tag separator")
        self.tag_separator.currentIndexChanged.connect(self._set_tag_separator)

        self.tag_max_characters = QSpinBox()
        self.tag_max_characters.setRange(16, 160)
        self.tag_max_characters.setAccessibleName("Maximum mission tag characters")
        self.tag_max_characters.setAccessibleDescription(
            "Whole tags that exceed the cap are omitted; they are never truncated mid-value."
        )
        self.tag_max_characters.valueChanged.connect(self._set_tag_max_characters)

        form = QFormLayout()
        form.addRow("Placement", self.tag_placement)
        form.addRow("Between tags", self.tag_separator)
        form.addRow("Maximum tag characters", self.tag_max_characters)
        box.add_layout(form)

        self.route_titles_enabled = QCheckBox(
            "Add evidence-backed routes to hauling titles"
        )
        self.route_titles_enabled.setAccessibleDescription(
            "Uses only endpoint tokens present in the stock mission descriptions."
        )
        self.route_titles_enabled.toggled.connect(self._set_route_enabled)
        box.add_widget(self.route_titles_enabled)

        self.route_title_mode = QComboBox()
        self.route_title_mode.addItem("Keep title and append route", "append")
        self.route_title_mode.addItem("Replace eligible title with route", "replace")
        self.route_title_mode.setAccessibleName("Hauling route title behavior")
        self.route_title_mode.currentIndexChanged.connect(self._set_route_mode)
        self.route_arrow = QComboBox()
        for label, value in (
            ("Greater-than (>)", ">"),
            ("ASCII arrow (->)", "->"),
            ("Word (to)", "to"),
        ):
            self.route_arrow.addItem(label, value)
        self.route_arrow.setAccessibleName("Hauling route separator")
        self.route_arrow.currentIndexChanged.connect(self._set_route_arrow)
        self.route_location_detail = QComboBox()
        self.route_location_detail.addItem("Full address", "address")
        self.route_location_detail.addItem("Short name", "name")
        self.route_location_detail.setAccessibleName("Hauling location detail")
        self.route_location_detail.currentIndexChanged.connect(self._set_route_detail)
        route_form = QFormLayout()
        route_form.addRow("Route behavior", self.route_title_mode)
        route_form.addRow("Route separator", self.route_arrow)
        route_form.addRow("Location detail", self.route_location_detail)
        box.add_layout(route_form)

        self.mining_signature_enabled = QCheckBox(
            "Append Battaglia mining resource tokens"
        )
        self.mining_signature_enabled.setAccessibleDescription(
            "Uses exact Resources and MineableType tokens from reviewed scan mission descriptions."
        )
        self.mining_signature_enabled.toggled.connect(self._set_mining_signature)
        box.add_widget(self.mining_signature_enabled)

        self.legacy_mining_pack_enabled = QCheckBox(
            "Add reviewed legacy presentation pack (exact build only)"
        )
        self.legacy_mining_pack_enabled.setAccessibleName(
            "Enable reviewed legacy presentation rules"
        )
        self.legacy_mining_pack_enabled.setAccessibleDescription(
            "Default-off numeric, item, commodity, and journal rules with exact build, key, stock-value, and source checks."
        )
        self.legacy_mining_pack_enabled.toggled.connect(
            self._set_legacy_mining_pack
        )
        box.add_widget(self.legacy_mining_pack_enabled)

        self.entity_tag_builder_enabled = QCheckBox(
            "Add typed tags to local entity and item names"
        )
        self.entity_tag_builder_enabled.setAccessibleDescription(
            "Uses only unambiguous local DataForge name joins and typed attributes."
        )
        self.entity_tag_builder_enabled.toggled.connect(self._set_entity_tags_enabled)
        box.add_widget(self.entity_tag_builder_enabled)

        kind_labels = {
            "vehicle": "Vehicles",
            "component": "Components",
            "ship-weapon": "Ship weapons",
            "fps-weapon": "FPS weapons",
            "medical": "Medical items",
            "commodity": "Commodities",
            "crafting": "Crafting recipes",
            "missile": "Missiles",
        }
        self.entity_kind_boxes: dict[str, QCheckBox] = {}
        entity_kinds = QWidget()
        kind_layout = QGridLayout(entity_kinds)
        kind_layout.setContentsMargins(24, 0, 0, 0)
        for index, name in enumerate(ENTITY_TAG_KINDS):
            check = QCheckBox(kind_labels[name])
            check.setAccessibleName(f"Tag {kind_labels[name].lower()}")
            check.toggled.connect(self._set_entity_tag_kinds)
            self.entity_kind_boxes[name] = check
            kind_layout.addWidget(check, index // 2, index % 2)
        box.add_widget(entity_kinds)

        field_labels = {
            "kind": "Type",
            "subtype": "Subtype",
            "tracking-signal": "Missile tracking signal",
            "size": "Size",
            "grade": "Grade",
            "class": "Class",
            "mass": "Mass",
            "cargo-capacity": "Cargo capacity",
            "crew-min": "Minimum crew",
            "crew-max": "Maximum crew",
            "damage": "Damage",
            "rate-of-fire": "Rate of fire",
            "projectile-speed": "Projectile speed",
            "range": "Weapon range",
            "magazine-capacity": "Magazine capacity",
            "effective-range": "Effective range",
            "health-restored": "Health restored",
            "max-health-repair-rate": "Health repair rate",
            "max-auto-dose": "Maximum automatic dose",
            "overdose-threshold": "Overdose threshold",
            "toxicity": "Toxicity",
            "base-price": "Base price",
            "shop-buy-price": "Shop buy price",
            "shop-sell-price": "Shop sell price",
        }
        self.entity_field_boxes: dict[str, QCheckBox] = {}
        entity_fields = QWidget()
        field_layout = QGridLayout(entity_fields)
        field_layout.setContentsMargins(24, 0, 0, 0)
        for index, name in enumerate(ENTITY_TAG_FIELDS):
            check = QCheckBox(field_labels[name])
            check.setAccessibleName(
                f"Use {field_labels[name].lower()} in entity tags"
            )
            check.toggled.connect(self._set_entity_tag_fields)
            self.entity_field_boxes[name] = check
            field_layout.addWidget(check, index // 4, index % 4)
        box.add_widget(entity_fields)

        self.entity_tag_placement = QComboBox()
        self.entity_tag_placement.addItem("Before the stock name", "prefix")
        self.entity_tag_placement.addItem("After the stock name", "suffix")
        self.entity_tag_placement.setAccessibleName("Entity tag placement")
        self.entity_tag_placement.currentIndexChanged.connect(
            self._set_entity_tag_placement
        )
        self.entity_tag_max_characters = QSpinBox()
        self.entity_tag_max_characters.setRange(16, 160)
        self.entity_tag_max_characters.setAccessibleName(
            "Maximum entity tag characters"
        )
        self.entity_tag_max_characters.setAccessibleDescription(
            "Attributes that do not fit are omitted as complete units."
        )
        self.entity_tag_max_characters.valueChanged.connect(
            self._set_entity_tag_max_characters
        )
        entity_form = QFormLayout()
        entity_form.addRow("Entity placement", self.entity_tag_placement)
        entity_form.addRow("Maximum entity tag characters", self.entity_tag_max_characters)
        box.add_layout(entity_form)

        self.entity_tag_preview = QLabel(
            "No locally joined entity names are loaded for preview."
        )
        self.entity_tag_preview.setWordWrap(True)
        self.entity_tag_preview.setProperty("component", "preview")
        self.entity_tag_preview.setAccessibleName("Entity title tag preview")
        box.add_widget(self.entity_tag_preview)

        self.tag_preview = QLabel("No evidenced mission facts are loaded for preview.")
        self.tag_preview.setWordWrap(True)
        self.tag_preview.setProperty("component", "preview")
        self.tag_preview.setAccessibleName("Mission title tag preview")
        box.add_widget(self.tag_preview)
        return box

    def _set_mission_details(self, checked: bool) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.description_details = checked
        self.state.touch_profile()

    def _set_tag_builder_enabled(self, checked: bool) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.tags.enabled = checked
        self.state.touch_profile()

    def _set_tag_builder_fields(self, _field: str) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.tags.fields = tuple(
            name for name in MISSION_FACT_GROUPS if self.tag_field_boxes[name].isChecked()
        )
        self.state.touch_profile()

    def _set_tag_placement(self, index: int) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.tags.placement = (
            self.tag_placement.itemData(index)
        )
        self.state.touch_profile()

    def _set_tag_separator(self, index: int) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.tags.separator = (
            self.tag_separator.itemData(index)
        )
        self.state.touch_profile()

    def _set_tag_max_characters(self, value: int) -> None:
        if self._loading:
            return
        self.state.profile.mission_presentation.tags.max_characters = value
        self.state.touch_profile()

    def _set_route_enabled(self, checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.route_titles_enabled = checked
            self.state.touch_profile()

    def _set_route_mode(self, index: int) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.route_title_mode = (
                self.route_title_mode.itemData(index)
            )
            self.state.touch_profile()

    def _set_route_arrow(self, index: int) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.route_arrow = (
                self.route_arrow.itemData(index)
            )
            self.state.touch_profile()

    def _set_route_detail(self, index: int) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.route_location_detail = (
                self.route_location_detail.itemData(index)
            )
            self.state.touch_profile()

    def _set_mining_signature(self, checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.mining_signature_enabled = checked
            self.state.touch_profile()

    def _set_legacy_mining_pack(self, checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.legacy_mining_pack_enabled = checked
            self.state.touch_profile()

    def _set_entity_tags_enabled(self, checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.entity_tags.enabled = checked
            self.state.touch_profile()

    def _set_entity_tag_kinds(self, _checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.entity_tags.kinds = frozenset(
                name
                for name in ENTITY_TAG_KINDS
                if self.entity_kind_boxes[name].isChecked()
            )
            self.state.touch_profile()

    def _set_entity_tag_fields(self, _checked: bool) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.entity_tags.fields = tuple(
                name
                for name in ENTITY_TAG_FIELDS
                if self.entity_field_boxes[name].isChecked()
            )
            self.state.touch_profile()

    def _set_entity_tag_placement(self, index: int) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.entity_tags.placement = (
                self.entity_tag_placement.itemData(index)
            )
            self.state.touch_profile()

    def _set_entity_tag_max_characters(self, value: int) -> None:
        if not self._loading:
            self.state.profile.mission_presentation.entity_tags.max_characters = value
            self.state.touch_profile()

    # --- structured wording -------------------------------------------------

    def _build_wording_box(self) -> SectionCard:
        box = SectionCard(
            "Generated wording",
            "Use validated controls for labels, ordering, and reward-number formatting.",
        )

        self.wording_order = QComboBox()
        self.wording_order.setAccessibleName("Generated reward section order")
        self.wording_order.setAccessibleDescription(
            "Reorders complete reward sections without hiding any enabled information."
        )
        for order, name, _hint in WORDING_ORDERS:
            self.wording_order.addItem(name, order)
        self.wording_order.currentIndexChanged.connect(self._set_wording_order)

        self.wording_order_hint = _muted("")
        order_form = QFormLayout()
        order_form.addRow("Information order", self.wording_order)
        box.add_layout(order_form)
        box.add_widget(self.wording_order_hint)

        self.stat_block_placement = QComboBox()
        self.stat_block_placement.addItem("Below the stock mission text", "below")
        self.stat_block_placement.addItem("Above the stock mission text", "above")
        self.stat_block_placement.setAccessibleName("Generated stat block placement")
        self.stat_block_placement.setAccessibleDescription(
            "Places complete generated mission details and rewards above or below unchanged stock text."
        )
        self.stat_block_placement.currentIndexChanged.connect(
            self._set_stat_block_placement
        )
        placement_form = QFormLayout()
        placement_form.addRow("Stat block", self.stat_block_placement)
        box.add_layout(placement_form)

        self.reputation_separator = QComboBox()
        self.reputation_separator.setAccessibleName("Reputation value separator")
        self.reputation_separator.setAccessibleDescription(
            "Choose the validated separator between difficulty-dependent reputation values."
        )
        for value, name in REPUTATION_SEPARATORS:
            self.reputation_separator.addItem(name, value)
        self.reputation_separator.currentIndexChanged.connect(
            self._set_reputation_separator
        )

        self.thousands_separator = QCheckBox("Use thousands separators")
        self.thousands_separator.setAccessibleName(
            "Use thousands separators in generated numbers"
        )
        self.thousands_separator.setAccessibleDescription(
            "Formats twelve thousand as 12,000 instead of 12000."
        )
        self.thousands_separator.toggled.connect(self._set_thousands_separator)

        number_form = QFormLayout()
        number_form.addRow("Reputation values", self.reputation_separator)
        number_form.addRow("", self.thousands_separator)
        box.add_layout(number_form)

        label_names = {
            "reputation": "Reputation",
            "scrip": "MG Scrip",
            "items": "Item rewards",
            "scenario": "Scenario points",
            "blueprints": "Blueprint pools",
            "multiple_blueprints": "Multiple pools",
            "chance": "Award chance",
            "regional": "Regional variants",
            "owned": "Owned marker",
        }
        self.wording_labels: dict[str, QLineEdit] = {}
        label_form = QFormLayout()
        for field, shown in label_names.items():
            editor = QLineEdit()
            editor.setMaxLength(48)
            editor.setAccessibleName(f"{shown} generated label")
            editor.setAccessibleDescription(
                "Plain text only; tags, line breaks, escapes, and control characters are rejected."
            )
            editor.editingFinished.connect(
                lambda label=field: self._set_wording_label(label)
            )
            self.wording_labels[field] = editor
            label_form.addRow(shown, editor)
        box.add_layout(label_form)

        self.wording_status = NoticeBanner(
            "Structured wording is validated before it reaches the renderer.",
            tone=Tone.SUCCESS,
        )
        box.add_widget(self.wording_status)
        return box

    def _set_wording_order(self, index: int) -> None:
        order = self.wording_order.itemData(index)
        if not order:
            return
        hint = next(
            (hint for value, _name, hint in WORDING_ORDERS if value == tuple(order)),
            "Custom validated order loaded from this profile.",
        )
        self.wording_order_hint.setText(hint)
        if self._loading:
            return
        self.state.profile.wording.section_order = tuple(order)
        self.state.touch_profile()

    def _set_reputation_separator(self, index: int) -> None:
        value = self.reputation_separator.itemData(index)
        if self._loading or value is None:
            return
        self.state.profile.wording.reputation_separator = value
        self.state.touch_profile()

    def _set_stat_block_placement(self, index: int) -> None:
        value = self.stat_block_placement.itemData(index)
        if self._loading or value is None:
            return
        self.state.profile.wording.stat_block_placement = value
        self.state.touch_profile()

    def _set_thousands_separator(self, checked: bool) -> None:
        if self._loading:
            return
        self.state.profile.wording.thousands_separator = checked
        self.state.touch_profile()

    def _set_wording_label(self, field: str) -> None:
        if self._loading:
            return
        editor = self.wording_labels[field]
        try:
            setattr(self.state.profile.wording.labels, field, editor.text())
        except ValueError as exc:
            self._loading = True
            try:
                editor.setText(getattr(self.state.profile.wording.labels, field))
            finally:
                self._loading = False
            self.wording_status.set_tone(Tone.DANGER)
            self.wording_status.setText(f"That label was not saved: {exc}")
            return
        self.wording_status.set_tone(Tone.SUCCESS)
        self.wording_status.setText(
            "Structured wording is valid and ready for preview."
        )
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

            mission = self.state.profile.mission_presentation
            self.mission_details.setChecked(mission.description_details)
            self.tag_builder_enabled.setChecked(mission.tags.enabled)
            for name, check in self.tag_field_boxes.items():
                check.setChecked(name in mission.tags.fields)
            self.tag_placement.setCurrentIndex(
                max(0, self.tag_placement.findData(mission.tags.placement))
            )
            self.tag_separator.setCurrentIndex(
                max(0, self.tag_separator.findData(mission.tags.separator))
            )
            self.tag_max_characters.setValue(mission.tags.max_characters)
            self.route_titles_enabled.setChecked(mission.route_titles_enabled)
            self.route_title_mode.setCurrentIndex(
                max(0, self.route_title_mode.findData(mission.route_title_mode))
            )
            self.route_arrow.setCurrentIndex(
                max(0, self.route_arrow.findData(mission.route_arrow))
            )
            self.route_location_detail.setCurrentIndex(
                max(
                    0,
                    self.route_location_detail.findData(
                        mission.route_location_detail
                    ),
                )
            )
            self.mining_signature_enabled.setChecked(mission.mining_signature_enabled)
            self.legacy_mining_pack_enabled.setChecked(
                mission.legacy_mining_pack_enabled
            )
            self.entity_tag_builder_enabled.setChecked(mission.entity_tags.enabled)
            for name, check in self.entity_kind_boxes.items():
                check.setChecked(name in mission.entity_tags.kinds)
            for name, check in self.entity_field_boxes.items():
                check.setChecked(name in mission.entity_tags.fields)
            self.entity_tag_placement.setCurrentIndex(
                max(
                    0,
                    self.entity_tag_placement.findData(
                        mission.entity_tags.placement
                    ),
                )
            )
            self.entity_tag_max_characters.setValue(
                mission.entity_tags.max_characters
            )

            wording = self.state.profile.wording
            while self.wording_order.count() > len(WORDING_ORDERS):
                self.wording_order.removeItem(self.wording_order.count() - 1)
            order = tuple(wording.section_order)
            order_index = self.wording_order.findData(order)
            if order_index < 0:
                self.wording_order.addItem("Custom profile order", order)
                order_index = self.wording_order.count() - 1
            self.wording_order.setCurrentIndex(order_index)
            self.wording_order_hint.setText(
                next(
                    (
                        hint
                        for value, _name, hint in WORDING_ORDERS
                        if value == order
                    ),
                    "Custom validated order loaded from this profile.",
                )
            )
            placement_index = self.stat_block_placement.findData(
                wording.stat_block_placement
            )
            self.stat_block_placement.setCurrentIndex(max(0, placement_index))
            separator_index = self.reputation_separator.findData(
                wording.reputation_separator
            )
            self.reputation_separator.setCurrentIndex(max(0, separator_index))
            self.thousands_separator.setChecked(wording.thousands_separator)
            for field, editor in self.wording_labels.items():
                editor.setText(getattr(wording.labels, field))
            self.max_items.setValue(formatting.max_pool_items or 0)
        finally:
            self._loading = False

        self.style_metric.set_value(self.default_tag.currentText())
        self.prefix_metric.set_value(self.prefix.currentText())
        self.length_metric.set_value(
            str(self.max_items.value()) if self.max_items.value() else "All"
        )
        self._refresh_tag_preview()
        self._update_reward_note()

    def _refresh_tag_preview(self) -> None:
        contract = self.state.sample_contract()
        if contract is None:
            preview = "No evidenced mission facts are loaded for preview."
        else:
            options = self.state.profile.to_render_options()
            tags = " ".join(
                filter(
                    None,
                    (
                        options.title_fact_tags(contract),
                        options.mission_title_suffix(contract),
                    ),
                )
            )
            preview = tags or "No enabled title tags match this contract."
        self.tag_preview.setText(preview)
        entity = next(iter(self.state.contracts.entities), None) if self.state.contracts else None
        if entity is None:
            entity_preview = "No locally joined entity names are loaded for preview."
        else:
            entity_preview = self.state.profile.to_render_options().entity_tag(entity)[0]
            if not entity_preview:
                entity_preview = "No enabled entity tag matches the preview item."
        self.entity_tag_preview.setText(entity_preview)
        mission = self.state.profile.mission_presentation
        enabled = (
            mission.tags.enabled
            or mission.route_titles_enabled
            or mission.mining_signature_enabled
            or mission.legacy_mining_pack_enabled
            or mission.entity_tags.enabled
        )
        for control in (
            self.route_title_mode,
            self.route_arrow,
            self.route_location_detail,
        ):
            control.setEnabled(mission.route_titles_enabled)
        for control in (
            *self.entity_kind_boxes.values(),
            *self.entity_field_boxes.values(),
            self.entity_tag_placement,
            self.entity_tag_max_characters,
        ):
            control.setEnabled(mission.entity_tags.enabled)
        self.tag_metric.set_value("Enabled" if enabled else "Off", preview)

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
