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

from ..model import BlueprintPool, Contract, ContractSet, Evidence, GateKind, StringKind
from ..validate import EMPHASIS_TAGS, Issue, Severity, validate_value

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

_REAL_NEWLINE = re.compile(r"[ \t]*\r?\n")


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

    def emphasis_for(self, field_name: str | None) -> str:
        return self.emphasis_by_field.get(field_name or "", self.emphasis)

    def format_number(self, value: int) -> str:
        return f"{value:,}" if self.thousands_separator else str(value)

    def format_reputation(self, values: list[int]) -> str:
        return self.reputation_separator.join(
            self.format_number(value) for value in values
        )


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
                result.provenance[key] = tuple(contract.evidence)
                result.warnings.extend((key, i) for i in issues)

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
