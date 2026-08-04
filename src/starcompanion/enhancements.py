"""Typed local enhancement providers and deterministic domain merging.

Raw extraction stays in :mod:`starcompanion.extract.dataforge`; this module is
the seam that converts those facts into presentation-ready contract rewards.
It performs no network access and has no dependency on templates or Qt.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Protocol, runtime_checkable

from .extract.dataforge import (
    CapabilityStatus as RawCapabilityStatus,
    MissionExtractionResult,
)
from .model import (
    BlueprintPool,
    ContractSet,
    Evidence,
    ProviderCapability,
    ProviderStatus,
    UnresolvedLocalization,
)
from .sources.naming import canonical_key

MISSION_PROVIDER = "local-dataforge-missions"
MISSION_PROVIDER_VERSION = "3"
_PLACEHOLDER_KEYS = frozenset({"loc_uninitialized", "loc_placeholder"})
_PLACEHOLDER_TEXT = frozenset({"<= uninitialized =>", "<= placeholder =>"})


@dataclass(frozen=True)
class ContractEnhancement:
    """Provider-neutral reward additions addressed by localization key."""

    source_id: str
    match_keys: tuple[str, ...]
    available_keys: tuple[str, ...] = ()
    placeholder_keys: tuple[str, ...] = ()
    reputation: tuple[int, ...] = ()
    blueprint_pools: tuple[BlueprintPool, ...] = ()
    item_rewards: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class EnhancementSet:
    provider: str
    version: str
    enhancements: tuple[ContractEnhancement, ...]
    capability: ProviderCapability


@runtime_checkable
class EnhancementProvider(Protocol):
    provider: str
    version: str

    def build(self, source: object) -> EnhancementSet: ...


class MissionEnhancementProvider:
    """Convert C1 mission facts into localized reward enhancements."""

    provider = MISSION_PROVIDER
    version = MISSION_PROVIDER_VERSION

    def __init__(self, resolve_text: Callable[[str], str | None] | None = None):
        self._resolve_text = resolve_text or (lambda _key: None)

    def build(self, source: object) -> EnhancementSet:
        if not isinstance(source, MissionExtractionResult):
            raise TypeError("mission provider requires a MissionExtractionResult")

        raw = source.capability
        status = {
            RawCapabilityStatus.AVAILABLE: ProviderStatus.AVAILABLE,
            RawCapabilityStatus.DEGRADED: ProviderStatus.DEGRADED,
            RawCapabilityStatus.UNAVAILABLE: ProviderStatus.UNAVAILABLE,
        }[raw.status]
        diagnostic_messages = tuple(
            f"{item.severity.value}:{item.code}: {item.message}"
            for item in raw.diagnostics
        )
        diagnostic_counts = tuple(
            sorted(Counter(item.category.value for item in raw.diagnostics).items())
        )
        capability = ProviderCapability(
            provider=self.provider,
            version=self.version,
            status=status,
            build_version=str(raw.build_version),
            facts_seen=len(source.facts),
            diagnostics=diagnostic_messages,
            diagnostic_counts=diagnostic_counts,
        )
        if status is ProviderStatus.UNAVAILABLE:
            return EnhancementSet(self.provider, self.version, (), capability)

        enhancements: list[ContractEnhancement] = []
        for fact in sorted(source.facts, key=lambda item: item.mission_id):
            match_keys = tuple(
                dict.fromkeys((*fact.title_keys, *fact.description_keys))
            )
            if not match_keys:
                continue
            pools = []
            for index, pool in enumerate(fact.blueprint_pools, 1):
                resolved_items = [
                    (display, position)
                    for position, key in enumerate(pool.items)
                    for display in self._display_items((key,))
                ]
                items = tuple(display for display, _position in resolved_items)
                if items:
                    pools.append(
                        BlueprintPool(
                            items=list(items),
                            item_ids={
                                display: pool.item_ids[position]
                                for display, position in resolved_items
                                if position < len(pool.item_ids)
                            },
                            item_categories={
                                display: pool.item_categories[position]
                                for display, position in resolved_items
                                if position < len(pool.item_categories)
                            },
                            label=(
                                f"Pool {index}"
                                if len(fact.blueprint_pools) > 1
                                else None
                            ),
                            chance=pool.chance,
                        )
                    )
            item_rewards = self._display_items(fact.item_rewards)
            if not (fact.reputation or pools or item_rewards):
                # A title-only C1 fact proves extraction coverage but does not
                # change presentation, so it is not a contract enhancement.
                continue
            evidence = tuple(
                Evidence(
                    self.provider,
                    item.record_id,
                    item.record_path,
                    item.field_path,
                    item.value,
                )
                for item in fact.evidence
            )
            enhancements.append(
                ContractEnhancement(
                    source_id=fact.mission_id,
                    match_keys=match_keys,
                    available_keys=tuple(
                        key
                        for key in match_keys
                        if key.casefold() not in {"loc_uninitialized", "loc_placeholder"}
                        and self._resolve_text(key) is not None
                    ),
                    placeholder_keys=tuple(
                        key
                        for key in match_keys
                        if key.casefold() in {"loc_uninitialized", "loc_placeholder"}
                    ),
                    reputation=tuple(fact.reputation),
                    blueprint_pools=tuple(pools),
                    item_rewards=item_rewards,
                    evidence=evidence,
                )
            )
        return EnhancementSet(
            self.provider,
            self.version,
            tuple(enhancements),
            replace(capability, reward_facts=len(enhancements)),
        )

    def _display_items(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        items: list[str] = []
        for key in keys:
            if key.casefold() in _PLACEHOLDER_KEYS:
                continue
            value = self._resolve_text(key) or key
            if value.strip().casefold() in _PLACEHOLDER_TEXT:
                continue
            if value not in items:
                items.append(value)
        return tuple(items)


def unavailable_mission_enhancements(
    build_version: str,
    reason: str,
) -> EnhancementSet:
    """Represent missing or unreadable local mission data without failing peers."""

    capability = ProviderCapability(
        provider=MISSION_PROVIDER,
        version=MISSION_PROVIDER_VERSION,
        status=ProviderStatus.UNAVAILABLE,
        build_version=build_version,
        diagnostics=(reason,),
    )
    return EnhancementSet(MISSION_PROVIDER, MISSION_PROVIDER_VERSION, (), capability)


def apply_enhancements(
    base: ContractSet,
    enhancement_sets: tuple[EnhancementSet, ...] | list[EnhancementSet],
) -> ContractSet:
    """Return a copy with enabled provider output merged deterministically."""

    result = copy.deepcopy(base)
    key_index: dict[str, list] = {}
    for contract in result.contracts:
        for key in contract.all_keys():
            key_index.setdefault(key.casefold(), []).append(contract)
            canonical = canonical_key(key).casefold()
            if canonical != key.casefold():
                key_index.setdefault(canonical, []).append(contract)

    capabilities: list[ProviderCapability] = []
    for enhancement_set in sorted(enhancement_sets, key=lambda item: item.provider):
        matched_ids: set[int] = set()
        matched_sources: set[str] = set()
        unmatched: list[ContractEnhancement] = []
        unmatched_reasons: Counter[str] = Counter()
        evidence_count = 0
        if enhancement_set.capability.status is not ProviderStatus.UNAVAILABLE:
            for enhancement in enhancement_set.enhancements:
                matches = []
                seen: set[int] = set()
                for key in enhancement.match_keys:
                    for contract in key_index.get(canonical_key(key).casefold(), ()):
                        identity = id(contract)
                        if identity not in seen:
                            seen.add(identity)
                            matches.append(contract)
                for contract in matches:
                    matched_ids.add(id(contract))
                    matched_sources.add(enhancement.source_id)
                    _extend_unique(contract.reward.reputation, enhancement.reputation)
                    _extend_pools(contract.reward.blueprint_pools, enhancement.blueprint_pools)
                    _extend_unique(contract.reward.item_rewards, enhancement.item_rewards)
                    before = len(contract.evidence)
                    _extend_unique(contract.evidence, enhancement.evidence)
                    evidence_count += len(contract.evidence) - before
                if not matches:
                    unmatched.append(enhancement)
                    reason = _unmatched_reason(enhancement)
                    unmatched_reasons[reason] += 1
        capabilities.append(
            replace(
                enhancement_set.capability,
                contracts_enhanced=len(matched_ids),
                evidence_links=evidence_count,
                matched_facts=len(matched_sources),
                unmatched_facts=len(unmatched),
                unmatched_samples=tuple(
                    f"{_unmatched_reason(item)}: "
                    f"{item.source_id}: {', '.join(item.match_keys[:3])}"
                    for item in unmatched[:10]
                ),
                unmatched_reason_counts=tuple(sorted(unmatched_reasons.items())),
                unresolved_localizations=tuple(
                    UnresolvedLocalization(
                        source_id=item.source_id,
                        reason=_unmatched_reason(item),
                        keys=item.match_keys,
                    )
                    for item in unmatched
                ),
            )
        )
    result.capabilities.extend(capabilities)
    return result


def _extend_unique(target: list, values) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _unmatched_reason(item: ContractEnhancement) -> str:
    if item.placeholder_keys and not item.available_keys:
        return "placeholder-key"
    if item.available_keys:
        return "contract-filtered"
    return "localization-missing"


def _extend_pools(target: list[BlueprintPool], pools: tuple[BlueprintPool, ...]) -> None:
    for pool in pools:
        identity = (pool.label, pool.chance, tuple(pool.items))
        if not any(
            (current.label, current.chance, tuple(current.items)) == identity
            for current in target
        ):
            target.append(copy.deepcopy(pool))


__all__ = [
    "ContractEnhancement",
    "EnhancementProvider",
    "EnhancementSet",
    "Evidence",
    "MissionEnhancementProvider",
    "apply_enhancements",
    "unavailable_mission_enhancements",
]
