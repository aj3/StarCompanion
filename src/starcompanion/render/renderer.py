"""Turn contracts into localization values via user-editable templates.

Templates are written with real newlines for readability; the renderer converts
them to the literal ``\\n`` escapes the game requires as the last step, so a
template author cannot accidentally emit a line break that blanks a contract
in-game.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import (
    ChoiceLoader,
    DictLoader,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
)
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ..model import (
    BlueprintPool,
    Contract,
    ContractSet,
    Evidence,
    GateKind,
    LocalizedEntity,
    MissionDetail,
    StringKind,
)
from ..route_presentation import resource_signature, route_fragment, token_evidence
from ..validate import EMPHASIS_TAGS, Issue, Severity, validate_value

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

_REAL_NEWLINE = re.compile(r"[ \t]*\r?\n")

MISSION_FACT_GROUPS = (
    "mission_type",
    "difficulty",
    "friendly_spawns",
    "hostile_spawns",
    "ace",
    "turrets",
    "engagement",
)
ENTITY_TAG_KINDS = (
    "vehicle",
    "component",
    "ship-weapon",
    "fps-weapon",
    "medical",
    "commodity",
    "missile",
)
ENTITY_TAG_FIELDS = ("kind", "size", "grade", "class")
_ENTITY_KIND_LABELS = {
    "vehicle": "Vehicle",
    "component": "Component",
    "ship-weapon": "Ship Weapon",
    "fps-weapon": "FPS Weapon",
    "medical": "Medical",
    "commodity": "Commodity",
    "missile": "Missile",
}
_GROUP_FACTS = {
    "mission_type": ("mission-type",),
    "difficulty": (
        "difficulty",
        "difficulty-risk",
        "difficulty-knowledge",
        "difficulty-mental-load",
        "difficulty-mechanical-skill",
    ),
    "friendly_spawns": ("friendly-spawns",),
    "hostile_spawns": ("hostile-spawns",),
    "ace": ("ace-pilot", "ace-probability"),
    "turrets": ("turret-count",),
    "engagement": ("engagement-type", "engagement-distance"),
}
_DETAIL_LABELS = {
    "mission-type": "Mission type",
    "difficulty": "Difficulty",
    "difficulty-risk": "Risk of loss",
    "difficulty-knowledge": "Game knowledge",
    "difficulty-mental-load": "Mental load",
    "difficulty-mechanical-skill": "Mechanical skill",
    "friendly-spawns": "Friendly spawns",
    "hostile-spawns": "Hostile spawns",
    "ace-pilot": "Ace pilot",
    "ace-probability": "Ace probability",
    "turret-count": "Turrets",
    "engagement-distance": "Engagement distance",
    "engagement-type": "Engagement type",
}


def validate_wording_label(value: str) -> str:
    """Validate a plain-text label before it reaches a game string."""
    if value != value.strip() or not value or len(value) > 48:
        raise ValueError("wording labels must be 1-48 trimmed characters")
    if value.endswith(":"):
        raise ValueError(
            "wording labels omit the trailing colon added by the renderer"
        )
    if any(character in value for character in "<>\\\r\n\0") or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        raise ValueError(
            "wording labels cannot contain tags, escapes, controls, "
            "or direction overrides"
        )
    return value


class TemplateRenderError(RuntimeError):
    """A template failed. Always names the key and template involved."""

    def __init__(self, key: str, template: str, cause: Exception):
        super().__init__(f"{template} failed rendering {key}: {cause}")
        self.key = key
        self.template = template
        self.cause = cause


class TitlePrefix:
    NONE = "none"
    ORG = "org"
    RANK = "rank"
    ORG_RANK = "org_rank"


class Field:
    """Annotation kinds that can each carry their own emphasis tag."""

    REPUTATION = "reputation"
    SCRIP = "scrip"
    SCENARIO = "scenario"
    POOLS = "pools"
    GATES = "gates"
    REGIONAL = "regional"
    TITLE = "title"
    ITEMS = "items"

    ALL = (REPUTATION, SCRIP, SCENARIO, POOLS, GATES, REGIONAL, TITLE, ITEMS)


class Section:
    """Validated reward-section identifiers used by structured profiles."""

    REPUTATION = "reputation"
    SCRIP = "scrip"
    ITEMS = "items"
    SCENARIO = "scenario"
    BLUEPRINTS = "blueprints"

    ALL = (REPUTATION, SCRIP, ITEMS, SCENARIO, BLUEPRINTS)


@dataclass(frozen=True)
class RenderLabels:
    """Plain-text labels used by the built-in structured templates."""

    reputation: str = "Reputation Awarded"
    scrip: str = "MG Scrip"
    items: str = "Item Rewards"
    scenario: str = "Scenario Progress Points"
    blueprints: str = "Potential Blueprints"
    multiple_blueprints: str = "Multiple Blueprint Pools"
    chance: str = "Award chance"
    regional: str = "[Regional Variants] example locations"
    owned: str = "Owned"

    def __post_init__(self) -> None:
        for value in self.__dict__.values():
            validate_wording_label(value)


@dataclass
class RenderOptions:
    """What appears and how. Phase 3 builds these from a saved profile."""

    show_reputation: bool = True
    show_blueprints: bool = True
    show_item_rewards: bool = True
    show_scenario_points: bool = True
    show_scrip: bool = True
    show_rank_gates: bool = True
    show_regional_variants: bool = True
    show_caveats: bool = True
    show_owned: bool = True
    """Mark blueprints you already hold, from a SCMDB export."""

    emphasis: str = "EM4"
    """Default tag wrapping annotation headers."""
    emphasis_by_field: dict[str, str] = field(default_factory=dict)
    """Per-`Field` overrides, so rep can stand out more than gate notes."""
    title_bracket_rep: bool = True
    title_bracket_bp: bool = True
    title_prefix: str = TitlePrefix.NONE
    max_pool_items: int | None = None
    """Truncate long pools; None keeps everything."""
    section_order: tuple[str, ...] = Section.ALL
    labels: RenderLabels = field(default_factory=RenderLabels)
    reputation_separator: str = " / "
    thousands_separator: bool = True
    mission_fact_groups: frozenset[str] = frozenset()
    show_mission_details: bool = False
    tag_builder_enabled: bool = False
    tag_builder_fields: tuple[str, ...] = MISSION_FACT_GROUPS
    tag_builder_placement: str = "prefix"
    tag_builder_separator: str = " "
    tag_builder_max_characters: int = 72
    route_titles_enabled: bool = False
    route_title_mode: str = "append"
    route_arrow: str = ">"
    route_location_detail: str = "address"
    mining_signature_enabled: bool = False
    legacy_mining_pack_enabled: bool = False
    entity_tag_builder_enabled: bool = False
    entity_tag_kinds: frozenset[str] = frozenset(ENTITY_TAG_KINDS)
    entity_tag_fields: tuple[str, ...] = ENTITY_TAG_FIELDS
    entity_tag_placement: str = "prefix"
    entity_tag_max_characters: int = 72

    def __post_init__(self):
        for tag in (self.emphasis, *self.emphasis_by_field.values()):
            if tag not in EMPHASIS_TAGS:
                raise ValueError(
                    f"emphasis tag {tag!r} is not renderable in-game; "
                    f"choose one of {sorted(EMPHASIS_TAGS)}"
                )
        unknown = set(self.emphasis_by_field) - set(Field.ALL)
        if unknown:
            raise ValueError(
                f"unknown emphasis field(s) {sorted(unknown)}; "
                f"choose from {sorted(Field.ALL)}"
            )
        if len(self.section_order) != len(Section.ALL) or set(
            self.section_order
        ) != set(Section.ALL):
            raise ValueError(
                "section_order must contain each structured reward section exactly once"
            )
        if self.reputation_separator not in {" / ", "/", " • "}:
            raise ValueError("unsupported reputation separator")
        unknown_groups = set(self.mission_fact_groups) - set(MISSION_FACT_GROUPS)
        unknown_tags = set(self.tag_builder_fields) - set(MISSION_FACT_GROUPS)
        if unknown_groups or unknown_tags:
            raise ValueError("unknown mission presentation group")
        if len(self.tag_builder_fields) != len(set(self.tag_builder_fields)):
            raise ValueError("tag builder fields must be unique")
        if self.tag_builder_placement not in {"prefix", "suffix"}:
            raise ValueError("unsupported tag builder placement")
        if self.tag_builder_separator not in {" ", " • "}:
            raise ValueError("unsupported tag builder separator")
        if not 16 <= self.tag_builder_max_characters <= 160:
            raise ValueError("tag builder length must be between 16 and 160")
        if self.route_title_mode not in {"append", "replace"}:
            raise ValueError("unsupported route title mode")
        if self.route_arrow not in {">", "->", "to"}:
            raise ValueError("unsupported route arrow")
        if self.route_location_detail not in {"address", "name"}:
            raise ValueError("unsupported route location detail")
        if set(self.entity_tag_kinds) - set(ENTITY_TAG_KINDS):
            raise ValueError("unsupported entity tag kind")
        if (
            set(self.entity_tag_fields) - set(ENTITY_TAG_FIELDS)
            or len(self.entity_tag_fields) != len(set(self.entity_tag_fields))
        ):
            raise ValueError("invalid entity tag fields")
        if self.entity_tag_placement not in {"prefix", "suffix"}:
            raise ValueError("unsupported entity tag placement")
        if not 16 <= self.entity_tag_max_characters <= 160:
            raise ValueError("entity tag length must be between 16 and 160")

    def emphasis_for(self, field_name: str | None) -> str:
        return self.emphasis_by_field.get(field_name or "", self.emphasis)

    def format_number(self, value: int) -> str:
        return f"{value:,}" if self.thousands_separator else str(value)

    def format_reputation(self, values: list[int]) -> str:
        return self.reputation_separator.join(
            self.format_number(value) for value in values
        )

    def visible_mission_details(self, contract: Contract) -> tuple[MissionDetail, ...]:
        allowed = {
            name
            for group in self.mission_fact_groups
            for name in _GROUP_FACTS[group]
        }
        return tuple(item for item in contract.mission_details if item.name in allowed)

    def mission_detail_lines(self, contract: Contract) -> tuple[str, ...]:
        if not self.show_mission_details:
            return ()
        details = self.visible_mission_details(contract)
        order = {
            name: index
            for index, group in enumerate(MISSION_FACT_GROUPS)
            for name in _GROUP_FACTS[group]
        }
        return tuple(
            f"{_DETAIL_LABELS[item.name]}: {self._format_mission_value(item)}"
            + (
                f" (confidence: {item.confidence.value})"
                if item.confidence.value not in {"high", "none"}
                else ""
            )
            for item in sorted(details, key=lambda value: order[value.name])
        )

    def title_fact_tags(self, contract: Contract) -> str:
        parts = [text for text, _details in self._title_fact_parts(contract)]
        return self.tag_builder_separator.join(parts)

    def mission_title_suffix(self, contract: Contract) -> str:
        parts: list[str] = []
        current_length = len(self.title_fact_tags(contract))
        if self.route_titles_enabled and self.route_title_mode == "append":
            route = route_fragment(
                contract,
                arrow=self.route_arrow,
                detail=self.route_location_detail,
            )
            if route:
                current_length = self._append_bounded_title_part(
                    parts, f"[{route}]", current_length
                )
        if self.mining_signature_enabled:
            signature = resource_signature(contract)
            if signature:
                self._append_bounded_title_part(
                    parts, f"[{signature}]", current_length
                )
        return self.tag_builder_separator.join(parts)

    def mission_title_base(self, contract: Contract, base: str) -> str:
        if self.route_titles_enabled and self.route_title_mode == "replace":
            route = route_fragment(
                contract,
                arrow=self.route_arrow,
                detail=self.route_location_detail,
            )
            current_length = len(self.title_fact_tags(contract))
            added = len(route) + (
                len(self.tag_builder_separator) if current_length else 0
            )
            if route and current_length + added <= self.tag_builder_max_characters:
                return route
        return base

    def _append_bounded_title_part(
        self,
        parts: list[str],
        text: str,
        current_length: int,
    ) -> int:
        added = len(text) + (
            len(self.tag_builder_separator) if current_length else 0
        )
        if current_length + added <= self.tag_builder_max_characters:
            parts.append(text)
            return current_length + added
        return current_length

    def mission_evidence(
        self,
        contract: Contract,
        kind: StringKind,
        rendered_value: str | None = None,
    ) -> tuple[Evidence, ...]:
        if kind is StringKind.DESC and self.show_mission_details:
            details = self.visible_mission_details(contract)
        elif kind is StringKind.TITLE and self.tag_builder_enabled:
            details = tuple(
                detail
                for _text, selected in self._title_fact_parts(contract)
                for detail in selected
            )
        else:
            details = ()
        evidence = list(
            dict.fromkeys(
                evidence for detail in details for evidence in detail.evidence
            )
        )
        if kind is StringKind.TITLE:
            route = ""
            if self.route_titles_enabled:
                candidate = route_fragment(
                    contract,
                    arrow=self.route_arrow,
                    detail=self.route_location_detail,
                )
                if self.route_title_mode == "append":
                    rendered = f"[{candidate}]" in self.mission_title_suffix(
                        contract
                    )
                else:
                    current_length = len(self.title_fact_tags(contract))
                    added = len(candidate) + (
                        len(self.tag_builder_separator) if current_length else 0
                    )
                    rendered = bool(candidate) and (
                        current_length + added <= self.tag_builder_max_characters
                    )
                if rendered_value is not None and candidate not in rendered_value:
                    rendered = False
                if rendered:
                    route = candidate
            resource = ""
            if self.mining_signature_enabled:
                candidate = resource_signature(contract)
                if (
                    f"[{candidate}]" in self.mission_title_suffix(contract)
                    and (rendered_value is None or candidate in rendered_value)
                ):
                    resource = candidate
            evidence.extend(
                token_evidence(
                    contract,
                    route_text=route,
                    resource_text=resource,
                )
            )
        return tuple(dict.fromkeys(evidence))

    def _title_fact_parts(
        self,
        contract: Contract,
    ) -> tuple[tuple[str, tuple[MissionDetail, ...]], ...]:
        if not self.tag_builder_enabled:
            return ()
        enabled = set(self.mission_fact_groups)
        parts: list[tuple[str, tuple[MissionDetail, ...]]] = []
        current_length = 0
        for group in self.tag_builder_fields:
            if group not in enabled:
                continue
            selected = self._title_group_details(contract, group)
            text = self._title_group_text(group, selected)
            if not text:
                continue
            added = len(text) + (len(self.tag_builder_separator) if parts else 0)
            if current_length + added > self.tag_builder_max_characters:
                continue
            parts.append((text, selected))
            current_length += added
        return tuple(parts)

    @staticmethod
    def _title_group_details(
        contract: Contract,
        group: str,
    ) -> tuple[MissionDetail, ...]:
        found = {
            item.name: item
            for item in contract.mission_details
            if item.name in _GROUP_FACTS[group]
        }
        preferred = {
            "difficulty": ("difficulty", "difficulty-risk"),
            "ace": ("ace-pilot", "ace-probability"),
            "engagement": ("engagement-type", "engagement-distance"),
        }.get(group, _GROUP_FACTS[group])
        return tuple(found[name] for name in preferred if name in found)[:1]

    def _title_group_text(
        self,
        group: str,
        details: tuple[MissionDetail, ...],
    ) -> str:
        if not details:
            return ""
        item = details[0]
        value = self._format_mission_value(item, compact=True)
        if group == "mission_type":
            return f"[{value}]"
        if group == "difficulty":
            return f"[Difficulty {value}]"
        if group == "friendly_spawns":
            return f"[Allies {value}]"
        if group == "hostile_spawns":
            return f"[Hostiles {value}]"
        if group == "ace":
            if item.name == "ace-pilot":
                return "[ACE]" if item.value is True else ""
            return "[ACE?]" if isinstance(item.value, (int, float)) and item.value > 0 else ""
        if group == "turrets":
            return f"[Turrets {value}]"
        return f"[{value}]"

    def _format_mission_value(
        self,
        detail: MissionDetail,
        *,
        compact: bool = False,
    ) -> str:
        value = detail.value
        if detail.name == "ace-probability" and isinstance(value, (int, float)):
            return f"{value:.0%}"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, int):
            return self.format_number(value)
        if isinstance(value, float):
            return f"{value:g}" if compact else f"{value:,.1f}"
        return value

    def entity_tag(
        self,
        entity: LocalizedEntity,
    ) -> tuple[str, tuple[Evidence, ...]]:
        """Build one bounded tag from typed, equal-across-record entity facts."""

        if (
            not self.entity_tag_builder_enabled
            or entity.kind not in self.entity_tag_kinds
        ):
            return "", ()
        parts: list[str] = []
        evidence: list[Evidence] = []
        current = 2
        for field_name in self.entity_tag_fields:
            if field_name == "kind":
                text = _ENTITY_KIND_LABELS[entity.kind]
                selected = entity.evidence
            else:
                attribute = entity.attribute(field_name)
                if attribute is None:
                    continue
                if field_name == "size":
                    text = f"S{attribute.value}"
                elif field_name == "grade":
                    text = f"Grade {attribute.value}"
                else:
                    text = str(attribute.value)
                selected = attribute.evidence
            added = len(text) + (1 if parts else 0)
            if current + added > self.entity_tag_max_characters:
                continue
            parts.append(text)
            current += added
            evidence.extend(selected)
        if not parts:
            return "", ()
        return f"[{' '.join(parts)}]", tuple(dict.fromkeys(evidence))

    def render_entity(
        self,
        entity: LocalizedEntity,
    ) -> tuple[str, tuple[Evidence, ...]] | None:
        tag, evidence = self.entity_tag(entity)
        if not tag:
            return None
        if self.entity_tag_placement == "prefix":
            return f"{tag} {entity.base_text}", evidence
        return f"{entity.base_text} {tag}", evidence


@dataclass
class RenderResult:
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[tuple[str, Issue]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(key, reason) for contracts that produced nothing usable."""
    provenance: dict[str, tuple[Evidence, ...]] = field(default_factory=dict)
    """Evidence contributing to each emitted localization value."""

    def summary(self) -> str:
        parts = [f"{len(self.values)} rendered"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        return ", ".join(parts)


class Renderer:
    def __init__(
        self,
        options: RenderOptions | None = None,
        *,
        template_dir: Path | None = None,
        overrides: dict[str, str] | None = None,
    ):
        self.options = options or RenderOptions()

        loaders = []
        if overrides:
            loaders.append(DictLoader(overrides))
        if template_dir:
            loaders.append(FileSystemLoader(str(template_dir)))
        loaders.append(FileSystemLoader(str(TEMPLATE_DIR)))

        # Profiles are user-controlled files. The immutable sandbox blocks
        # Python internals and state mutation while retaining ordinary Jinja
        # expressions and the small formatting surface used by our templates.
        self.env = ImmutableSandboxedEnvironment(
            loader=ChoiceLoader(loaders),
            undefined=StrictUndefined,
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def template_for(self, contract: Contract, kind: StringKind) -> str:
        """Org-specific template if one exists, otherwise the default."""
        specific = f"orgs/{contract.org.id}/{kind.value}.j2"
        try:
            self.env.get_template(specific)
        except TemplateError:
            return f"{kind.value}.j2"
        return specific

    def render_key(self, contract: Contract, key: str) -> str:
        kind = contract.kind_of(key) or StringKind.DESC
        name = self.template_for(contract, kind)

        try:
            rendered = self.env.get_template(name).render(
                base=contract.base_text(key) or "",
                contract=contract,
                org=contract.org,
                reward=contract.reward,
                pools=self._visible_pools(contract),
                opts=self.options,
                em=self._emphasise,
                GateKind=GateKind,
            )
        except TemplateError as exc:
            raise TemplateRenderError(key, name, exc) from exc

        return self._to_ini_value(rendered)

    def render(self, contract: Contract) -> dict[str, str]:
        return {key: self.render_key(contract, key) for key in contract.all_keys()}

    def render_all(self, contracts: ContractSet) -> RenderResult:
        result = RenderResult()

        for contract in contracts.contracts:
            for key, value in self.render(contract).items():
                source_value = contract.base_text(key) or ""
                issues = validate_value(
                    value,
                    trusted_source=source_value,
                )
                source_warnings = {
                    issue
                    for issue in validate_value(source_value)
                    if issue.severity is Severity.WARNING
                }
                # Do not attribute CIG's existing tag-balance defects to the
                # generated output. New warnings and every error remain visible.
                issues = [
                    issue
                    for issue in issues
                    if not (
                        issue.severity is Severity.WARNING
                        and issue in source_warnings
                    )
                ]
                errors = [i for i in issues if i.severity is Severity.ERROR]
                if errors:
                    # Never emit a value that would break in-game, even if the
                    # user's own template produced it.
                    result.skipped.append((key, str(errors[0])))
                    continue

                result.values[key] = value
                kind = contract.kind_of(key) or StringKind.DESC
                result.provenance[key] = tuple(
                    dict.fromkeys(
                        (
                            *contract.evidence,
                            *self.options.mission_evidence(contract, kind, value),
                        )
                    )
                )
                result.warnings.extend((key, i) for i in issues)

        for entity in contracts.entities:
            rendered = self.options.render_entity(entity)
            if rendered is None:
                continue
            value, evidence = rendered
            key = entity.localization_key
            if key in result.values:
                if result.values[key] != value:
                    result.skipped.append(
                        (key, "entity localization collides with a contract key")
                    )
                continue
            issues = validate_value(value, trusted_source=entity.base_text)
            source_warnings = {
                issue
                for issue in validate_value(entity.base_text)
                if issue.severity is Severity.WARNING
            }
            issues = [
                issue
                for issue in issues
                if not (
                    issue.severity is Severity.WARNING and issue in source_warnings
                )
            ]
            errors = [issue for issue in issues if issue.severity is Severity.ERROR]
            if errors:
                result.skipped.append((key, str(errors[0])))
                continue
            result.values[key] = value
            result.provenance[key] = evidence
            result.warnings.extend((key, issue) for issue in issues)

        if self.options.legacy_mining_pack_enabled:
            for item in contracts.legacy_signatures:
                key = item.localization_key
                value = f"{item.base_text} (RS {item.signature})"
                if key in result.values:
                    if result.values[key] != value:
                        result.skipped.append(
                            (key, "legacy localization collides with another generated key")
                        )
                    continue
                issues = validate_value(value, trusted_source=item.base_text)
                errors = [
                    issue for issue in issues if issue.severity is Severity.ERROR
                ]
                if errors:
                    result.skipped.append((key, str(errors[0])))
                    continue
                result.values[key] = value
                result.provenance[key] = item.evidence
                result.warnings.extend((key, issue) for issue in issues)

        return result

    def _emphasise(self, text: str, field_name: str | None = None) -> str:
        tag = self.options.emphasis_for(field_name)
        return f"<{tag}>{text}</{tag}>"

    def _visible_pools(self, contract: Contract) -> list[BlueprintPool]:
        if not self.options.show_blueprints:
            return []

        visible = []
        for pool in contract.reward.blueprint_pools:
            if pool.example_locations and not self.options.show_regional_variants:
                continue
            if self.options.max_pool_items is not None:
                pool = BlueprintPool(
                    items=pool.items[: self.options.max_pool_items],
                    item_ids={
                        item: pool.item_ids[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_ids
                    },
                    item_categories={
                        item: pool.item_categories[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_categories
                    },
                    item_types={
                        item: pool.item_types[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_types
                    },
                    item_classes={
                        item: pool.item_classes[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_classes
                    },
                    item_sizes={
                        item: pool.item_sizes[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_sizes
                    },
                    item_grades={
                        item: pool.item_grades[item]
                        for item in pool.items[: self.options.max_pool_items]
                        if item in pool.item_grades
                    },
                    gates=pool.gates,
                    label=pool.label,
                    example_locations=pool.example_locations,
                    caveat=pool.caveat,
                    chance=pool.chance,
                    owned=pool.owned,
                )
            visible.append(pool)
        return visible

    @staticmethod
    def _to_ini_value(rendered: str) -> str:
        """Templates use real newlines; the file format needs literal escapes.

        Runs of blank lines are left alone -- they occur inside CIG's original
        prose, and collapsing them would edit text we were only meant to append
        to."""
        return _REAL_NEWLINE.sub(r"\\n", rendered.strip())
