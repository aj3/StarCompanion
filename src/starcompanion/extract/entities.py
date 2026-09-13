"""Synthetic-first, evidence-carrying extraction for local entity records.

Providers are intentionally declarative and independent. A changed vehicle
schema therefore cannot disable weapons, medical items, or any other peer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable

from .dataforge import (
    CapabilityReport,
    CapabilityStatus,
    DataForgeIndex,
    Diagnostic,
    Evidence,
    FieldValue,
    ScalarKind,
    Severity,
    convert_scalar,
    normalize_uuid,
)

Scalar = str | int | float | bool


class EntityKind(StrEnum):
    VEHICLE = "vehicle"
    COMPONENT = "component"
    SHIP_WEAPON = "ship-weapon"
    FPS_WEAPON = "fps-weapon"
    MEDICAL = "medical"
    COMMODITY = "commodity"
    CRAFTING = "crafting"
    JOURNAL = "journal"
    MISSION_TACTICAL = "mission-tactical"


@dataclass(frozen=True)
class FieldSpec:
    """One stable fact name and the DataForge leaf names that may supply it."""

    name: str
    source_names: tuple[str, ...]
    kind: ScalarKind
    required: bool = False
    path_fragments: tuple[str, ...] = ()
    follow_pointers: bool = True

    def __post_init__(self) -> None:
        if not self.name or not self.source_names or any(not item for item in self.source_names):
            raise ValueError("field specs require non-empty names")
        normalized = [item.casefold() for item in self.source_names]
        if len(normalized) != len(set(normalized)):
            raise ValueError("field source names must be unique")
        if any(not item.strip() for item in self.path_fragments):
            raise ValueError("field path fragments must not be empty")


@dataclass(frozen=True)
class RelationshipSpec:
    """A reviewed reference edge exposed as a stable relationship."""

    name: str
    source_names: tuple[str, ...]
    target_structs: tuple[str, ...]
    path_fragments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.source_names or not self.target_structs:
            raise ValueError("relationship specs require names and target structs")
        for values in (self.source_names, self.target_structs, self.path_fragments):
            if any(not item.strip() for item in values):
                raise ValueError("relationship aliases must not be empty")


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    version: str
    kind: EntityKind
    path_fragments: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    excluded_path_fragments: tuple[str, ...] = ()
    relationships: tuple[RelationshipSpec, ...] = ()
    struct_names: tuple[str, ...] = ()
    max_pointer_hops: int | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.version.strip() or not self.path_fragments:
            raise ValueError("provider specs require identity and path fragments")
        if any(not item.strip() for item in (*self.path_fragments, *self.excluded_path_fragments)):
            raise ValueError("provider path fragments must not be empty")
        if any(not item.strip() for item in self.struct_names):
            raise ValueError("provider struct names must not be empty")
        if self.max_pointer_hops is not None and not 0 <= self.max_pointer_hops <= 64:
            raise ValueError("provider pointer hops must be between 0 and 64")
        normalized_paths = [item.casefold() for item in self.path_fragments]
        if len(normalized_paths) != len(set(normalized_paths)):
            raise ValueError("provider path fragments must be unique")
        if not self.fields:
            raise ValueError("provider specs require fields")
        names = [item.name.casefold() for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("provider fact names must be unique")
        relationship_names = [item.name.casefold() for item in self.relationships]
        if len(relationship_names) != len(set(relationship_names)):
            raise ValueError("provider relationship names must be unique")


@dataclass(frozen=True)
class EntityValue:
    name: str
    value: Scalar
    evidence: Evidence
    correction: BuildCorrection | None = None
    original_value: Scalar | None = None

    @property
    def correction_id(self) -> str | None:
        return self.correction.correction_id if self.correction else None


@dataclass(frozen=True)
class EntityRelationship:
    name: str
    target_id: str
    target_record_path: str
    target_struct: str
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.target_id or not self.evidence:
            raise ValueError("entity relationships require identity and evidence")


@dataclass(frozen=True)
class EntityFact:
    entity_id: str
    kind: EntityKind
    record_path: str
    values: tuple[EntityValue, ...]
    relationships: tuple[EntityRelationship, ...] = ()

    def get(self, name: str) -> EntityValue | None:
        wanted = name.casefold()
        return next((item for item in self.values if item.name.casefold() == wanted), None)


@dataclass(frozen=True)
class EntityExtractionResult:
    facts: tuple[EntityFact, ...]
    capability: CapabilityReport
    corrections_applied: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityCatalogResult:
    """Independent provider results over one shared DataForge index."""

    provider_results: tuple[EntityExtractionResult, ...]

    @property
    def capabilities(self) -> tuple[CapabilityReport, ...]:
        return tuple(item.capability for item in self.provider_results)

    @property
    def facts(self) -> tuple[EntityFact, ...]:
        return tuple(fact for item in self.provider_results for fact in item.facts)

    @property
    def evidence_links(self) -> int:
        return sum(
            len(fact.values) + sum(len(link.evidence) for link in fact.relationships)
            for fact in self.facts
        )

    @property
    def corrections_applied(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                correction
                for item in self.provider_results
                for correction in item.corrections_applied
            )
        )

    @property
    def status_counts(self) -> tuple[tuple[CapabilityStatus, int], ...]:
        return tuple(
            (status, sum(item.status is status for item in self.capabilities))
            for status in CapabilityStatus
        )

    def for_provider(self, provider: str) -> EntityExtractionResult | None:
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
class BuildCorrection:
    """Reviewed replacement that applies only to one exact game build and field."""

    correction_id: str
    provider: str
    build_version: str
    record_id: str
    field_path: str
    expected_value: Scalar
    replacement_value: Scalar
    rationale: str
    source: str

    def __post_init__(self) -> None:
        required = (
            self.correction_id,
            self.provider,
            self.build_version,
            self.record_id,
            self.field_path,
            self.rationale,
            self.source,
        )
        if any(not item.strip() for item in required):
            raise ValueError("build corrections require complete provenance")
        if type(self.expected_value) is not type(self.replacement_value):
            raise ValueError("correction values must have the same scalar type")
        if self.expected_value == self.replacement_value:
            raise ValueError("a correction must change the source value")


class CorrectionRegistry:
    """Validated immutable collection of exact-build data corrections."""

    def __init__(self, corrections: Iterable[BuildCorrection] = ()) -> None:
        items = tuple(corrections)
        ids = [item.correction_id.casefold() for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("correction ids must be unique")
        targets: set[tuple[str, str, str, str]] = set()
        for item in items:
            target = (
                item.provider.casefold(),
                item.build_version,
                item.record_id.casefold(),
                item.field_path.casefold(),
            )
            if target in targets:
                raise ValueError("only one correction may target a build field")
            targets.add(target)
        self._items = items

    def for_provider(self, provider: str, build_version: str) -> tuple[BuildCorrection, ...]:
        return tuple(
            item
            for item in self._items
            if item.provider.casefold() == provider.casefold()
            and item.build_version == build_version
        )


class LocalEntityProvider:
    """Extract one entity family from an existing bounded DataForge index."""

    def __init__(
        self,
        spec: ProviderSpec,
        *,
        corrections: CorrectionRegistry | None = None,
    ) -> None:
        self.spec = spec
        self.corrections = corrections or CorrectionRegistry()

    def matches(self, record_path: str) -> bool:
        path = record_path.casefold()
        return any(fragment.casefold() in path for fragment in self.spec.path_fragments) and not any(
            fragment.casefold() in path for fragment in self.spec.excluded_path_fragments
        )

    def extract(self, index: DataForgeIndex, *, build_version: str | None = None) -> EntityExtractionResult:
        build = str(index.source.version if build_version is None else build_version)
        struct_names = {item.casefold() for item in self.spec.struct_names}
        nodes = tuple(
            node
            for node in index.nodes
            if self.matches(node.normalized_path)
            and (
                not self.spec.struct_names
                or not callable(getattr(index.source, "properties_of", None))
                or node.struct_name.casefold() in struct_names
            )
        )
        if not nodes:
            diagnostic = Diagnostic(
                f"{self.spec.kind.value}-provider-missing",
                f"No records matched provider {self.spec.provider}",
                Severity.WARNING,
            )
            return EntityExtractionResult(
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

        facts: list[EntityFact] = []
        diagnostics: list[Diagnostic] = []
        for node in sorted(nodes, key=lambda item: (item.normalized_path, item.id)):
            graph_names = tuple(
                dict.fromkeys(
                    name
                    for field in self.spec.fields
                    if field.follow_pointers
                    for name in field.source_names
                )
            )
            inline_names = tuple(
                dict.fromkeys(
                    name
                    for field in self.spec.fields
                    if not field.follow_pointers
                    for name in field.source_names
                )
            )
            fields = tuple(
                index.iter_fields(node)
                if self.spec.relationships
                else (
                    *index.iter_selected_fields(
                        node,
                        graph_names,
                        max_pointer_hops=self.spec.max_pointer_hops,
                    ),
                    *index.iter_inline_fields(node, inline_names),
                )
            )
            values: list[EntityValue] = []
            for field_spec in self.spec.fields:
                matches = _matching_scalars(
                    fields,
                    field_spec.source_names,
                    field_spec.path_fragments,
                )
                if not matches:
                    if field_spec.required:
                        diagnostics.append(
                            Diagnostic(
                                f"{self.spec.kind.value}-field-missing",
                                f"Required field {field_spec.name!r} is absent",
                                Severity.WARNING,
                                node.id,
                                node.normalized_path,
                            )
                        )
                    continue
                converted = [
                    (found, convert_scalar(found, field_spec.kind, node=node))
                    for found in matches
                ]
                diagnostics.extend(
                    Diagnostic(
                        f"{self.spec.kind.value}-field-conversion-schema-drift",
                        f"Field {field_spec.name!r}: {item.diagnostic.message}",
                        Severity.WARNING,
                        node.id,
                        node.normalized_path,
                        found.path,
                    )
                    for found, item in converted
                    if item.diagnostic is not None
                )
                valid = [(found, item.value) for found, item in converted if item.value is not None]
                if field_spec.required and not valid:
                    diagnostics.append(
                        Diagnostic(
                            f"{self.spec.kind.value}-field-missing",
                            f"Required field {field_spec.name!r} has no valid scalar value",
                            Severity.WARNING,
                            node.id,
                            node.normalized_path,
                            matches[0].path,
                        )
                    )
                unique = {value for _found, value in valid}
                if len(unique) > 1:
                    diagnostics.append(
                        Diagnostic(
                            f"{self.spec.kind.value}-field-ambiguous-schema-drift",
                            f"Field {field_spec.name!r} has conflicting values",
                            Severity.WARNING,
                            node.id,
                            node.normalized_path,
                            breadcrumbs=tuple(found.path for found, _value in valid[:8]),
                        )
                    )
                    continue
                if valid:
                    found, value = valid[0]
                    assert isinstance(value, (str, int, float, bool))
                    values.append(
                        EntityValue(
                            field_spec.name,
                            value,
                            Evidence(node.id, node.normalized_path, found.path, value),
                        )
                    )
            relationships, relationship_diagnostics = _extract_relationships(
                index, node, fields, self.spec
            )
            diagnostics.extend(relationship_diagnostics)
            if values or relationships:
                facts.append(
                    EntityFact(
                        node.id,
                        self.spec.kind,
                        node.normalized_path,
                        tuple(values),
                        relationships,
                    )
                )

        for required in (item for item in self.spec.fields if item.required):
            if not any(fact.get(required.name) is not None for fact in facts):
                diagnostics.append(
                    Diagnostic(
                        f"{self.spec.kind.value}-required-field-schema-drift",
                        f"No matched record supplied required field {required.name!r}",
                        Severity.WARNING,
                    )
                )
        result = EntityExtractionResult(tuple(facts), _capability(self.spec, build, len(nodes), facts, diagnostics))
        return _apply_corrections(result, self.spec.provider, build, self.corrections)


def _matching_scalars(
    fields: tuple[FieldValue, ...],
    names: tuple[str, ...],
    path_fragments: tuple[str, ...] = (),
) -> tuple[FieldValue, ...]:
    wanted = {name.casefold() for name in names}
    paths = tuple(item.casefold() for item in path_fragments)
    return tuple(
        found
        for found in fields
        if found.path.rsplit(".", 1)[-1].split("[", 1)[0].casefold() in wanted
        and isinstance(found.value, (str, int, float, bool))
        and (not paths or any(fragment in found.path.casefold() for fragment in paths))
    )


def _extract_relationships(
    index: DataForgeIndex,
    node,
    fields: tuple[FieldValue, ...],
    provider: ProviderSpec,
) -> tuple[tuple[EntityRelationship, ...], tuple[Diagnostic, ...]]:
    relationships: list[EntityRelationship] = []
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in provider.relationships:
        aliases = {item.casefold() for item in spec.source_names}
        targets = {item.casefold() for item in spec.target_structs}
        path_fragments = tuple(item.casefold() for item in spec.path_fragments)
        for found in fields:
            leaf = found.path.rsplit(".", 1)[-1].split("[", 1)[0].casefold()
            if leaf not in aliases or (
                path_fragments
                and not any(fragment in found.path.casefold() for fragment in path_fragments)
            ):
                continue
            reference = normalize_uuid(found.value)
            if reference is None:
                continue
            resolved = index.resolve(found.value, source=node, field_path=found.path)
            diagnostics.extend(resolved.diagnostics)
            if resolved.target is None:
                continue
            target = resolved.target
            if target.struct_name.casefold() not in targets:
                diagnostics.append(
                    Diagnostic(
                        f"{provider.kind.value}-relationship-target-schema-drift",
                        f"Relationship {spec.name!r} resolved to unexpected struct {target.struct_name!r}",
                        Severity.WARNING,
                        node.id,
                        node.normalized_path,
                        found.path,
                    )
                )
                continue
            identity = (spec.name.casefold(), target.id.casefold(), found.path.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            relationships.append(
                EntityRelationship(
                    spec.name,
                    target.id,
                    target.normalized_path,
                    target.struct_name,
                    (
                        Evidence(node.id, node.normalized_path, found.path, reference),
                        Evidence(
                            target.id,
                            target.normalized_path,
                            "$record.file_name",
                            target.record.file_name,
                        ),
                    ),
                )
            )
    return tuple(relationships), tuple(diagnostics)


def _capability(
    spec: ProviderSpec,
    build: str,
    examined: int,
    facts: list[EntityFact],
    diagnostics: list[Diagnostic],
) -> CapabilityReport:
    if not facts:
        status = CapabilityStatus.UNAVAILABLE
    elif any(item.degrades_capability for item in diagnostics):
        status = CapabilityStatus.DEGRADED
    else:
        status = CapabilityStatus.AVAILABLE
    return CapabilityReport(spec.provider, status, build, examined, len(facts), tuple(diagnostics))


def _apply_corrections(
    result: EntityExtractionResult,
    provider: str,
    build: str,
    registry: CorrectionRegistry,
) -> EntityExtractionResult:
    corrections = registry.for_provider(provider, build)
    if not corrections:
        return result
    facts = list(result.facts)
    diagnostics = list(result.capability.diagnostics)
    applied: list[str] = []
    for correction in corrections:
        targets: list[tuple[int, int, EntityValue]] = []
        for fact_index, fact in enumerate(facts):
            if fact.entity_id.casefold() != correction.record_id.casefold():
                continue
            for value_index, value in enumerate(fact.values):
                if value.evidence.field_path.casefold() == correction.field_path.casefold():
                    targets.append((fact_index, value_index, value))
        if not targets:
            diagnostics.append(
                Diagnostic(
                    "correction-target-missing-schema-drift",
                    f"Correction {correction.correction_id!r} did not match its reviewed record field",
                    Severity.WARNING,
                    correction.record_id,
                    field_path=correction.field_path,
                )
            )
            continue
        if len(targets) > 1:
            diagnostics.append(
                Diagnostic(
                    "correction-target-ambiguous-schema-drift",
                    f"Correction {correction.correction_id!r} matched multiple record fields",
                    Severity.WARNING,
                    correction.record_id,
                    field_path=correction.field_path,
                )
            )
            continue
        fact_index, value_index, value = targets[0]
        fact = facts[fact_index]
        if value.value != correction.expected_value:
            diagnostics.append(
                Diagnostic(
                    "correction-source-mismatch-schema-drift",
                    f"Correction {correction.correction_id!r} expected {correction.expected_value!r}, "
                    f"found {value.value!r}; replacement was not applied",
                    Severity.WARNING,
                    fact.entity_id,
                    fact.record_path,
                    value.evidence.field_path,
                )
            )
            continue
        values = list(fact.values)
        values[value_index] = replace(
            value,
            value=correction.replacement_value,
            correction=correction,
            original_value=value.value,
        )
        facts[fact_index] = replace(fact, values=tuple(values))
        applied.append(correction.correction_id)
    capability = replace(
        result.capability,
        status=(
            CapabilityStatus.DEGRADED
            if result.capability.status is CapabilityStatus.AVAILABLE
            and diagnostics
            and any(item.degrades_capability for item in diagnostics)
            else result.capability.status
        ),
        diagnostics=tuple(diagnostics),
    )
    return EntityExtractionResult(tuple(facts), capability, tuple(applied))


def extract_entity_catalog(
    index: DataForgeIndex,
    *,
    build_version: str | None = None,
    providers: Iterable[LocalEntityProvider] | None = None,
    corrections: CorrectionRegistry | None = None,
) -> EntityCatalogResult:
    """Extract all providers and suppress records claimed by multiple peers."""

    if providers is not None and corrections is not None:
        raise ValueError("pass corrections through explicit providers or use the default catalog")
    selected = tuple(providers) if providers is not None else entity_providers(corrections)
    names = [item.spec.provider.casefold() for item in selected]
    if len(names) != len(set(names)):
        raise ValueError("entity provider ids must be unique")
    build = str(index.source.version if build_version is None else build_version)
    results = []
    for provider in sorted(selected, key=lambda item: item.spec.provider):
        try:
            results.append(provider.extract(index, build_version=build_version))
        except Exception:
            results.append(
                EntityExtractionResult(
                    (),
                    CapabilityReport(
                        provider.spec.provider,
                        CapabilityStatus.UNAVAILABLE,
                        build,
                        0,
                        0,
                        (
                            Diagnostic(
                                "provider-exception",
                                "Entity provider failed without affecting peer providers",
                                Severity.WARNING,
                            ),
                        ),
                    ),
                )
            )
    claims: dict[tuple[str, str], set[int]] = {}
    for result_index, result in enumerate(results):
        for fact in result.facts:
            identity = (fact.entity_id.casefold(), fact.record_path.casefold())
            claims.setdefault(identity, set()).add(result_index)
    overlaps = {
        identity: owners for identity, owners in claims.items() if len(owners) > 1
    }
    if not overlaps:
        return EntityCatalogResult(tuple(results))

    for result_index, result in enumerate(results):
        affected = tuple(
            identity for identity, owners in overlaps.items() if result_index in owners
        )
        if not affected:
            continue
        retained = tuple(
            fact
            for fact in result.facts
            if (fact.entity_id.casefold(), fact.record_path.casefold()) not in affected
        )
        diagnostics = list(result.capability.diagnostics)
        diagnostics.extend(
            Diagnostic(
                "provider-classification-overlap-schema-drift",
                f"Record is claimed by multiple entity providers: {record_path}",
                Severity.WARNING,
                record_id,
                record_path,
            )
            for record_id, record_path in affected
        )
        retained_corrections = tuple(
            dict.fromkeys(
                value.correction_id
                for fact in retained
                for value in fact.values
                if value.correction_id is not None
            )
        )
        results[result_index] = EntityExtractionResult(
            retained,
            replace(
                result.capability,
                status=(
                    CapabilityStatus.DEGRADED
                    if retained
                    else CapabilityStatus.UNAVAILABLE
                ),
                facts_emitted=len(retained),
                diagnostics=tuple(diagnostics),
            ),
            retained_corrections,
        )
    return EntityCatalogResult(tuple(results))


VEHICLE_PROVIDER = ProviderSpec(
    "local-dataforge-vehicles",
    "2",
    EntityKind.VEHICLE,
    ("/entities/spaceships/", "/entities/groundvehicles/", "/entities/vehicles/"),
    (
        FieldSpec(
            "name",
            ("displayName", "vehicleName"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "$.vehiclename", "staticentityclassdata"),
        ),
        FieldSpec("mass", ("mass",), ScalarKind.FLOAT, follow_pointers=False),
        FieldSpec(
            "cargo-capacity",
            ("cargoCapacity",),
            ScalarKind.FLOAT,
            follow_pointers=False,
        ),
        FieldSpec("crew-min", ("minCrew",), ScalarKind.INTEGER, follow_pointers=False),
        FieldSpec(
            "crew-max",
            ("maxCrew", "crewSize"),
            ScalarKind.INTEGER,
            path_fragments=("$.maxcrew", "$.crewsize", "staticentityclassdata"),
        ),
    ),
    max_pointer_hops=4,
)

COMPONENT_PROVIDER = ProviderSpec(
    "local-dataforge-components",
    "2",
    EntityKind.COMPONENT,
    (
        "/entities/scitem/ships/armor/",
        "/entities/scitem/ships/cooler/",
        "/entities/scitem/ships/fuel_intakes/",
        "/entities/scitem/ships/fueltanks/",
        "/entities/scitem/ships/jumpdrive/",
        "/entities/scitem/ships/powerplant/",
        "/entities/scitem/ships/quantumdrive/",
        "/entities/scitem/ships/radar/",
        "/entities/scitem/ships/shieldgenerator/",
        "/entities/scitem/ships/thrusters/",
    ),
    (
        FieldSpec(
            "name",
            ("displayName", "name"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "attachdef.localization.name"),
        ),
        FieldSpec(
            "size",
            ("size", "itemSize"),
            ScalarKind.INTEGER,
            path_fragments=("$.size", "$.itemsize", "attachdef.size"),
        ),
        FieldSpec(
            "grade",
            ("grade",),
            ScalarKind.ENUM,
            path_fragments=("attachdef.grade",),
        ),
        FieldSpec("class", ("class", "itemClass"), ScalarKind.STRING),
    ),
)

SHIP_WEAPON_PROVIDER = ProviderSpec(
    "local-dataforge-ship-weapons",
    "2",
    EntityKind.SHIP_WEAPON,
    (
        "/entities/scitem/ships/weapons/",
        "/entities/scitem/weapons/ship/",
        "/entities/scitem/weapons/vehicle/",
    ),
    (
        FieldSpec(
            "name",
            ("displayName", "name"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "attachdef.localization.name"),
        ),
        FieldSpec(
            "size",
            ("size", "itemSize"),
            ScalarKind.INTEGER,
            path_fragments=("$.size", "$.itemsize", "attachdef.size"),
        ),
        FieldSpec(
            "damage",
            ("damage", "damageTotal"),
            ScalarKind.FLOAT,
            path_fragments=("$.damage", "$.damagetotal", "ammoparams"),
        ),
        FieldSpec("rate-of-fire", ("rateOfFire", "roundsPerMinute"), ScalarKind.FLOAT),
        FieldSpec("projectile-speed", ("projectileSpeed", "ammoSpeed"), ScalarKind.FLOAT),
        FieldSpec("range", ("range", "effectiveRange"), ScalarKind.FLOAT),
    ),
)

FPS_WEAPON_PROVIDER = ProviderSpec(
    "local-dataforge-fps-weapons",
    "2",
    EntityKind.FPS_WEAPON,
    (
        "/entities/scitem/weapons/fps_weapons/",
        "/entities/scitem/weapons/fps/",
        "/entities/scitem/weapons/personal/",
    ),
    (
        FieldSpec(
            "name",
            ("displayName", "name"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "attachdef.localization.name"),
        ),
        FieldSpec(
            "damage",
            ("damage", "damageTotal"),
            ScalarKind.FLOAT,
            path_fragments=("$.damage", "$.damagetotal", "ammoparams"),
        ),
        FieldSpec("rate-of-fire", ("rateOfFire", "roundsPerMinute"), ScalarKind.FLOAT),
        FieldSpec("magazine-capacity", ("magazineCapacity", "ammoCount"), ScalarKind.INTEGER),
        FieldSpec("effective-range", ("effectiveRange",), ScalarKind.FLOAT),
    ),
    ("medgun",),
)

MEDICAL_PROVIDER = ProviderSpec(
    "local-dataforge-medical",
    "2",
    EntityKind.MEDICAL,
    (
        "/entities/scitem/medical/",
        "/entities/scitem/consumables/medical/",
        "medgun",
        "medpen",
    ),
    (
        FieldSpec(
            "name",
            ("displayName", "name"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "attachdef.localization.name"),
        ),
        FieldSpec("health-restored", ("healthRestored", "healAmount"), ScalarKind.FLOAT),
        FieldSpec("max-health-repair-rate", ("maxHealthRepairRate",), ScalarKind.FLOAT),
        FieldSpec("max-auto-dose", ("maxDoseForAutoAdjustment",), ScalarKind.FLOAT),
        FieldSpec("overdose-threshold", ("overdoseThreshold",), ScalarKind.FLOAT),
        FieldSpec("toxicity", ("toxicity",), ScalarKind.FLOAT),
    ),
    struct_names=("EntityClassDefinition",),
)

COMMODITY_PROVIDER = ProviderSpec(
    "local-dataforge-commodities",
    "2",
    EntityKind.COMMODITY,
    (
        "/entities/commodities/",
        "/entities/scitem/commodities/",
        "/commodities/tradable/",
    ),
    (
        FieldSpec(
            "name",
            ("displayName",),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "$.components["),
        ),
        FieldSpec("base-price", ("basePrice", "price"), ScalarKind.FLOAT),
        FieldSpec("shop-buy-price", ("buyPrice",), ScalarKind.FLOAT),
        FieldSpec("shop-sell-price", ("sellPrice",), ScalarKind.FLOAT),
    ),
    relationships=(
        RelationshipSpec(
            "composed-of-resource",
            ("entry",),
            ("ResourceType",),
            ("defaultComposition",),
        ),
    ),
)

CRAFTING_PROVIDER = ProviderSpec(
    "local-dataforge-crafting",
    "2",
    EntityKind.CRAFTING,
    (
        "/crafting/blueprints/",
        "/crafting/legacy/recipes/",
        "/crafting/recipes/",
    ),
    (
        FieldSpec(
            "name",
            ("displayName", "blueprintName", "recipeName"),
            ScalarKind.LOCALE_KEY,
            True,
            ("$.displayname", "blueprintname", "recipename"),
        ),
        FieldSpec("craft-time", ("craftTime", "duration", "timeSeconds"), ScalarKind.FLOAT),
        FieldSpec("craft-seconds", ("seconds",), ScalarKind.INTEGER),
        FieldSpec("craft-minutes", ("minutes",), ScalarKind.INTEGER),
        FieldSpec("craft-hours", ("hours",), ScalarKind.INTEGER),
        FieldSpec("craft-days", ("days",), ScalarKind.INTEGER),
        FieldSpec("output-count", ("outputCount",), ScalarKind.INTEGER),
        FieldSpec("required-rank", ("requiredRank",), ScalarKind.INTEGER),
    ),
    ("/crafting/blueprints/dismantle",),
    relationships=(
        RelationshipSpec(
            "requires-resource",
            ("resource",),
            ("ResourceType",),
            ("costs", "mandatoryCost"),
        ),
        RelationshipSpec(
            "produces-entity",
            ("entityClass",),
            ("EntityClassDefinition",),
            ("processSpecificData", "outputs"),
        ),
        RelationshipSpec(
            "requires-entity",
            ("entityClass",),
            ("EntityClassDefinition",),
            ("mandatoryCost",),
        ),
        RelationshipSpec(
            "blueprint-category",
            ("category",),
            ("BlueprintCategoryRecord",),
        ),
    ),
)

JOURNAL_PROVIDER = ProviderSpec(
    "local-dataforge-journal",
    "2",
    EntityKind.JOURNAL,
    ("/journalentry/", "/journal/entries/", "/journalentries/"),
    (
        FieldSpec("title", ("title", "displayName"), ScalarKind.LOCALE_KEY, True),
        FieldSpec("body", ("body", "description", "text"), ScalarKind.LOCALE_KEY),
        FieldSpec("category", ("category", "entryCategory"), ScalarKind.STRING),
        FieldSpec("discovery-tag", ("discoveryTag",), ScalarKind.STRING),
    ),
)


def entity_providers(
    corrections: CorrectionRegistry | None = None,
) -> tuple[LocalEntityProvider, ...]:
    """Return the complete current provider catalog in stable order."""

    return tuple(
        LocalEntityProvider(spec, corrections=corrections)
        for spec in (
            COMMODITY_PROVIDER,
            COMPONENT_PROVIDER,
            CRAFTING_PROVIDER,
            FPS_WEAPON_PROVIDER,
            JOURNAL_PROVIDER,
            MEDICAL_PROVIDER,
            SHIP_WEAPON_PROVIDER,
            VEHICLE_PROVIDER,
        )
    )


def baseline_entity_providers(
    corrections: CorrectionRegistry | None = None,
) -> tuple[LocalEntityProvider, ...]:
    """Return the two providers shipped in the initial G5 checkpoint."""

    return tuple(
        LocalEntityProvider(spec, corrections=corrections)
        for spec in (COMPONENT_PROVIDER, VEHICLE_PROVIDER)
    )


def presentation_entity_providers(
    corrections: CorrectionRegistry | None = None,
) -> tuple[LocalEntityProvider, ...]:
    """Use only name/tag fields; omit unrelated stats and graph relationships."""

    selected = []
    for spec in (
        COMMODITY_PROVIDER,
        COMPONENT_PROVIDER,
        FPS_WEAPON_PROVIDER,
        MEDICAL_PROVIDER,
        SHIP_WEAPON_PROVIDER,
        VEHICLE_PROVIDER,
    ):
        fields = tuple(
            field
            for field in spec.fields
            if field.name in {"name", "size", "grade", "class"}
        )
        selected.append(
            LocalEntityProvider(
                replace(spec, fields=fields, relationships=()),
                corrections=corrections,
            )
        )
    return tuple(selected)
