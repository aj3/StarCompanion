from __future__ import annotations

import pytest

from starcompanion.extract.dataforge import CapabilityStatus, DataForgeIndex
from starcompanion.extract.entities import (
    BuildCorrection,
    CorrectionRegistry,
    LocalEntityProvider,
    VEHICLE_PROVIDER,
    baseline_entity_providers,
)
from test_dataforge import SyntheticDataCore


VEHICLE_ID = "10000000-0000-0000-0000-000000000001"
COMPONENT_ID = "10000000-0000-0000-0000-000000000002"


def entity_fixture(*, vehicle_payload=None, component_payload=None, version=42):
    return SyntheticDataCore(
        [
            (
                "Vehicle.Test",
                "Data/Libs/Foundry/Records/Entities/Spaceships/Test/vehicle.xml",
                VEHICLE_ID,
                vehicle_payload
                or {
                    "vehicleName": "@vehicle_name_test",
                    "stats": {"mass": "25000", "cargoCapacity": 48},
                    "crew": {"minCrew": 1, "maxCrew": 3},
                },
            ),
            (
                "Component.Test",
                "Data/Libs/Foundry/Records/Entities/SCItem/Test/component.xml",
                COMPONENT_ID,
                component_payload
                or {
                    "displayName": "@item_name_test",
                    "itemSize": 2,
                    "grade": "A",
                    "itemClass": "Military",
                },
            ),
        ],
        version=version,
    )


def test_synthetic_providers_emit_typed_values_with_per_value_evidence():
    index = DataForgeIndex(entity_fixture())
    component, vehicle = [provider.extract(index) for provider in baseline_entity_providers()]

    assert component.capability.status is CapabilityStatus.AVAILABLE
    assert component.capability.records_examined == 1
    assert component.facts[0].get("size").value == 2
    assert component.facts[0].get("size").evidence.field_path == "$.itemSize"
    assert vehicle.capability.status is CapabilityStatus.AVAILABLE
    assert vehicle.facts[0].get("name").value == "vehicle_name_test"
    assert vehicle.facts[0].get("mass").value == 25000.0
    assert all(value.evidence.record_id == VEHICLE_ID for value in vehicle.facts[0].values)


def test_schema_drift_is_reported_by_only_the_affected_provider():
    index = DataForgeIndex(entity_fixture(vehicle_payload={"mass": 25_000}))
    component, vehicle = [provider.extract(index) for provider in baseline_entity_providers()]

    assert component.capability.status is CapabilityStatus.AVAILABLE
    assert not component.capability.diagnostics
    assert vehicle.capability.status is CapabilityStatus.DEGRADED
    assert {item.code for item in vehicle.capability.diagnostics} == {
        "vehicle-field-schema-drift"
    }


def test_invalid_required_value_is_schema_drift_not_silent_data_loss():
    result = LocalEntityProvider(VEHICLE_PROVIDER).extract(
        DataForgeIndex(entity_fixture(vehicle_payload={"vehicleName": 99, "mass": 25_000}))
    )

    assert result.capability.status is CapabilityStatus.DEGRADED
    assert "vehicle-field-schema-drift" in {
        item.code for item in result.capability.diagnostics
    }


def test_absent_provider_is_unavailable_without_affecting_peer():
    source = entity_fixture()
    source.entries = source.entries[:1]
    source.__post_init__()
    index = DataForgeIndex(source)
    component, vehicle = [provider.extract(index) for provider in baseline_entity_providers()]

    assert component.capability.status is CapabilityStatus.UNAVAILABLE
    assert vehicle.capability.status is CapabilityStatus.AVAILABLE


def test_build_scoped_correction_preserves_original_and_source_evidence():
    correction = BuildCorrection(
        "vehicle-test-mass-42",
        VEHICLE_PROVIDER.provider,
        "SC-42",
        VEHICLE_ID,
        "$.stats.mass",
        25000.0,
        25500.0,
        "Synthetic fixture models a reviewed upstream typo.",
        "tests/test_entities.py synthetic evidence",
    )
    provider = LocalEntityProvider(
        VEHICLE_PROVIDER,
        corrections=CorrectionRegistry((correction,)),
    )

    corrected = provider.extract(DataForgeIndex(entity_fixture()), build_version="SC-42")
    untouched = provider.extract(DataForgeIndex(entity_fixture()), build_version="SC-43")

    value = corrected.facts[0].get("mass")
    assert value.value == 25500.0
    assert value.original_value == 25000.0
    assert value.correction_id == correction.correction_id
    assert value.correction.rationale == correction.rationale
    assert value.correction.source == correction.source
    assert value.evidence.value == 25000.0
    assert corrected.corrections_applied == (correction.correction_id,)
    assert untouched.facts[0].get("mass").value == 25000.0
    assert not untouched.corrections_applied


def test_correction_fails_closed_when_source_value_drifted():
    correction = BuildCorrection(
        "vehicle-test-mass-42",
        VEHICLE_PROVIDER.provider,
        "SC-42",
        VEHICLE_ID,
        "$.stats.mass",
        1.0,
        2.0,
        "Synthetic fixture models a reviewed upstream typo.",
        "tests/test_entities.py synthetic evidence",
    )
    result = LocalEntityProvider(
        VEHICLE_PROVIDER,
        corrections=CorrectionRegistry((correction,)),
    ).extract(DataForgeIndex(entity_fixture()), build_version="SC-42")

    assert result.facts[0].get("mass").value == 25000.0
    assert not result.corrections_applied
    assert result.capability.status is CapabilityStatus.DEGRADED
    assert result.capability.diagnostics[-1].code == "correction-source-mismatch-schema-drift"


def test_missing_provider_stays_unavailable_when_correction_target_is_absent():
    correction = BuildCorrection(
        "vehicle-missing-42",
        VEHICLE_PROVIDER.provider,
        "SC-42",
        VEHICLE_ID,
        "$.mass",
        1.0,
        2.0,
        "reviewed",
        "local test",
    )
    source = SyntheticDataCore([], version=42)
    result = LocalEntityProvider(
        VEHICLE_PROVIDER,
        corrections=CorrectionRegistry((correction,)),
    ).extract(DataForgeIndex(source), build_version="SC-42")

    assert result.capability.status is CapabilityStatus.UNAVAILABLE
    assert not result.corrections_applied


def test_correction_refuses_ambiguous_duplicate_record_targets():
    correction = BuildCorrection(
        "vehicle-duplicate-42",
        VEHICLE_PROVIDER.provider,
        "SC-42",
        VEHICLE_ID,
        "$.mass",
        25000.0,
        25500.0,
        "reviewed",
        "local test",
    )
    entries = [
        (
            f"Vehicle.Test.{position}",
            f"Data/Libs/Foundry/Records/Entities/Spaceships/Test/{position}.xml",
            VEHICLE_ID,
            {"displayName": "@vehicle_name_test", "mass": 25000},
        )
        for position in range(2)
    ]
    result = LocalEntityProvider(
        VEHICLE_PROVIDER,
        corrections=CorrectionRegistry((correction,)),
    ).extract(DataForgeIndex(SyntheticDataCore(entries)), build_version="SC-42")

    assert all(fact.get("mass").value == 25000.0 for fact in result.facts)
    assert not result.corrections_applied
    assert result.capability.status is CapabilityStatus.DEGRADED
    assert result.capability.diagnostics[-1].code == (
        "correction-target-ambiguous-schema-drift"
    )


def test_registry_rejects_duplicate_ids_and_overlapping_targets():
    correction = BuildCorrection(
        "one",
        VEHICLE_PROVIDER.provider,
        "42",
        VEHICLE_ID,
        "$.mass",
        1.0,
        2.0,
        "reviewed",
        "local test",
    )
    with pytest.raises(ValueError, match="ids"):
        CorrectionRegistry((correction, correction))
    with pytest.raises(ValueError, match="one correction"):
        CorrectionRegistry((correction, BuildCorrection(**{**correction.__dict__, "correction_id": "two"})))
