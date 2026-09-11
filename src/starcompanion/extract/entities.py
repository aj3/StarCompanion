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

    def __post_init__(self) -> None:
        if not self.name or not self.source_names or any(not item for item in self.source_names):
            raise ValueError("field specs require non-empty names")


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    version: str
    kind: EntityKind
    path_fragments: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    excluded_path_fragments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider or not self.version or not self.path_fragments:
            raise ValueError("provider specs require identity and path fragments")
        names = [item.name.casefold() for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("provider fact names must be unique")


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
class EntityFact:
    entity_id: str
    kind: EntityKind
    record_path: str
    values: tuple[EntityValue, ...]

    def get(self, name: str) -> EntityValue | None:
        wanted = name.casefold()
        return next((item for item in self.values if item.name.casefold() == wanted), None)


@dataclass(frozen=True)
class EntityExtractionResult:
    facts: tuple[EntityFact, ...]
    capability: CapabilityReport
    corrections_applied: tuple[str, ...] = ()


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
        if self.expected_value == self.replacement_value:
            raise ValueError("a correction must change the source value")


class CorrectionRegistry:
    """Validated immutable collection of exact-build data corrections."""

    def __init__(self, corrections: Iterable[BuildCorrection] = ()) -> None:
        items = tuple(corrections)
        ids = [item.correction_id for item in items]
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

    def extract(self, index: DataForgeIndex, *, build_version: str | None = None) -> EntityExtractionResult:
        build = str(index.source.version if build_version is None else build_version)
        nodes = tuple(
            node
            for node in index.nodes
            if any(fragment.casefold() in node.normalized_path for fragment in self.spec.path_fragments)
            and not any(
                fragment.casefold() in node.normalized_path
                for fragment in self.spec.excluded_path_fragments
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
            fields = tuple(index.iter_fields(node))
            values: list[EntityValue] = []
            for field_spec in self.spec.fields:
                matches = _matching_scalars(fields, field_spec.source_names)
                if not matches:
                    if field_spec.required:
                        diagnostics.append(
                            Diagnostic(
                                f"{self.spec.kind.value}-field-schema-drift",
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
                    item.diagnostic for _found, item in converted if item.diagnostic is not None
                )
                valid = [(found, item.value) for found, item in converted if item.value is not None]
                if field_spec.required and not valid:
                    diagnostics.append(
                        Diagnostic(
                            f"{self.spec.kind.value}-field-schema-drift",
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
            if values:
                facts.append(EntityFact(node.id, self.spec.kind, node.normalized_path, tuple(values)))

        result = EntityExtractionResult(tuple(facts), _capability(self.spec, build, len(nodes), facts, diagnostics))
        return _apply_corrections(result, self.spec.provider, build, self.corrections)


def _matching_scalars(fields: tuple[FieldValue, ...], names: tuple[str, ...]) -> tuple[FieldValue, ...]:
    wanted = {name.casefold() for name in names}
    return tuple(
        found
        for found in fields
        if found.path.rsplit(".", 1)[-1].split("[", 1)[0].casefold() in wanted
        and isinstance(found.value, (str, int, float, bool))
    )


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


VEHICLE_PROVIDER = ProviderSpec(
    "local-dataforge-vehicles",
    "1",
    EntityKind.VEHICLE,
    ("/entities/spaceships/", "/entities/vehicles/"),
    (
        FieldSpec("name", ("displayName", "vehicleName"), ScalarKind.LOCALE_KEY, True),
        FieldSpec("mass", ("mass",), ScalarKind.FLOAT),
        FieldSpec("cargo-capacity", ("cargoCapacity",), ScalarKind.FLOAT),
        FieldSpec("crew-min", ("minCrew",), ScalarKind.INTEGER),
        FieldSpec("crew-max", ("maxCrew",), ScalarKind.INTEGER),
    ),
)

COMPONENT_PROVIDER = ProviderSpec(
    "local-dataforge-components",
    "1",
    EntityKind.COMPONENT,
    ("/entities/scitem/",),
    (
        FieldSpec("name", ("displayName",), ScalarKind.LOCALE_KEY, True),
        FieldSpec("size", ("size", "itemSize"), ScalarKind.INTEGER),
        FieldSpec("grade", ("grade",), ScalarKind.STRING),
        FieldSpec("class", ("class", "itemClass"), ScalarKind.STRING),
    ),
    ("/weapons/", "/medical/", "/commodities/"),
)


def baseline_entity_providers(
    corrections: CorrectionRegistry | None = None,
) -> tuple[LocalEntityProvider, ...]:
    """Return the initial G5 providers in deterministic order."""

    return tuple(
        LocalEntityProvider(spec, corrections=corrections)
        for spec in (COMPONENT_PROVIDER, VEHICLE_PROVIDER)
    )
