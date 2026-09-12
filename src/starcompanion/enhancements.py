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
from .extract.mission_tactical import MissionTacticalResult
from .model import (
    BlueprintPool,
    ContractSet,
    Evidence,
    FactConfidence,
    MissionDetail,
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
    mission_details: tuple[MissionDetail, ...] = ()


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
        capability = _provider_capability(
            self.provider,
            self.version,
            raw,
            facts_seen=len(source.facts),
        )
        status = capability.status
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
                            item_types={
                                display: pool.item_types[position]
                                for display, position in resolved_items
                                if position < len(pool.item_types) and pool.item_types[position]
                            },
                            item_classes={
                                display: pool.item_classes[position]
                                for display, position in resolved_items
                                if position < len(pool.item_classes) and pool.item_classes[position]
                            },
                            item_sizes={
                                display: pool.item_sizes[position]
                                for display, position in resolved_items
                                if position < len(pool.item_sizes) and pool.item_sizes[position]
                            },
                            item_grades={
                                display: pool.item_grades[position]
                                for display, position in resolved_items
                                if position < len(pool.item_grades) and pool.item_grades[position]
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


class MissionTacticalEnhancementProvider:
    """Convert one independent G5 tactical result into cached contract facts."""

    def __init__(
        self,
        provider: str,
        version: str,
        resolve_text: Callable[[str], str | None] | None = None,
    ) -> None:
        if not provider.strip() or not version.strip():
            raise ValueError("tactical presentation providers require identity")
        self.provider = provider
        self.version = version
        self._resolve_text = resolve_text or (lambda _key: None)

    def build(self, source: object) -> EnhancementSet:
        if not isinstance(source, MissionTacticalResult):
            raise TypeError("tactical provider requires a MissionTacticalResult")
        if source.capability.provider != self.provider:
            raise ValueError("tactical provider result identity does not match")

        capability = _provider_capability(
            self.provider,
            self.version,
            source.capability,
            facts_seen=len(source.facts),
        )
        if capability.status is ProviderStatus.UNAVAILABLE:
            return EnhancementSet(self.provider, self.version, (), capability)

        enhancements: list[ContractEnhancement] = []
        missing_localizations = 0
        rejected_values = 0
        for fact in sorted(source.facts, key=lambda item: item.mission_id):
            details = []
            for value in fact.values:
                displayed = value.value
                requires_localization = (
                    value.name == "mission-type"
                    and isinstance(displayed, str)
                    and any(
                        (
                            isinstance(item.value, str)
                            and item.value.strip().startswith("@")
                        )
                        or item.field_path.rsplit(".", 1)[-1].casefold()
                        == "localisedtypename"
                        for item in value.evidence
                    )
                )
                if requires_localization:
                    localized = self._resolve_text(displayed)
                    if localized is None:
                        missing_localizations += 1
                        continue
                    displayed = localized
                evidence = tuple(
                    Evidence(
                        self.provider,
                        item.record_id,
                        item.record_path,
                        item.field_path,
                        item.value,
                    )
                    for item in value.evidence
                )
                try:
                    details.append(
                        MissionDetail(
                            value.name,
                            displayed,
                            FactConfidence(value.confidence.value),
                            evidence,
                        )
                    )
                except ValueError:
                    rejected_values += 1
            if not details:
                continue
            match_keys = tuple(fact.match_keys)
            enhancements.append(
                ContractEnhancement(
                    source_id=fact.mission_id,
                    match_keys=match_keys,
                    available_keys=tuple(
                        key
                        for key in match_keys
                        if key.casefold() not in _PLACEHOLDER_KEYS
                        and self._resolve_text(key) is not None
                    ),
                    placeholder_keys=tuple(
                        key for key in match_keys if key.casefold() in _PLACEHOLDER_KEYS
                    ),
                    mission_details=tuple(details),
                )
            )

        extra_diagnostics = []
        if missing_localizations:
            extra_diagnostics.append(
                f"warning:mission-type-localization-missing: "
                f"{missing_localizations} mission type values were not presented"
            )
        if rejected_values:
            extra_diagnostics.append(
                f"warning:tactical-presentation-value-rejected: "
                f"{rejected_values} unsafe or unsupported values were not presented"
            )
        if extra_diagnostics:
            capability = replace(
                capability,
                status=(
                    ProviderStatus.DEGRADED
                    if capability.status is ProviderStatus.AVAILABLE
                    else capability.status
                ),
                diagnostics=(*capability.diagnostics, *extra_diagnostics),
            )
        return EnhancementSet(
            self.provider,
            self.version,
            tuple(enhancements),
            capability,
        )


def _provider_capability(
    provider: str,
    version: str,
    raw,
    *,
    facts_seen: int,
) -> ProviderCapability:
    status = {
        RawCapabilityStatus.AVAILABLE: ProviderStatus.AVAILABLE,
        RawCapabilityStatus.DEGRADED: ProviderStatus.DEGRADED,
        RawCapabilityStatus.UNAVAILABLE: ProviderStatus.UNAVAILABLE,
    }[raw.status]
    diagnostics = tuple(
        f"{item.severity.value}:{item.code}: {item.message}"
        for item in raw.diagnostics
    )
    diagnostic_counts = tuple(
        sorted(Counter(item.category.value for item in raw.diagnostics).items())
    )
    return ProviderCapability(
        provider=provider,
        version=version,
        status=status,
        build_version=str(raw.build_version),
        facts_seen=facts_seen,
        diagnostics=diagnostics,
        diagnostic_counts=diagnostic_counts,
    )


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


def unavailable_tactical_enhancements(
    provider: str,
    version: str,
    build_version: str,
    reason: str,
) -> EnhancementSet:
    """Keep one missing tactical capability isolated from every peer."""

    return EnhancementSet(
        provider,
        version,
        (),
        ProviderCapability(
            provider=provider,
            version=version,
            status=ProviderStatus.UNAVAILABLE,
            build_version=build_version,
            diagnostics=(reason,),
        ),
    )


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
    blocked_mission_details: dict[int, set[str]] = {}
    for enhancement_set in sorted(enhancement_sets, key=lambda item: item.provider):
        matched_ids: set[int] = set()
        matched_sources: set[str] = set()
        unmatched: list[ContractEnhancement] = []
        unmatched_reasons: Counter[str] = Counter()
        evidence_count = 0
        mission_conflicts = 0
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
                    added, conflict = _extend_mission_details(
                        contract.mission_details,
                        enhancement.mission_details,
                        blocked_mission_details.setdefault(id(contract), set()),
                    )
                    evidence_count += added
                    mission_conflicts += conflict
                if not matches:
                    unmatched.append(enhancement)
                    reason = _unmatched_reason(enhancement)
                    unmatched_reasons[reason] += 1
        capability = enhancement_set.capability
        if mission_conflicts:
            capability = replace(
                capability,
                status=(
                    ProviderStatus.DEGRADED
                    if capability.status is ProviderStatus.AVAILABLE
                    else capability.status
                ),
                diagnostics=(
                    *capability.diagnostics,
                    "warning:mission-detail-conflict: "
                    f"{mission_conflicts} conflicting mission facts were suppressed",
                ),
            )
        capabilities.append(
            replace(
                capability,
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


def _extend_mission_details(
    target: list[MissionDetail],
    details: tuple[MissionDetail, ...],
    blocked: set[str],
) -> tuple[int, int]:
    evidence_added = 0
    conflicts = 0
    for detail in details:
        if detail.name in blocked:
            continue
        matching = [
            (index, current)
            for index, current in enumerate(target)
            if current.name == detail.name
        ]
        if matching:
            index, current = matching[0]
            if current.value != detail.value:
                del target[index]
                blocked.add(detail.name)
                conflicts += 1
                continue
            evidence = tuple(dict.fromkeys((*current.evidence, *detail.evidence)))
            confidence = min(
                (current.confidence, detail.confidence),
                key=lambda item: {
                    FactConfidence.NONE: 0,
                    FactConfidence.LOW: 1,
                    FactConfidence.MEDIUM: 2,
                    FactConfidence.HIGH: 3,
                }[item],
            )
            target[index] = replace(current, confidence=confidence, evidence=evidence)
            evidence_added += len(evidence) - len(current.evidence)
            continue
        target.append(detail)
        evidence_added += len(detail.evidence)
    return evidence_added, conflicts


__all__ = [
    "ContractEnhancement",
    "EnhancementProvider",
    "EnhancementSet",
    "Evidence",
    "MissionEnhancementProvider",
    "MissionTacticalEnhancementProvider",
    "apply_enhancements",
    "unavailable_mission_enhancements",
    "unavailable_tactical_enhancements",
]
