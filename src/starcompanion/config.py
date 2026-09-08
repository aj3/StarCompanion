"""User profiles: what appears in the game text and how it is formatted.

A profile is a versioned JSON document, so a set of preferences can be saved,
shared, and reloaded. Structural validation happens here; checks that need to
know the actual contract data (does this org exist?) are in
`Profile.validate_against`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .inject import MergeMode
from .model import ContractSet
from .render.renderer import (
    Field as RenderField,
    RenderLabels,
    RenderOptions,
    Renderer,
    Section,
    TitlePrefix,
    validate_wording_label,
)
from .validate import EMPHASIS_TAGS

SCHEMA_VERSION = 2

PROFILE_DIR = Path(__file__).parent / "profiles"

EmphasisTag = Annotated[str, Field(description="A tag the game can render")]


class UnsupportedProfileVersion(ValueError):
    def __init__(self, found: object):
        super().__init__(
            f"profile schema_version {found!r} is not supported by this build "
            f"(expected {SCHEMA_VERSION}). "
            f"{'Upgrade StarCompanion to read it.' if isinstance(found, int) and found > SCHEMA_VERSION else 'Re-save it from a newer profile, or edit schema_version by hand.'}"
        )


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FieldToggles(Strict):
    """Which reward information is shown at all."""

    reputation: bool = True
    blueprints: bool = True
    item_rewards: bool = True
    scenario_points: bool = True
    scrip: bool = True
    rank_gates: bool = True
    regional_variants: bool = True
    caveats: bool = True
    owned: bool = True
    """Mark blueprints already held, when a SCMDB export has been loaded."""


class TitleFormatting(Strict):
    bracket_rep: bool = True
    bracket_bp: bool = True
    prefix: Literal["none", "org", "rank", "org_rank"] = TitlePrefix.NONE


class Formatting(Strict):
    emphasis: EmphasisTag = "EM4"
    by_field: dict[str, EmphasisTag] = Field(default_factory=dict)
    """Per-field emphasis overrides, keyed by render field name."""
    max_pool_items: int | None = Field(default=None, ge=1)
    title: TitleFormatting = Field(default_factory=TitleFormatting)

    @field_validator("emphasis")
    @classmethod
    def _known_tag(cls, value: str) -> str:
        if value not in EMPHASIS_TAGS:
            raise ValueError(
                f"{value!r} is not renderable in-game; choose one of {sorted(EMPHASIS_TAGS)}"
            )
        return value

    @field_validator("by_field")
    @classmethod
    def _known_fields_and_tags(cls, value: dict[str, str]) -> dict[str, str]:
        for name, tag in value.items():
            if name not in RenderField.ALL:
                raise ValueError(
                    f"unknown field {name!r}; choose from {sorted(RenderField.ALL)}"
                )
            if tag not in EMPHASIS_TAGS:
                raise ValueError(
                    f"{tag!r} is not renderable in-game; choose one of {sorted(EMPHASIS_TAGS)}"
                )
        return value


class WordingLabels(Strict):
    """Safe plain-text labels for generated contract facts."""

    reputation: str = "Reputation Awarded"
    scrip: str = "MG Scrip"
    items: str = "Item Rewards"
    scenario: str = "Scenario Progress Points"
    blueprints: str = "Potential Blueprints"
    multiple_blueprints: str = "Multiple Blueprint Pools"
    chance: str = "Award chance"
    regional: str = "[Regional Variants] example locations"
    owned: str = "Owned"

    @field_validator(
        "reputation",
        "scrip",
        "items",
        "scenario",
        "blueprints",
        "multiple_blueprints",
        "chance",
        "regional",
        "owned",
    )
    @classmethod
    def _safe_label(cls, value: str) -> str:
        return validate_wording_label(value)


SectionName = Literal["reputation", "scrip", "items", "scenario", "blueprints"]


class StructuredWording(Strict):
    """Typed wording controls; templates remain an explicit advanced mode."""

    mode: Literal["structured", "advanced"] = "structured"
    section_order: tuple[SectionName, ...] = Section.ALL
    labels: WordingLabels = Field(default_factory=WordingLabels)
    reputation_separator: Literal[" / ", "/", " • "] = " / "
    thousands_separator: bool = True

    @field_validator("section_order")
    @classmethod
    def _complete_section_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(Section.ALL) or set(value) != set(Section.ALL):
            raise ValueError("section order must contain every reward section exactly once")
        return value


class Appearance(Strict):
    """How the interface looks. Additive with a default, so profiles written
    before this existed still load."""

    theme: Literal["dark", "light"] = "dark"


class OrgTemplates(Strict):
    """Inline Jinja overriding the defaults for one mission giver."""

    title: str | None = None
    desc: str | None = None


class Injection(Strict):
    mode: Literal["merge", "overwrite"] = MergeMode.MERGE.value
    backup: bool = True

    @property
    def merge_mode(self) -> MergeMode:
        return MergeMode(self.mode)


class Profile(Strict):
    schema_version: Literal[2] = SCHEMA_VERSION
    name: str = "default"
    description: str = ""
    fields: FieldToggles = Field(default_factory=FieldToggles)
    formatting: Formatting = Field(default_factory=Formatting)
    wording: StructuredWording = Field(default_factory=StructuredWording)
    appearance: Appearance = Field(default_factory=Appearance)
    templates: dict[str, OrgTemplates] = Field(default_factory=dict)
    """Keyed by org id (casefolded), matching `Org.id`."""
    injection: Injection = Field(default_factory=Injection)

    @model_validator(mode="before")
    @classmethod
    def _preserve_direct_template_profiles(cls, value):
        if isinstance(value, dict) and value.get("templates") and "wording" not in value:
            value = dict(value)
            value["wording"] = {"mode": "advanced"}
        return value

    # --- persistence ---------------------------------------------------------

    @classmethod
    def loads(cls, text: str) -> Profile:
        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate profile key {key!r}")
                result[key] = value
            return result

        data = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(data, dict):
            raise ValueError("profile JSON must contain one object")
        found = data.get("schema_version", SCHEMA_VERSION)
        if found == 1:
            data["schema_version"] = SCHEMA_VERSION
            data["wording"] = {
                "mode": "advanced" if data.get("templates") else "structured"
            }
            found = SCHEMA_VERSION
        if found != SCHEMA_VERSION:
            # Checked before model validation so the message names the real
            # problem instead of a confusing Literal mismatch.
            raise UnsupportedProfileVersion(found)
        return cls.model_validate(data)

    @classmethod
    def load(cls, path: Path) -> Profile:
        return cls.loads(path.read_text(encoding="utf-8"))

    def dumps(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2) + "\n"

    def save(self, path: Path) -> None:
        path.write_text(self.dumps(), encoding="utf-8")

    # --- use -----------------------------------------------------------------

    def to_render_options(self) -> RenderOptions:
        return RenderOptions(
            show_reputation=self.fields.reputation,
            show_blueprints=self.fields.blueprints,
            show_item_rewards=self.fields.item_rewards,
            show_scenario_points=self.fields.scenario_points,
            show_scrip=self.fields.scrip,
            show_rank_gates=self.fields.rank_gates,
            show_regional_variants=self.fields.regional_variants,
            show_caveats=self.fields.caveats,
            show_owned=self.fields.owned,
            emphasis=self.formatting.emphasis,
            emphasis_by_field=dict(self.formatting.by_field),
            title_bracket_rep=self.formatting.title.bracket_rep,
            title_bracket_bp=self.formatting.title.bracket_bp,
            title_prefix=self.formatting.title.prefix,
            max_pool_items=self.formatting.max_pool_items,
            section_order=tuple(self.wording.section_order),
            labels=RenderLabels(**self.wording.labels.model_dump()),
            reputation_separator=self.wording.reputation_separator,
            thousands_separator=self.wording.thousands_separator,
        )

    def template_overrides(self) -> dict[str, str]:
        """Inline templates in the loader's naming scheme."""
        if self.wording.mode != "advanced":
            return {}
        overrides: dict[str, str] = {}
        for org_id, templates in self.templates.items():
            if templates.title:
                overrides[f"orgs/{org_id}/title.j2"] = templates.title
            if templates.desc:
                overrides[f"orgs/{org_id}/desc.j2"] = templates.desc
        return overrides

    def build_renderer(self, *, template_dir: Path | None = None) -> Renderer:
        return Renderer(
            self.to_render_options(),
            template_dir=template_dir,
            overrides=self.template_overrides() or None,
        )

    def validate_against(self, contracts: ContractSet) -> list[str]:
        """Checks needing real data. Returns human-readable problems."""
        return [
            f"profile has a template for unknown org {org_id!r}"
            for org_id in self.templates
            if org_id not in contracts.orgs
        ]


def builtin_profiles() -> dict[str, Path]:
    if not PROFILE_DIR.is_dir():
        return {}
    return {path.stem: path for path in sorted(PROFILE_DIR.glob("*.json"))}


def load_builtin(name: str) -> Profile:
    profiles = builtin_profiles()
    if name not in profiles:
        raise KeyError(f"no built-in profile {name!r}; have {sorted(profiles)}")
    return Profile.load(profiles[name])


__all__ = [
    "SCHEMA_VERSION",
    "Appearance",
    "Formatting",
    "FieldToggles",
    "Injection",
    "OrgTemplates",
    "Profile",
    "StructuredWording",
    "TitleFormatting",
    "UnsupportedProfileVersion",
    "ValidationError",
    "WordingLabels",
    "builtin_profiles",
    "load_builtin",
]
