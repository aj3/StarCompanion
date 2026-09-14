"""Domain model for contracts and their rewards.

Deliberately free of both game-file and .ini concepts: this is the seam that
lets the datamined extractor replace the contracts.ini importer without
anything downstream noticing.

Unknown data is represented as None, never guessed. A contract whose reward we
could not parse is a contract with no reward, not a contract with zero rep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import math
import re
import unicodedata


class Difficulty(Enum):
    """CIG's per-contract difficulty codes, ordered easiest to hardest."""

    VERY_EASY = ("VE", 1, "Yellow")
    EASY = ("E", 2, "Yellow")
    MEDIUM = ("M", 3, "Orange")
    HARD = ("H", 4, "Orange")
    VERY_HARD = ("VH", 5, "Red")
    SUPERIOR = ("S", 6, "Red")

    def __init__(self, code: str, rank: int, colour: str):
        self.code = code
        self.rank = rank
        self.colour = colour

    @classmethod
    def from_code(cls, code: str) -> Difficulty | None:
        return _BY_CODE.get(code.upper())

    def __lt__(self, other: Difficulty) -> bool:
        return self.rank < other.rank


_BY_CODE = {d.code: d for d in Difficulty}


class GateKind(Enum):
    """Why a blueprint pool might not drop for you."""

    RANK = "rank"
    """Reputation tier, e.g. 'Jr. Contractor'."""
    FACTION = "faction"
    """Specific mission giver, e.g. 'BitZeros'."""
    REGION = "region"
    """Location, e.g. 'Nyx'."""
    REPEAT = "repeat"
    """Only on repeat runs, not the intro."""


@dataclass(frozen=True)
class Gate:
    kind: GateKind
    label: str

    def __str__(self) -> str:
        return self.label


class StringKind(Enum):
    TITLE = "title"
    DESC = "desc"


class ProviderStatus(Enum):
    """Whether an enhancement provider can safely contribute this build."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class FactConfidence(Enum):
    """Presentation confidence copied from one reviewed local provider fact."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class UnresolvedLocalization:
    """One provider fact that could not join because localization is absent."""

    source_id: str
    reason: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    """One traceable source for a generated enhancement value."""

    provider: str
    record_id: str
    record_path: str
    field_path: str
    value: str | int | float | bool | None = None


ENTITY_DISPLAY_KINDS = frozenset(
    {
        "vehicle",
        "component",
        "ship-weapon",
        "fps-weapon",
        "medical",
        "commodity",
        "crafting",
        "missile",
    }
)
_SAFE_ATTRIBUTE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SAFE_LOCALIZATION_KEY = re.compile(r"^[^\s=\x00-\x1f\x7f]{1,512}$")
_SAFE_MISSION_VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_SAFE_MISSION_TOKEN = re.compile(
    r"^~mission\([A-Za-z][A-Za-z0-9_]*(?:\|[^)\r\n\x00]{1,128})?\)$"
)


def _safe_evidenced_scalar(value: object) -> bool:
    if type(value) is bool:
        return True
    if type(value) is int:
        return -1_000_000_000_000 <= value <= 1_000_000_000_000
    if type(value) is float:
        return math.isfinite(value) and abs(value) <= 1_000_000_000_000
    if isinstance(value, str):
        return (
            bool(value)
            and value == value.strip()
            and len(value) <= 256
            and not any(character in value for character in "<>\r\n\0")
            and not any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                for character in value
            )
        )
    return False


@dataclass(frozen=True)
class EntityAttribute:
    """One typed entity value retained specifically for local presentation."""

    name: str
    value: str | int | float | bool
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _SAFE_ATTRIBUTE.fullmatch(self.name):
            raise ValueError("invalid entity attribute name")
        if not _safe_evidenced_scalar(self.value):
            raise ValueError("entity attribute value is unsafe or outside bounds")
        if not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("entity attributes require evidence")


@dataclass(frozen=True)
class LocalizedEntity:
    """A strict local entity-to-display-name join used by the Tag Builder."""

    entity_id: str
    kind: str
    localization_key: str
    base_text: str
    attributes: tuple[EntityAttribute, ...]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not self.entity_id or len(self.entity_id) > 512:
            raise ValueError("invalid localized entity identity")
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in self.entity_id):
            raise ValueError("invalid localized entity identity")
        if self.kind not in ENTITY_DISPLAY_KINDS:
            raise ValueError("unsupported localized entity kind")
        if not _SAFE_LOCALIZATION_KEY.fullmatch(self.localization_key):
            raise ValueError("invalid entity localization key")
        if (
            not isinstance(self.base_text, str)
            or not self.base_text
            or len(self.base_text) > 32_768
            or any(character in self.base_text for character in "\r\n\0")
        ):
            raise ValueError("invalid entity localization value")
        if not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("localized entities require join evidence")
        names = [item.name for item in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("localized entity attributes must be unique")

    def attribute(self, name: str) -> EntityAttribute | None:
        wanted = name.casefold()
        return next(
            (item for item in self.attributes if item.name.casefold() == wanted),
            None,
        )


@dataclass(frozen=True)
class LegacyMiningSignature:
    """One exact-key community signature accepted for one reviewed build."""

    rule_id: str
    supported_build: str
    localization_key: str
    base_text: str
    signature: int
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not _SAFE_ATTRIBUTE.fullmatch(self.rule_id):
            raise ValueError("invalid legacy rule identity")
        if (
            not self.supported_build
            or len(self.supported_build) > 128
            or any(character in self.supported_build for character in "\r\n\0")
        ):
            raise ValueError("invalid legacy rule build")
        if not _SAFE_LOCALIZATION_KEY.fullmatch(self.localization_key):
            raise ValueError("invalid legacy localization key")
        if (
            not self.base_text
            or len(self.base_text) > 256
            or self.base_text != self.base_text.strip()
            or any(character in self.base_text for character in "<>\r\n\0")
        ):
            raise ValueError("invalid legacy base text")
        if type(self.signature) is not int or not 1 <= self.signature <= 999_999:
            raise ValueError("legacy mining signature is outside its reviewed range")
        if not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("legacy mining signatures require source evidence")


@dataclass(frozen=True)
class LegacyPresentationRule:
    """One exact-build/key/stock wording result with source evidence."""

    rule_id: str
    supported_build: str
    localization_key: str
    base_text: str
    base_text_sha256: str
    replacement_text: str
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not _SAFE_ATTRIBUTE.fullmatch(self.rule_id):
            raise ValueError("invalid legacy presentation rule identity")
        if (
            not self.supported_build
            or len(self.supported_build) > 128
            or any(character in self.supported_build for character in "\r\n\0")
        ):
            raise ValueError("invalid legacy presentation build")
        if not _SAFE_LOCALIZATION_KEY.fullmatch(self.localization_key):
            raise ValueError("invalid legacy presentation localization key")
        if (
            not self.base_text
            or len(self.base_text) > 32_768
            or any(character in self.base_text for character in "\r\n\0")
        ):
            raise ValueError("invalid legacy presentation stock value")
        if not re.fullmatch(r"[0-9a-f]{64}", self.base_text_sha256):
            raise ValueError("legacy presentation rules require a SHA-256 stock binding")
        if hashlib.sha256(self.base_text.encode("utf-8")).hexdigest() != self.base_text_sha256:
            raise ValueError("legacy presentation stock value does not match its SHA-256")
        if (
            not self.replacement_text
            or len(self.replacement_text) > 32_768
            or any(character in self.replacement_text for character in "\r\n\0")
        ):
            raise ValueError("invalid legacy presentation replacement")
        if not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("legacy presentation rules require source evidence")


@dataclass(frozen=True)
class RouteExpansion:
    """One bounded, one-level stock localization expansion for a mission token."""

    variable: str
    tokens: tuple[str, ...]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not _SAFE_MISSION_VARIABLE.fullmatch(self.variable):
            raise ValueError("invalid nested mission variable")
        if (
            not self.tokens
            or len(self.tokens) > 64
            or len(self.tokens) != len(set(self.tokens))
            or any(not _SAFE_MISSION_TOKEN.fullmatch(item) for item in self.tokens)
        ):
            raise ValueError("invalid nested mission-token expansion")
        if (
            not self.evidence
            or len(self.evidence) > 4_096
            or any(not isinstance(item, Evidence) for item in self.evidence)
        ):
            raise ValueError("nested mission-token expansion requires bounded evidence")


MISSION_DETAIL_NAMES = frozenset(
    {
        "mission-type",
        "difficulty",
        "difficulty-risk",
        "difficulty-knowledge",
        "difficulty-mental-load",
        "difficulty-mechanical-skill",
        "friendly-spawns",
        "hostile-spawns",
        "ace-pilot",
        "ace-probability",
        "turret-count",
        "engagement-distance",
        "engagement-type",
    }
)
_TEXT_MISSION_DETAILS = MISSION_DETAIL_NAMES - {
    "friendly-spawns",
    "hostile-spawns",
    "ace-pilot",
    "ace-probability",
    "turret-count",
    "engagement-distance",
}
_COUNT_MISSION_DETAILS = frozenset(
    {"friendly-spawns", "hostile-spawns", "turret-count"}
)


@dataclass(frozen=True)
class MissionDetail:
    """One safe, typed mission fact with its complete local evidence."""

    name: str
    value: str | int | float | bool
    confidence: FactConfidence
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in MISSION_DETAIL_NAMES:
            raise ValueError(f"unsupported mission detail {self.name!r}")
        if not isinstance(self.confidence, FactConfidence):
            raise ValueError("mission detail confidence must be typed")
        if not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("mission details require provenance evidence")
        if self.name in _TEXT_MISSION_DETAILS:
            if not isinstance(self.value, str):
                raise ValueError("text mission details require text values")
            if (
                not self.value.strip()
                or self.value != self.value.strip()
                or len(self.value) > 256
                or any(character in self.value for character in "<>\r\n\0")
                or any(
                    unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                    for character in self.value
                )
            ):
                raise ValueError("mission detail text is empty, unsafe, or oversized")
        elif self.name in _COUNT_MISSION_DETAILS:
            if type(self.value) is not int or not 0 <= self.value <= 100_000:
                raise ValueError("mission detail count is outside its reviewed range")
        elif self.name == "ace-pilot":
            if type(self.value) is not bool:
                raise ValueError("ace-pilot mission detail must be boolean")
        elif self.name == "ace-probability":
            if (
                type(self.value) is not float
                or not math.isfinite(self.value)
                or not 0 <= self.value <= 1
            ):
                raise ValueError("ace probability is outside its reviewed range")
        elif (
            type(self.value) is not float
            or not math.isfinite(self.value)
            or not 0 <= self.value <= 1_000_000_000
        ):
            raise ValueError("engagement distance is outside its reviewed range")


@dataclass(frozen=True)
class ProviderCapability:
    """Stable, cacheable provider health and coverage summary."""

    provider: str
    version: str
    status: ProviderStatus
    build_version: str
    facts_seen: int = 0
    contracts_enhanced: int = 0
    evidence_links: int = 0
    diagnostics: tuple[str, ...] = ()
    reward_facts: int = 0
    matched_facts: int = 0
    unmatched_facts: int = 0
    unmatched_samples: tuple[str, ...] = ()
    unmatched_reason_counts: tuple[tuple[str, int], ...] = ()
    diagnostic_counts: tuple[tuple[str, int], ...] = ()
    unresolved_localizations: tuple[UnresolvedLocalization, ...] = ()


@dataclass
class ScenarioPoints:
    """Event scenario progress, e.g. Return of XenoThreat."""

    amount: int
    split: bool = False
    """True when the award is shared across all participants."""


@dataclass
class BlueprintPool:
    items: list[str] = field(default_factory=list)
    item_ids: dict[str, str] = field(default_factory=dict)
    """Display name -> stable local DataForge blueprint-record identity.

    Older/community sources may not provide an identity.  Catalog construction
    then uses an explicit name-derived fallback instead of pretending the name
    is a CIG record identifier.
    """
    item_categories: dict[str, str] = field(default_factory=dict)
    """Display name -> conservative category derived from the entity path."""
    item_types: dict[str, str] = field(default_factory=dict)
    item_classes: dict[str, str] = field(default_factory=dict)
    item_sizes: dict[str, str] = field(default_factory=dict)
    item_grades: dict[str, str] = field(default_factory=dict)
    """Display name -> explicit local DataForge metadata when available."""
    gates: list[Gate] = field(default_factory=list)
    """All conditions that must hold. Real pools stack them -- 'BitZeros Only'
    *and* 'Neutral level variants' is one pool with two gates. Empty means
    unconditional, not unknown."""
    label: str | None = None
    """e.g. 'Pool 2' when a contract draws from several pools."""
    example_locations: list[str] = field(default_factory=list)
    """Sample spawn locations for a regional variant."""
    caveat: str | None = None
    """Known-unreliable warning carried from the source data."""
    chance: float | None = None
    """Observed award probability in the local game data, when present."""
    owned: set[str] = field(default_factory=set)
    """Items the player already has, from their own SCMDB export. Empty means
    unknown, not 'owns nothing'."""

    @property
    def is_gated(self) -> bool:
        return bool(self.gates) or (self.chance is not None and self.chance < 1.0)

    def is_owned(self, item: str) -> bool:
        return item in self.owned

    @property
    def fully_owned(self) -> bool:
        """True only when ownership is known and covers every item."""
        return bool(self.items) and bool(self.owned) and set(self.items) <= self.owned

    def gate_of(self, kind: GateKind) -> Gate | None:
        return next((g for g in self.gates if g.kind is kind), None)

    @property
    def rank_gate(self) -> Gate | None:
        return self.gate_of(GateKind.RANK)


@dataclass
class Reward:
    reputation: list[int] = field(default_factory=list)
    """One value per difficulty variant; may be negative (a rep loss)."""
    scenario_points: list[ScenarioPoints] = field(default_factory=list)
    scrip: bool = False
    """True when the contract pays MG Scrip; the amount is dynamic in-game."""
    blueprint_pools: list[BlueprintPool] = field(default_factory=list)
    item_rewards: list[str] = field(default_factory=list)
    """Direct item awards resolved through the local localization table."""

    @property
    def is_empty(self) -> bool:
        return not (
            self.reputation
            or self.scenario_points
            or self.scrip
            or self.blueprint_pools
            or self.item_rewards
        )

    @property
    def reputation_display(self) -> str | None:
        """Spaced and thousands-separated, for description bodies."""
        if not self.reputation:
            return None
        return " / ".join(f"{v:,}" for v in self.reputation)

    @property
    def reputation_compact(self) -> str | None:
        """Unpunctuated, for title bracket tags where space is tight."""
        if not self.reputation:
            return None
        return "/".join(str(v) for v in self.reputation)

    @property
    def reputation_varies(self) -> bool:
        """True when the award depends on the variant rolled."""
        return len(self.reputation) > 1

    @property
    def awards_blueprints(self) -> bool:
        return any(p.items for p in self.blueprint_pools)

    @property
    def blueprints_conditional(self) -> bool:
        """True when a pool exists but will not drop for everyone -- the
        distinction the source data marks with `[BP]*`."""
        return any(p.items and p.is_gated for p in self.blueprint_pools)


@dataclass
class Org:
    """A mission giver. `id` is casefolded; source data spells these
    inconsistently (headhunters / Headhunters / HeadHunters are one org)."""

    id: str
    name: str
    rank_ladder: list[str] = field(default_factory=list)
    """Observed reputation tiers, ascending where order is known."""

    def rank_index(self, label: str) -> int | None:
        try:
            return self.rank_ladder.index(label)
        except ValueError:
            return None


@dataclass
class Contract:
    id: str
    """Stable identity: the shared key base, e.g. 'Foxwell_ShipAmbush_VE'."""
    org: Org
    family: str
    """Mission type within the org, e.g. 'ShipAmbush'."""
    keys: dict[StringKind, list[str]] = field(default_factory=dict)
    """Localization keys per kind. Plural because CIG ships alternate
    phrasings (`_001`/`_002`/`_003`) the game picks between at runtime; they
    share a contract but each still needs its own override written. Either
    kind may be absent -- many real entries have no partner."""
    texts: dict[str, str] = field(default_factory=dict)
    """Source text per key, since variants differ."""
    base_texts: dict[str, str] = field(default_factory=dict)
    """Text with any reward annotation removed -- what the renderer builds on.

    When the source is already-annotated output (the interim contracts.ini
    importer) this is recovered by stripping. Once contracts come from the game
    files it is simply the stock string."""
    difficulty: Difficulty | None = None
    reward: Reward = field(default_factory=Reward)
    evidence: list[Evidence] = field(default_factory=list)
    """Provider evidence contributing generated fields on this contract."""
    mission_details: list[MissionDetail] = field(default_factory=list)
    """Independent G5 tactical facts; presentation remains profile-controlled."""
    route_expansions: list[RouteExpansion] = field(default_factory=list)
    """One-level stock token indirections retained without caching all strings."""

    @property
    def rank(self) -> int | None:
        return self.difficulty.rank if self.difficulty else None

    @property
    def colour(self) -> str | None:
        return self.difficulty.colour if self.difficulty else None

    def key(self, kind: StringKind) -> str | None:
        """The first key of this kind, for display."""
        found = self.keys.get(kind)
        return found[0] if found else None

    def keys_of(self, kind: StringKind) -> list[str]:
        return list(self.keys.get(kind, ()))

    def all_keys(self) -> list[str]:
        return [key for keys in self.keys.values() for key in keys]

    def text(self, key: str) -> str | None:
        return self.texts.get(key)

    def base_text(self, key: str) -> str | None:
        return self.base_texts.get(key, self.texts.get(key))

    def kind_of(self, key: str) -> StringKind | None:
        return next((kind for kind, keys in self.keys.items() if key in keys), None)

    def mission_detail(self, name: str) -> MissionDetail | None:
        wanted = name.casefold()
        return next(
            (item for item in self.mission_details if item.name.casefold() == wanted),
            None,
        )

    def route_expansion(self, variable: str) -> RouteExpansion | None:
        wanted = variable.casefold()
        return next(
            (
                item
                for item in self.route_expansions
                if item.variable.casefold() == wanted
            ),
            None,
        )

    @property
    def title(self) -> str | None:
        key = self.key(StringKind.TITLE)
        return self.texts.get(key) if key else None

    @property
    def desc(self) -> str | None:
        key = self.key(StringKind.DESC)
        return self.texts.get(key) if key else None

    @property
    def has_variants(self) -> bool:
        return any(len(keys) > 1 for keys in self.keys.values())


@dataclass
class ContractSet:
    """Everything a source produced, plus what it could not make sense of."""

    contracts: list[Contract] = field(default_factory=list)
    orgs: dict[str, Org] = field(default_factory=dict)
    unparsed: list[tuple[str, str]] = field(default_factory=list)
    """(key, reason) — surfaced, never silently dropped."""
    capabilities: list[ProviderCapability] = field(default_factory=list)
    """Independent enhancement-provider health reports for this build."""
    entities: list[LocalizedEntity] = field(default_factory=list)
    """Strict local entity/name joins retained for opt-in typed presentation."""
    legacy_signatures: list[LegacyMiningSignature] = field(default_factory=list)
    """Exact-build community mining facts retained for explicit opt-in use."""
    legacy_presentations: list[LegacyPresentationRule] = field(default_factory=list)
    """Exact-build/key/stock legacy wording retained for explicit opt-in use."""

    def by_org(self, org_id: str) -> list[Contract]:
        return [c for c in self.contracts if c.org.id == org_id.casefold()]

    def by_key(self, key: str) -> Contract | None:
        return next((c for c in self.contracts if key in c.all_keys()), None)

    def sorted_by_rank(self) -> list[Contract]:
        """Rank ascending, unranked last."""
        return sorted(
            self.contracts,
            key=lambda c: (c.rank is None, c.rank or 0, c.org.name, c.family),
        )
