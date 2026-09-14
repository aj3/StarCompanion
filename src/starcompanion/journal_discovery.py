"""Resolve local journals and report evidenced localization gaps."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .extract.dataforge import CapabilityStatus
from .extract.entities import EntityCatalogResult, EntityFact, EntityKind, JOURNAL_PROVIDER
from .ini import LocalizationFile
from .model import ContractSet, Evidence, ProviderCapability, ProviderStatus

JOURNAL_RESOLUTION_PROVIDER = "local-journal-resolution"
JOURNAL_RESOLUTION_VERSION = "1"
DISCOVERY_PROVIDER = "local-localization-discovery"
DISCOVERY_VERSION = "1"
MAX_DISCOVERIES = 50_000


@dataclass(frozen=True)
class JournalEntry:
    """One journal record joined only to text from the selected language."""

    record_id: str
    record_path: str
    title_key: str
    title: str
    body_key: str | None
    body: str | None
    category: str | None
    discovery_tag: str | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class LocalizationDiscovery:
    """A reviewed entity key that the selected local language does not contain."""

    source_id: str
    entity_kind: str
    localization_key: str
    role: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class JournalDiscoveryResult:
    journals: tuple[JournalEntry, ...]
    discoveries: tuple[LocalizationDiscovery, ...]
    capabilities: tuple[ProviderCapability, ...]


def _status(status: CapabilityStatus) -> ProviderStatus:
    return {
        CapabilityStatus.AVAILABLE: ProviderStatus.AVAILABLE,
        CapabilityStatus.DEGRADED: ProviderStatus.DEGRADED,
        CapabilityStatus.UNAVAILABLE: ProviderStatus.UNAVAILABLE,
    }[status]


def _evidence(provider: str, fact: EntityFact, name: str) -> tuple[Evidence, ...]:
    value = fact.get(name)
    if value is None:
        return ()
    item = value.evidence
    return (
        Evidence(provider, item.record_id, item.record_path, item.field_path, item.value),
    )


def _key(fact: EntityFact, name: str) -> str | None:
    value = fact.get(name)
    if value is None or not isinstance(value.value, str):
        return None
    return value.value


def build_journal_discovery(
    catalog: EntityCatalogResult,
    strings: LocalizationFile,
    *,
    max_discoveries: int = MAX_DISCOVERIES,
) -> JournalDiscoveryResult:
    """Build independent journal and missing-key projections without guessing text."""

    if not 1 <= max_discoveries <= MAX_DISCOVERIES:
        raise ValueError("discovery limit must be between 1 and 50,000")

    raw_journal = catalog.for_provider(JOURNAL_PROVIDER.provider)
    journal_facts = tuple(raw_journal.facts) if raw_journal is not None else ()
    owners: dict[str, set[str]] = defaultdict(set)
    for fact in journal_facts:
        for name in ("title", "body"):
            if key := _key(fact, name):
                owners[key.casefold()].add(fact.entity_id)
    ambiguous_ids = {
        record_id
        for record_ids in owners.values()
        if len(record_ids) > 1
        for record_id in record_ids
    }

    journals = []
    missing_journal = Counter()
    for fact in sorted(journal_facts, key=lambda item: (item.record_path, item.entity_id)):
        if fact.entity_id in ambiguous_ids:
            continue
        title_key = _key(fact, "title")
        body_key = _key(fact, "body")
        if title_key is None:
            missing_journal["title-field-missing"] += 1
            continue
        title = strings.get(title_key)
        body = strings.get(body_key) if body_key is not None else None
        if title is None:
            missing_journal["title-localization-missing"] += 1
            continue
        if body_key is not None and body is None:
            missing_journal["body-localization-missing"] += 1
            continue
        category = fact.get("category")
        discovery_tag = fact.get("discovery-tag")
        evidence = tuple(
            item
            for name in ("title", "body", "category", "discovery-tag")
            for item in _evidence(JOURNAL_PROVIDER.provider, fact, name)
        )
        journals.append(
            JournalEntry(
                fact.entity_id,
                fact.record_path,
                strings.resolve_key(title_key) or title_key,
                title,
                (strings.resolve_key(body_key) or body_key) if body_key else None,
                body,
                str(category.value) if category is not None else None,
                str(discovery_tag.value) if discovery_tag is not None else None,
                evidence,
            )
        )

    discoveries = []
    present_names = 0
    discovery_truncated = False
    for result in catalog.provider_results:
        for fact in result.facts:
            if fact.kind is EntityKind.JOURNAL:
                continue
            name_key = _key(fact, "name")
            if name_key is None:
                continue
            if strings.get(name_key) is not None:
                present_names += 1
                continue
            if len(discoveries) >= max_discoveries:
                discovery_truncated = True
                continue
            discoveries.append(
                LocalizationDiscovery(
                    fact.entity_id,
                    fact.kind.value,
                    name_key,
                    "name",
                    _evidence(result.capability.provider, fact, "name"),
                )
            )

    build = (
        str(raw_journal.capability.build_version)
        if raw_journal is not None
        else next(
            (str(item.capability.build_version) for item in catalog.provider_results),
            "unknown",
        )
    )
    if raw_journal is None:
        source_capability = ProviderCapability(
            JOURNAL_PROVIDER.provider,
            JOURNAL_PROVIDER.version,
            ProviderStatus.UNAVAILABLE,
            build,
            diagnostics=("journal-provider-not-run",),
        )
        journal_status = ProviderStatus.UNAVAILABLE
    else:
        raw = raw_journal.capability
        source_capability = ProviderCapability(
            raw.provider,
            JOURNAL_PROVIDER.version,
            _status(raw.status),
            build,
            facts_seen=len(raw_journal.facts),
            evidence_links=sum(len(fact.values) for fact in raw_journal.facts),
            diagnostics=tuple(
                dict.fromkeys(f"{item.severity.value}:{item.code}" for item in raw.diagnostics)
            ),
            diagnostic_counts=tuple(sorted(Counter(item.code for item in raw.diagnostics).items())),
        )
        journal_status = _status(raw.status)
        if journal_facts and (ambiguous_ids or missing_journal):
            journal_status = ProviderStatus.DEGRADED

    journal_reasons = Counter(missing_journal)
    if ambiguous_ids:
        journal_reasons["shared-localization-ambiguous"] = len(ambiguous_ids)
    journal_capability = ProviderCapability(
        JOURNAL_RESOLUTION_PROVIDER,
        JOURNAL_RESOLUTION_VERSION,
        journal_status,
        build,
        facts_seen=len(journal_facts),
        contracts_enhanced=len(journals),
        evidence_links=sum(len(item.evidence) for item in journals),
        diagnostics=(
            ("warning:shared-journal-localization-suppressed",)
            if ambiguous_ids
            else ()
        ),
        matched_facts=len(journals),
        unmatched_facts=max(0, len(journal_facts) - len(journals)),
        unmatched_reason_counts=tuple(sorted(journal_reasons.items())),
    )

    discovery_status = (
        ProviderStatus.DEGRADED if discovery_truncated else ProviderStatus.AVAILABLE
    )
    if not catalog.provider_results:
        discovery_status = ProviderStatus.UNAVAILABLE
    discovery_capability = ProviderCapability(
        DISCOVERY_PROVIDER,
        DISCOVERY_VERSION,
        discovery_status,
        build,
        facts_seen=present_names + len(discoveries),
        evidence_links=sum(len(item.evidence) for item in discoveries),
        diagnostics=(
            ("warning:localization-discovery-limit-reached",)
            if discovery_truncated
            else ()
        ),
        matched_facts=present_names,
        unmatched_facts=len(discoveries),
        unmatched_reason_counts=(
            (("localization-missing", len(discoveries)),) if discoveries else ()
        ),
    )
    return JournalDiscoveryResult(
        tuple(journals),
        tuple(discoveries),
        (source_capability, journal_capability, discovery_capability),
    )


def attach_journal_discovery(
    contracts: ContractSet,
    catalog: EntityCatalogResult,
    strings: LocalizationFile,
) -> JournalDiscoveryResult:
    """Expose G8 provider health while later UI work consumes the typed result."""

    result = build_journal_discovery(catalog, strings)
    existing = {item.provider for item in contracts.capabilities}
    contracts.capabilities.extend(
        item for item in result.capabilities if item.provider not in existing
    )
    return result


def unavailable_journal_discovery_capabilities(
    build_version: str,
    reason: str,
) -> tuple[ProviderCapability, ...]:
    return tuple(
        ProviderCapability(provider, version, ProviderStatus.UNAVAILABLE, build_version, diagnostics=(reason,))
        for provider, version in (
            (JOURNAL_PROVIDER.provider, JOURNAL_PROVIDER.version),
            (JOURNAL_RESOLUTION_PROVIDER, JOURNAL_RESOLUTION_VERSION),
            (DISCOVERY_PROVIDER, DISCOVERY_VERSION),
        )
    )


__all__ = [
    "DISCOVERY_PROVIDER",
    "DISCOVERY_VERSION",
    "JournalDiscoveryResult",
    "JournalEntry",
    "LocalizationDiscovery",
    "attach_journal_discovery",
    "build_journal_discovery",
    "unavailable_journal_discovery_capabilities",
]
