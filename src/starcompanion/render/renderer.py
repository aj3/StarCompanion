"""Turn contracts into localization values via user-editable templates.

Templates are written with real newlines for readability; the renderer converts
them to the literal ``\\n`` escapes the game requires as the last step, so a
template author cannot accidentally emit a line break that blanks a contract
in-game.
"""

from __future__ import annotations

import re
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

    def emphasis_for(self, field_name: str | None) -> str:
        return self.emphasis_by_field.get(field_name or "", self.emphasis)


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
