"""Independent, evidence-carrying tactical facts from local mission records."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Iterable

from .dataforge import (
    CapabilityReport,
    CapabilityStatus,
    Confidence,
    DataForgeIndex,
    Diagnostic,
    Evidence,
    FieldValue,
    MissionRecordScope,
    RecordNode,
    RecordSource,
    ScalarKind,
    Severity,
    convert_scalar,
    mission_contract_localization,
    mission_contract_scopes,
)

Scalar = str | int | float | bool


@dataclass(frozen=True)
class TacticalFieldSpec:
    name: str
    source_names: tuple[str, ...]
    kind: ScalarKind
    confidence: Confidence = Confidence.HIGH
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 256

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.source_names:
            raise ValueError("tactical fields require names")
        normalized = [item.casefold() for item in self.source_names]
        if any(not item.strip() for item in self.source_names):
            raise ValueError("tactical source names must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("tactical source names must be unique")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("tactical numeric range is inverted")
        if not 1 <= self.max_length <= 4096:
            raise ValueError("tactical text bounds must be between 1 and 4096")


@dataclass(frozen=True)
class TacticalProviderSpec:
    provider: str
    version: str
    fields: tuple[TacticalFieldSpec, ...]
    reference_names: tuple[str, ...] = ()
    max_reference_hops: int = 4

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.version.strip() or not self.fields:
            raise ValueError("tactical providers require identity and fields")
        field_names = [item.name.casefold() for item in self.fields]
        references = [item.casefold() for item in self.reference_names]
        if len(field_names) != len(set(field_names)):
            raise ValueError("tactical fact names must be unique")
        if any(not item.strip() for item in self.reference_names):
            raise ValueError("tactical reference names must not be empty")
        if len(references) != len(set(references)):
            raise ValueError("tactical reference names must be unique")
        if not 0 <= self.max_reference_hops <= 16:
            raise ValueError("tactical reference hops must be between 0 and 16")


@dataclass(frozen=True)
class TacticalValue:
    name: str
    value: Scalar
    confidence: Confidence
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("tactical values require evidence")


@dataclass(frozen=True)
class MissionTacticalFacts:
    mission_id: str
    title_keys: tuple[str, ...]
    description_keys: tuple[str, ...]
    match_keys: tuple[str, ...]
    values: tuple[TacticalValue, ...]
    source_record_ids: tuple[str, ...]
    confidence: Confidence
    diagnostics: tuple[Diagnostic, ...] = ()

    def get(self, name: str) -> TacticalValue | None:
        wanted = name.casefold()
        return next((item for item in self.values if item.name.casefold() == wanted), None)


@dataclass(frozen=True)
class MissionTacticalResult:
    facts: tuple[MissionTacticalFacts, ...]
    capability: CapabilityReport


@dataclass(frozen=True)
class MissionTacticalCatalog:
    provider_results: tuple[MissionTacticalResult, ...]

    @property
    def capabilities(self) -> tuple[CapabilityReport, ...]:
        return tuple(item.capability for item in self.provider_results)

    @property
    def facts(self) -> tuple[MissionTacticalFacts, ...]:
        return tuple(fact for result in self.provider_results for fact in result.facts)

    @property
    def evidence_links(self) -> int:
        return sum(len(value.evidence) for fact in self.facts for value in fact.values)

    def for_provider(self, provider: str) -> MissionTacticalResult | None:
        wanted = provider.casefold()
        return next(
            (
                item
                for item in self.provider_results
                if item.capability.provider.casefold() == wanted
            ),
            None,
        )


@dataclass(frozen=True)
class _MissionView:
    mission_id: str
    node: RecordNode
    scope: MissionRecordScope
    title_keys: tuple[str, ...]
    description_keys: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    node: RecordNode
    field: FieldValue
    reference_evidence: tuple[Evidence, ...] = ()


class MissionTacticalProvider:
    """Extract one tactical capability without depending on its peers."""

    def __init__(self, spec: TacticalProviderSpec) -> None:
        self.spec = spec

    def extract(
        self,
        index: DataForgeIndex,
        *,
        build_version: str | None = None,
    ) -> MissionTacticalResult:
        build = str(index.source.version if build_version is None else build_version)
        nodes = index.records_under("records/contracts/contractgenerator")
        if not nodes:
            diagnostic = Diagnostic(
                f"{self.spec.provider}-provider-missing",
                "No contract generator records were found",
                Severity.ERROR,
            )
            return MissionTacticalResult(
                (),
                CapabilityReport(
                    self.spec.provider,
                    CapabilityStatus.UNAVAILABLE,
                    build,
                    0,
                    0,
                    (diagnostic,),
                ),
            )

        views = _mission_views(index, nodes)
        ambiguous_descriptions = _shared_descriptions(views)
        facts: list[MissionTacticalFacts] = []
        diagnostics: list[Diagnostic] = []
        for view in views:
            candidates, graph_diagnostics = _provider_candidates(index, view, self.spec)
            values, value_diagnostics = _extract_values(candidates, self.spec)
            fact_diagnostics = [*graph_diagnostics, *value_diagnostics]
            diagnostics.extend(fact_diagnostics)
            if not values:
                continue
            safe_descriptions = tuple(
                key for key in view.description_keys if key.casefold() not in ambiguous_descriptions
            )
            removed = tuple(
                key for key in view.description_keys if key.casefold() in ambiguous_descriptions
            )
            if removed:
                ambiguity = Diagnostic(
                    "shared-description-ambiguous",
                    "Shared mission descriptions were removed from tactical matching",
                    Severity.WARNING,
                    view.node.id,
                    view.node.normalized_path,
                    view.scope.path,
                    removed[:8],
                )
                fact_diagnostics.append(ambiguity)
                diagnostics.append(ambiguity)
            match_keys = tuple(dict.fromkeys((*view.title_keys, *safe_descriptions)))
            confidence = _fact_confidence(values, bool(match_keys), bool(removed))
            source_ids = tuple(
                dict.fromkeys(
                    evidence.record_id
                    for value in values
                    for evidence in value.evidence
                )
            )
            facts.append(
                MissionTacticalFacts(
                    view.mission_id,
                    view.title_keys,
                    view.description_keys,
                    match_keys,
                    tuple(values),
                    source_ids,
                    confidence,
                    tuple(fact_diagnostics),
                )
            )

        if not facts:
            diagnostics.append(
                Diagnostic(
                    f"{self.spec.provider}-schema-drift",
                    "Contract records exist but no supported tactical fields were found",
                    Severity.ERROR,
                )
            )
            status = CapabilityStatus.UNAVAILABLE
        elif any(item.degrades_capability for item in diagnostics):
            status = CapabilityStatus.DEGRADED
        else:
            status = CapabilityStatus.AVAILABLE
        report = CapabilityReport(
            self.spec.provider,
            status,
            build,
            len(nodes),
            len(facts),
            tuple(dict.fromkeys(diagnostics)),
        )
        return MissionTacticalResult(tuple(facts), report)


def _mission_views(
    index: DataForgeIndex,
    nodes: tuple[RecordNode, ...],
) -> tuple[_MissionView, ...]:
    views: list[_MissionView] = []
    for node in sorted(nodes, key=lambda item: (item.normalized_path, item.id)):
        for scope in mission_contract_scopes(index, node):
            titles, descriptions, _evidence = mission_contract_localization(index, node, scope)
            mission_id = node.id if scope.path == "$" else f"{node.id}:{scope.path}"
            views.append(_MissionView(mission_id, node, scope, titles, descriptions))
    return tuple(views)


def _shared_descriptions(views: tuple[_MissionView, ...]) -> frozenset[str]:
    owners: dict[str, set[str]] = {}
    for view in views:
        for key in view.description_keys:
            owners.setdefault(key.casefold(), set()).add(view.mission_id)
    return frozenset(key for key, missions in owners.items() if len(missions) > 1)


def _provider_candidates(
    index: DataForgeIndex,
    view: _MissionView,
    spec: TacticalProviderSpec,
) -> tuple[tuple[_Candidate, ...], tuple[Diagnostic, ...]]:
    candidates = [_Candidate(view.node, field) for field in view.scope.fields]
    diagnostics: list[Diagnostic] = []
    reference_names = {item.casefold() for item in spec.reference_names}
    if not reference_names or spec.max_reference_hops == 0:
        return tuple(candidates), ()

    queue: deque[
        tuple[RecordNode, tuple[Evidence, ...], int, frozenset[tuple[str, str]]]
    ] = deque()
    root_identity = (view.node.id.casefold(), view.node.normalized_path)
    seen: set[tuple[str, str]] = {root_identity}

    def enqueue_references(
        source_node: RecordNode,
        fields: Iterable[FieldValue],
        chain: tuple[Evidence, ...],
        depth: int,
        ancestry: frozenset[tuple[str, str]],
    ) -> None:
        for field in fields:
            if _field_name(field.path).casefold() not in reference_names:
                continue
            resolution = index.resolve(
                field.value,
                source=source_node,
                field_path=field.path,
                breadcrumbs=tuple(item.record_path for item in chain),
            )
            diagnostics.extend(resolution.diagnostics)
            if not resolution.target:
                continue
            target = resolution.target
            identity = (target.id.casefold(), target.normalized_path)
            edge = Evidence(
                source_node.id,
                source_node.normalized_path,
                field.path,
                resolution.reference,
            )
            if identity in ancestry:
                diagnostics.append(
                    Diagnostic(
                        "reference-cycle",
                        f"Tactical reference revisits {target.normalized_path}",
                        Severity.WARNING,
                        source_node.id,
                        source_node.normalized_path,
                        field.path,
                    )
                )
                continue
            if identity in seen:
                continue
            if depth >= spec.max_reference_hops:
                diagnostics.append(
                    Diagnostic(
                        "reference-hop-limit",
                        f"Tactical reference traversal stopped at {spec.max_reference_hops} hops",
                        Severity.WARNING,
                        source_node.id,
                        source_node.normalized_path,
                        field.path,
                    )
                )
                continue
            seen.add(identity)
            queue.append((target, (*chain, edge), depth + 1, ancestry | {identity}))

    enqueue_references(view.node, view.scope.fields, (), 0, frozenset({root_identity}))
    while queue:
        target, chain, depth, ancestry = queue.popleft()
        fields = tuple(index.iter_fields(target))
        candidates.extend(_Candidate(target, field, chain) for field in fields)
        enqueue_references(target, fields, chain, depth, ancestry)
    return tuple(candidates), tuple(diagnostics)


def _extract_values(
    candidates: tuple[_Candidate, ...],
    spec: TacticalProviderSpec,
) -> tuple[list[TacticalValue], list[Diagnostic]]:
    values: list[TacticalValue] = []
    diagnostics: list[Diagnostic] = []
    for field_spec in spec.fields:
        aliases = {item.casefold() for item in field_spec.source_names}
        matches = [
            candidate
            for candidate in candidates
            if _field_name(candidate.field.path).casefold() in aliases
            and isinstance(candidate.field.value, (str, int, float, bool))
        ]
        converted: list[tuple[_Candidate, Scalar]] = []
        for candidate in matches:
            result = convert_scalar(candidate.field, field_spec.kind, node=candidate.node)
            if result.value is None:
                diagnostics.append(
                    Diagnostic(
                        f"{spec.provider}-field-conversion-schema-drift",
                        f"Field {field_spec.name!r}: {result.diagnostic.message}",
                        Severity.WARNING,
                        candidate.node.id,
                        candidate.node.normalized_path,
                        candidate.field.path,
                    )
                )
                continue
            assert isinstance(result.value, (str, int, float, bool))
            if isinstance(result.value, str) and (
                len(result.value) > field_spec.max_length
                or any(character in result.value for character in "\r\n\0")
            ):
                diagnostics.append(
                    Diagnostic(
                        f"{spec.provider}-field-value-invalid-schema-drift",
                        f"Field {field_spec.name!r} contains unsafe or oversized text",
                        Severity.WARNING,
                        candidate.node.id,
                        candidate.node.normalized_path,
                        candidate.field.path,
                    )
                )
                continue
            if not isinstance(result.value, (str, bool)) and (
                (field_spec.minimum is not None and result.value < field_spec.minimum)
                or (field_spec.maximum is not None and result.value > field_spec.maximum)
            ):
                diagnostics.append(
                    Diagnostic(
                        f"{spec.provider}-field-range-schema-drift",
                        f"Field {field_spec.name!r} is outside its reviewed range",
                        Severity.WARNING,
                        candidate.node.id,
                        candidate.node.normalized_path,
                        candidate.field.path,
                    )
                )
                continue
            converted.append((candidate, result.value))
        distinct = {value for _candidate, value in converted}
        if len(distinct) > 1:
            first = converted[0][0]
            diagnostics.append(
                Diagnostic(
                    f"{spec.provider}-field-ambiguous-schema-drift",
                    f"Field {field_spec.name!r} has conflicting values",
                    Severity.WARNING,
                    first.node.id,
                    first.node.normalized_path,
                    breadcrumbs=tuple(item.field.path for item, _value in converted[:8]),
                )
            )
            continue
        if converted:
            candidate, value = min(
                converted,
                key=lambda item: (
                    len(item[0].reference_evidence),
                    item[0].node.normalized_path,
                    item[0].field.path,
                ),
            )
            evidence = (
                *candidate.reference_evidence,
                Evidence(
                    candidate.node.id,
                    candidate.node.normalized_path,
                    candidate.field.path,
                    value,
                ),
            )
            values.append(
                TacticalValue(
                    field_spec.name,
                    value,
                    field_spec.confidence,
                    tuple(evidence),
                )
            )
    return values, diagnostics


def _fact_confidence(
    values: list[TacticalValue],
    has_match_key: bool,
    removed_shared_description: bool,
) -> Confidence:
    if not has_match_key:
        return Confidence.LOW
    levels = {
        Confidence.NONE: 0,
        Confidence.LOW: 1,
        Confidence.MEDIUM: 2,
        Confidence.HIGH: 3,
    }
    confidence = min(values, key=lambda item: levels[item.confidence]).confidence
    if removed_shared_description and confidence is Confidence.HIGH:
        return Confidence.MEDIUM
    return confidence


def _field_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[", 1)[0]


CLASSIFICATION_PROVIDER = TacticalProviderSpec(
    "local-dataforge-mission-classification",
    "1",
    (
        TacticalFieldSpec("mission-type", ("missionType", "contractType", "archetype"), ScalarKind.STRING),
        TacticalFieldSpec("difficulty", ("difficulty", "difficultyCode", "risk"), ScalarKind.STRING),
    ),
)

SPAWN_PROVIDER = TacticalProviderSpec(
    "local-dataforge-mission-spawns",
    "1",
    (
        TacticalFieldSpec(
            "friendly-spawns",
            ("friendlyCount", "friendlySpawnCount"),
            ScalarKind.INTEGER,
            minimum=0,
            maximum=100_000,
        ),
        TacticalFieldSpec(
            "hostile-spawns",
            ("hostileCount", "hostileSpawnCount"),
            ScalarKind.INTEGER,
            minimum=0,
            maximum=100_000,
        ),
        TacticalFieldSpec("ace-pilot", ("hasAcePilot", "acePilot"), ScalarKind.BOOLEAN),
        TacticalFieldSpec(
            "ace-probability",
            ("acePilotChance", "aceProbability"),
            ScalarKind.FLOAT,
            Confidence.MEDIUM,
            minimum=0,
            maximum=1,
        ),
    ),
    ("spawnConfig", "spawnDefinition", "spawnProfile"),
)

ENGAGEMENT_PROVIDER = TacticalProviderSpec(
    "local-dataforge-mission-engagement",
    "1",
    (
        TacticalFieldSpec(
            "turret-count",
            ("turretCount", "turrets"),
            ScalarKind.INTEGER,
            minimum=0,
            maximum=100_000,
        ),
        TacticalFieldSpec(
            "engagement-distance",
            ("engagementDistance", "effectiveRange"),
            ScalarKind.FLOAT,
            minimum=0,
            maximum=1_000_000_000,
        ),
        TacticalFieldSpec(
            "engagement-type",
            ("engagementType", "combatStyle"),
            ScalarKind.STRING,
        ),
    ),
    ("engagementConfig", "combatConfig", "encounterConfig"),
)


def mission_tactical_providers() -> tuple[MissionTacticalProvider, ...]:
    return tuple(
        MissionTacticalProvider(spec)
        for spec in (CLASSIFICATION_PROVIDER, ENGAGEMENT_PROVIDER, SPAWN_PROVIDER)
    )


def extract_mission_tactical_catalog(
    source: RecordSource,
    *,
    build_version: str | None = None,
    providers: Iterable[MissionTacticalProvider] | None = None,
) -> MissionTacticalCatalog:
    """Run independent tactical providers over one bounded record graph."""

    selected = tuple(providers) if providers is not None else mission_tactical_providers()
    names = [item.spec.provider.casefold() for item in selected]
    if len(names) != len(set(names)):
        raise ValueError("mission tactical provider ids must be unique")
    index = DataForgeIndex(source)
    results = tuple(
        provider.extract(index, build_version=build_version)
        for provider in sorted(selected, key=lambda item: item.spec.provider)
    )
    return MissionTacticalCatalog(results)
