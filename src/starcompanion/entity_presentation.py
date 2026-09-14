"""Strict joins from G5 entity facts to opt-in localization presentation."""

from __future__ import annotations

from collections import Counter
from .extract.dataforge import CapabilityStatus
from .extract.entities import (
    EntityCatalogResult,
    EntityFact,
    EntityKind,
    presentation_entity_providers,
)
from .ini import LocalizationFile
from .model import (
    ContractSet,
    EntityAttribute,
    Evidence,
    LocalizedEntity,
    ProviderCapability,
    ProviderStatus,
)

PRESENTATION_PROVIDER = "local-entity-presentation"
PRESENTATION_VERSION = "2"
_TAG_ATTRIBUTES = (
    "subtype",
    "tracking-signal",
    "size",
    "grade",
    "class",
    "mass",
    "cargo-capacity",
    "crew-min",
    "crew-max",
    "damage",
    "rate-of-fire",
    "projectile-speed",
    "range",
    "magazine-capacity",
    "effective-range",
    "health-restored",
    "max-health-repair-rate",
    "max-auto-dose",
    "overdose-threshold",
    "toxicity",
    "base-price",
    "shop-buy-price",
    "shop-sell-price",
)
_NUMERIC_BOUNDS = {
    "size": (0, 100),
    "mass": (0, 1_000_000_000_000),
    "cargo-capacity": (0, 1_000_000_000),
    "crew-min": (0, 10_000),
    "crew-max": (0, 10_000),
    "damage": (0, 1_000_000_000),
    "rate-of-fire": (0, 1_000_000),
    "projectile-speed": (0, 1_000_000_000),
    "range": (0, 1_000_000_000),
    "magazine-capacity": (0, 1_000_000),
    "effective-range": (0, 1_000_000_000),
    "health-restored": (0, 1_000_000_000),
    "max-health-repair-rate": (0, 1_000_000_000),
    "max-auto-dose": (0, 1_000_000_000),
    "overdose-threshold": (0, 1_000_000_000),
    "toxicity": (0, 1_000_000_000),
    "base-price": (0, 1_000_000_000_000),
    "shop-buy-price": (0, 1_000_000_000_000),
    "shop-sell-price": (0, 1_000_000_000_000),
}
_TRACKING_SIGNALS = frozenset({"CrossSection", "Electromagnetic", "Infrared"})
_PATH_SUBTYPES = {
    "component": (
        ("/armor/", "Armor"),
        ("/cooler/", "Cooler"),
        ("/fuel_intakes/", "Fuel Intake"),
        ("/fueltanks/", "Fuel Tank"),
        ("/jumpdrive/", "Jump Drive"),
        ("/powerplant/", "Power Plant"),
        ("/quantumdrive/", "Quantum Drive"),
        ("/radar/", "Radar"),
        ("/shieldgenerator/", "Shield Generator"),
        ("/thrusters/", "Thruster"),
    ),
    "missile": (
        ("/torpedo/", "Torpedo"),
        ("/torpedoes/", "Torpedo"),
        ("/missile/", "Missile"),
        ("/missiles/", "Missile"),
    ),
}
_LOCALIZATION_PREFIXES = {
    "vehicle": ("vehicle_name",),
    "component": ("item_name",),
    "ship-weapon": ("item_name",),
    "fps-weapon": ("item_name",),
    "medical": ("item_name",),
    "commodity": ("items_commodities_", "commodity_name_", "item_name"),
    "missile": ("item_name",),
}


def _kind(fact: EntityFact) -> str | None:
    if fact.kind is EntityKind.JOURNAL:
        return None
    if fact.kind is EntityKind.SHIP_WEAPON and any(
        part in fact.record_path.casefold()
        for part in ("/missile/", "/missiles/", "/missile_")
    ):
        return "missile"
    return fact.kind.value


def _evidence(provider: str, item) -> Evidence:
    return Evidence(
        provider,
        item.record_id,
        item.record_path,
        item.field_path,
        item.value,
    )


def _attribute(
    provider: str,
    name: str,
    grouped: tuple[EntityFact, ...],
) -> EntityAttribute | None:
    values = [fact.get(name) for fact in grouped]
    if any(item is None for item in values):
        return None
    distinct = {item.value for item in values if item is not None}
    if len(distinct) != 1:
        return None
    value = values[0].value
    if name == "tracking-signal" and value not in _TRACKING_SIGNALS:
        return None
    bounds = _NUMERIC_BOUNDS.get(name)
    if bounds is not None and (
        type(value) not in {int, float} or not bounds[0] <= value <= bounds[1]
    ):
        return None
    try:
        return EntityAttribute(
            name,
            value,
            tuple(
                dict.fromkeys(
                    _evidence(provider, item.evidence)
                    for item in values
                    if item is not None
                )
            ),
        )
    except ValueError:
        return None


def _path_subtype(
    provider: str,
    kind: str,
    grouped: tuple[EntityFact, ...],
) -> EntityAttribute | None:
    aliases = _PATH_SUBTYPES.get(kind, ())
    if not aliases:
        return None
    labels: list[str] = []
    for fact in grouped:
        matches = {
            label for fragment, label in aliases if fragment in fact.record_path.casefold()
        }
        if len(matches) != 1:
            return None
        labels.append(next(iter(matches)))
    if len(set(labels)) != 1:
        return None
    return EntityAttribute(
        "subtype",
        labels[0],
        tuple(
            Evidence(
                provider,
                fact.entity_id,
                fact.record_path,
                "$record.file_name",
                fact.record_path,
            )
            for fact in grouped
        ),
    )


def _provider_status(status: CapabilityStatus) -> ProviderStatus:
    return {
        CapabilityStatus.AVAILABLE: ProviderStatus.AVAILABLE,
        CapabilityStatus.DEGRADED: ProviderStatus.DEGRADED,
        CapabilityStatus.UNAVAILABLE: ProviderStatus.UNAVAILABLE,
    }[status]


def _supported_localization_key(kind: str, key: str) -> bool:
    """Reject generic UI and cross-domain labels before display mutation."""

    return key.casefold().startswith(_LOCALIZATION_PREFIXES.get(kind, ()))


def attach_entity_presentation(
    contracts: ContractSet,
    catalog: EntityCatalogResult,
    strings: LocalizationFile,
) -> ContractSet:
    """Attach only unambiguous, locally resolved display-name joins."""

    grouped: dict[str, list[tuple[str, EntityFact]]] = {}
    unmatched = Counter()
    source_capabilities: list[ProviderCapability] = []
    provider_versions = {
        item.spec.provider: item.spec.version for item in presentation_entity_providers()
    }
    for result in catalog.provider_results:
        provider = result.capability.provider
        if provider not in provider_versions:
            continue
        matched = 0
        source_unmatched = Counter()
        for fact in result.facts:
            if _kind(fact) is None:
                continue
            name = fact.get("name")
            if name is None or not isinstance(name.value, str):
                unmatched["name-unavailable"] += 1
                source_unmatched["name-unavailable"] += 1
                continue
            resolved = strings.resolve_key(name.value)
            if resolved is None:
                unmatched["localization-missing"] += 1
                source_unmatched["localization-missing"] += 1
                continue
            kind = _kind(fact)
            if kind is None or not _supported_localization_key(kind, resolved):
                unmatched["unsupported-localization-family"] += 1
                source_unmatched["unsupported-localization-family"] += 1
                continue
            grouped.setdefault(resolved, []).append((provider, fact))
            matched += 1
        raw = result.capability
        counts = Counter(item.category.value for item in raw.diagnostics)
        source_capabilities.append(
            ProviderCapability(
                provider=provider,
                version=provider_versions[provider],
                status=_provider_status(raw.status),
                build_version=str(raw.build_version),
                facts_seen=len(result.facts),
                contracts_enhanced=matched,
                evidence_links=sum(
                    len(fact.values)
                    + sum(len(link.evidence) for link in fact.relationships)
                    for fact in result.facts
                ),
                diagnostics=tuple(
                    dict.fromkeys(
                        f"{item.severity.value}:{item.code}"
                        for item in raw.diagnostics
                    )
                ),
                matched_facts=matched,
                unmatched_facts=max(0, len(result.facts) - matched),
                diagnostic_counts=tuple(sorted(counts.items())),
                unmatched_reason_counts=tuple(sorted(source_unmatched.items())),
            )
        )

    displays: list[LocalizedEntity] = []
    matched_facts = 0
    ambiguous_facts = 0
    ambiguous_groups = 0
    invalid_facts = 0
    invalid_groups = 0
    for key, sourced in sorted(grouped.items()):
        facts = tuple(fact for _provider, fact in sourced)
        kinds = {_kind(fact) for fact in facts}
        if None in kinds:
            kinds.remove(None)
        if len(kinds) != 1:
            ambiguous_facts += len(facts)
            ambiguous_groups += 1
            continue
        kind = next(iter(kinds))
        providers = {provider for provider, _fact in sourced}
        name_evidence = tuple(
            dict.fromkeys(
                _evidence(provider, fact.get("name").evidence)
                for provider, fact in sourced
                if fact.get("name") is not None
            )
        )
        evidence_provider = (
            next(iter(providers)) if len(providers) == 1 else PRESENTATION_PROVIDER
        )
        attributes = tuple(
            attribute
            for name in _TAG_ATTRIBUTES
            if (
                attribute := (
                    _path_subtype(evidence_provider, kind, facts)
                    if name == "subtype"
                    else _attribute(evidence_provider, name, facts)
                )
            )
            is not None
        )
        try:
            displays.append(
                LocalizedEntity(
                    entity_id=f"localization:{key}",
                    kind=kind,
                    localization_key=key,
                    base_text=strings.get(key) or "",
                    attributes=attributes,
                    evidence=name_evidence,
                )
            )
            matched_facts += len(facts)
        except ValueError:
            invalid_facts += len(facts)
            invalid_groups += 1

    build = next(
        (item.build_version for item in source_capabilities),
        "unknown",
    )
    diagnostics = []
    if ambiguous_groups:
        diagnostics.append(
            "warning:entity-display-ambiguous: "
            f"{ambiguous_groups} shared names were suppressed"
        )
    if invalid_groups:
        diagnostics.append(
            "warning:entity-display-invalid: "
            f"{invalid_groups} unsafe joins were suppressed"
        )
    facts_seen = sum(
        _kind(fact) is not None
        for item in catalog.provider_results
        for fact in item.facts
    )
    status = ProviderStatus.AVAILABLE if facts_seen else ProviderStatus.UNAVAILABLE
    if facts_seen and (diagnostics or unmatched):
        status = ProviderStatus.DEGRADED if displays else ProviderStatus.UNAVAILABLE
    presentation = ProviderCapability(
        provider=PRESENTATION_PROVIDER,
        version=PRESENTATION_VERSION,
        status=status,
        build_version=build,
        facts_seen=facts_seen,
        contracts_enhanced=len(displays),
        evidence_links=sum(
            len(item.evidence)
            + sum(len(attribute.evidence) for attribute in item.attributes)
            for item in displays
        ),
        diagnostics=tuple(diagnostics),
        matched_facts=matched_facts,
        unmatched_facts=(
            sum(unmatched.values()) + ambiguous_facts + invalid_facts
        ),
        unmatched_reason_counts=tuple(
            sorted(
                (reason, count)
                for reason, count in {
                    **unmatched,
                    "ambiguous-localization": ambiguous_facts,
                    "invalid-localization": invalid_facts,
                }.items()
                if count
            )
        ),
    )
    contracts.entities = displays
    existing = {item.provider for item in contracts.capabilities}
    contracts.capabilities.extend(
        item for item in (*source_capabilities, presentation) if item.provider not in existing
    )
    return contracts


def unavailable_entity_capabilities(
    build_version: str,
    reason: str,
) -> tuple[ProviderCapability, ...]:
    """Represent a missing entity graph without weakening mission providers."""

    sources = tuple(
        ProviderCapability(
            provider=item.spec.provider,
            version=item.spec.version,
            status=ProviderStatus.UNAVAILABLE,
            build_version=build_version,
            diagnostics=(reason,),
        )
        for item in presentation_entity_providers()
    )
    return (
        *sources,
        ProviderCapability(
            provider=PRESENTATION_PROVIDER,
            version=PRESENTATION_VERSION,
            status=ProviderStatus.UNAVAILABLE,
            build_version=build_version,
            diagnostics=(reason,),
        ),
    )


__all__ = [
    "PRESENTATION_PROVIDER",
    "PRESENTATION_VERSION",
    "attach_entity_presentation",
    "unavailable_entity_capabilities",
]
