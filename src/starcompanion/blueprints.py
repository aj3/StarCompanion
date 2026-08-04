"""Stable local blueprint catalog and read-only ownership queries.

The catalog is rebuilt from a C1/C2 contract cache.  It contains no player
state.  Player state is joined only by :func:`query_blueprints`, keeping a
game-build cache safe to delete and ownership safe from cache invalidation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from .model import ContractSet, Evidence

CATALOG_VERSION = 1
_WS = re.compile(r"\s+")
_LEADING_TAG = re.compile(r"^\[[^\]]{1,32}\]\s*")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def normalize_blueprint_name(name: str) -> str:
    """Canonical comparison form shared by logs, imports, and the catalog."""

    value = unicodedata.normalize("NFKC", str(name)).strip()
    value = _LEADING_TAG.sub("", value)
    return _WS.sub(" ", value).casefold()


def _fallback_id(name: str) -> str:
    digest = hashlib.sha256(normalize_blueprint_name(name).encode("utf-8")).hexdigest()
    return f"name-sha256:{digest}"


def _stable_id(value: str | None, name: str) -> tuple[str, bool]:
    candidate = (value or "").strip().casefold()
    if _UUID.fullmatch(candidate):
        return f"cig:{candidate}", False
    if candidate.startswith("path:") and len(candidate) > 5:
        digest = hashlib.sha256(candidate[5:].encode("utf-8")).hexdigest()
        return f"cig-path-sha256:{digest}", False
    return _fallback_id(name), True


@dataclass(frozen=True, order=True)
class RewardSource:
    contract_id: str
    org: str
    family: str
    pool: str = ""
    chance: float | None = None
    gates: tuple[str, ...] = ()


@dataclass
class CatalogEntry:
    blueprint_id: str
    name: str
    normalized_name: str
    category: str = "unknown"
    identity_fallback: bool = False
    aliases: set[str] = field(default_factory=set)
    reward_sources: set[RewardSource] = field(default_factory=set)
    evidence: set[Evidence] = field(default_factory=set)


@dataclass(frozen=True)
class BlueprintCatalog:
    entries: tuple[CatalogEntry, ...]
    version: int = CATALOG_VERSION

    @property
    def by_id(self) -> dict[str, CatalogEntry]:
        return {entry.blueprint_id: entry for entry in self.entries}

    @property
    def aliases(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, set[str]] = {}
        for entry in self.entries:
            for alias in {entry.name, *entry.aliases}:
                normalized = normalize_blueprint_name(alias)
                if normalized:
                    result.setdefault(normalized, set()).add(entry.blueprint_id)
        return {key: tuple(sorted(ids)) for key, ids in result.items()}

    def resolve_name(self, name: str) -> str | None:
        """Resolve only an unambiguous exact normalized alias; never fuzzy match."""

        matches = self.resolve_name_candidates(name)
        return matches[0] if len(matches) == 1 else None

    def resolve_name_candidates(self, name: str) -> tuple[str, ...]:
        return self.aliases.get(normalize_blueprint_name(name), ())


def build_catalog(contracts: ContractSet) -> BlueprintCatalog:
    """Build a deterministic catalog from locally extracted contract rewards."""

    entries: dict[str, CatalogEntry] = {}
    for contract in sorted(contracts.contracts, key=lambda item: item.id):
        for pool_index, pool in enumerate(contract.reward.blueprint_pools, 1):
            source = RewardSource(
                contract_id=contract.id,
                org=contract.org.name,
                family=contract.family,
                pool=pool.label or f"Pool {pool_index}",
                chance=pool.chance,
                gates=tuple(str(gate) for gate in pool.gates),
            )
            for name in pool.items:
                name = str(name).strip()
                if not name:
                    continue
                blueprint_id, fallback = _stable_id(pool.item_ids.get(name), name)
                entry = entries.get(blueprint_id)
                if entry is None:
                    entry = CatalogEntry(
                        blueprint_id=blueprint_id,
                        name=name,
                        normalized_name=normalize_blueprint_name(name),
                        category=pool.item_categories.get(name, "unknown").casefold(),
                        identity_fallback=fallback,
                    )
                    entries[blueprint_id] = entry
                elif name != entry.name:
                    entry.aliases.add(name)
                entry.reward_sources.add(source)
                raw_identity = pool.item_ids.get(name, "").casefold()
                entry.evidence.update(
                    evidence
                    for evidence in contract.evidence
                    if raw_identity
                    and (
                        evidence.record_id.casefold() == raw_identity
                        or str(evidence.value).casefold() == raw_identity
                    )
                )
    # Stock/community text can contribute the same item without an entity ID
    # before the DataForge provider contributes the CIG identity. Fold that
    # name-only source into a unique exact local identity; retain an explicit
    # fallback entry when zero or several candidates exist rather than guess.
    stable_names: dict[str, list[CatalogEntry]] = {}
    for entry in entries.values():
        if not entry.identity_fallback:
            stable_names.setdefault(entry.normalized_name, []).append(entry)
    for blueprint_id, fallback in tuple(entries.items()):
        if not fallback.identity_fallback:
            continue
        matches = stable_names.get(fallback.normalized_name, ())
        if len(matches) != 1:
            continue
        target = matches[0]
        target.aliases.update(fallback.aliases)
        target.reward_sources.update(fallback.reward_sources)
        target.evidence.update(fallback.evidence)
        entries.pop(blueprint_id)
    return BlueprintCatalog(tuple(entries[key] for key in sorted(entries)))


class OwnershipFilter(StrEnum):
    ALL = "all"
    OWNED = "owned"
    UNOWNED = "unowned"


@dataclass(frozen=True)
class BlueprintQuery:
    search: str = ""
    ownership: OwnershipFilter = OwnershipFilter.ALL
    reward_source: str = ""
    category: str = ""
    acquisition_source: str = ""


@dataclass(frozen=True)
class BlueprintRow:
    entry: CatalogEntry
    owned: bool
    acquired_at: str | None = None
    acquisition_sources: tuple[str, ...] = ()


def query_blueprints(
    catalog: BlueprintCatalog,
    ownership: object,
    query: BlueprintQuery = BlueprintQuery(),
) -> tuple[BlueprintRow, ...]:
    """Join a catalog to an ownership state without mutating either model."""

    records = getattr(ownership, "records", {})
    needle = normalize_blueprint_name(query.search)
    reward_needle = query.reward_source.casefold().strip()
    category = query.category.casefold().strip()
    acquisition = query.acquisition_source.casefold().strip()
    rows: list[BlueprintRow] = []
    for entry in catalog.entries:
        record = records.get(entry.blueprint_id)
        owned = bool(record)
        if query.ownership is OwnershipFilter.OWNED and not owned:
            continue
        if query.ownership is OwnershipFilter.UNOWNED and owned:
            continue
        if needle and needle not in entry.normalized_name and all(
            needle not in normalize_blueprint_name(alias) for alias in entry.aliases
        ):
            continue
        if category and entry.category != category:
            continue
        if reward_needle and not any(
            reward_needle in source.contract_id.casefold()
            or reward_needle in source.org.casefold()
            or reward_needle in source.family.casefold()
            for source in entry.reward_sources
        ):
            continue
        sources = tuple(sorted({item.source for item in getattr(record, "acquisitions", ())}))
        if acquisition and acquisition not in sources:
            continue
        acquired = min(
            (item.acquired_at for item in getattr(record, "acquisitions", ()) if item.acquired_at),
            default=None,
        )
        rows.append(BlueprintRow(entry, owned, acquired, sources))
    return tuple(sorted(rows, key=lambda row: (row.entry.name.casefold(), row.entry.blueprint_id)))


def categories(catalog: BlueprintCatalog) -> tuple[str, ...]:
    return tuple(sorted({entry.category for entry in catalog.entries}))


def reward_sources(catalog: BlueprintCatalog) -> tuple[str, ...]:
    return tuple(sorted({source.org for entry in catalog.entries for source in entry.reward_sources}))
