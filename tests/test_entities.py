from __future__ import annotations

import pytest

from starcompanion.extract.dataforge import CapabilityStatus, DataForgeIndex, ScalarKind
from starcompanion.extract.entities import (
    BuildCorrection,
    COMMODITY_PROVIDER,
    CorrectionRegistry,
    CRAFTING_PROVIDER,
    EntityKind,
    FieldSpec,
    FPS_WEAPON_PROVIDER,
    JOURNAL_PROVIDER,
    LocalEntityProvider,
    MEDICAL_PROVIDER,
    ProviderSpec,
    SHIP_WEAPON_PROVIDER,
    VEHICLE_PROVIDER,
    baseline_entity_providers,
    entity_providers,
    extract_entity_catalog,
)
from test_dataforge import SyntheticDataCore


VEHICLE_ID = "10000000-0000-0000-0000-000000000001"
COMPONENT_ID = "10000000-0000-0000-0000-000000000002"
SPECIALIZED_IDS = {
    "ship": "10000000-0000-0000-0000-000000000011",
    "fps": "10000000-0000-0000-0000-000000000012",
    "medical": "10000000-0000-0000-0000-000000000013",
    "commodity": "10000000-0000-0000-0000-000000000014",
    "crafting": "10000000-0000-0000-0000-000000000015",
    "journal": "10000000-0000-0000-0000-000000000016",
}


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


def specialized_fixture(*, medical_payload=None):
    source = entity_fixture()
    source.entries.extend(
        [
            (
                "ShipWeapon.Test",
                "Data/Libs/Foundry/Records/Entities/SCItem/Weapons/Ship/laser.xml",
                SPECIALIZED_IDS["ship"],
                {
                    "displayName": "@item_name_ship_laser",
                    "itemSize": 3,
                    "damageTotal": 650,
                    "roundsPerMinute": 120,
                    "projectileSpeed": 1400,
                    "effectiveRange": 3200,
                },
            ),
            (
                "FpsWeapon.Test",
                "Data/Libs/Foundry/Records/Entities/SCItem/Weapons/FPS/rifle.xml",
                SPECIALIZED_IDS["fps"],
                {
                    "displayName": "@item_name_fps_rifle",
                    "damage": 42,
                    "rateOfFire": 720,
                    "magazineCapacity": 30,
                    "effectiveRange": 110,
                },
            ),
            (
                "Medical.Test",
                "Data/Libs/Foundry/Records/Entities/SCItem/Consumables/Medical/medpen.xml",
                SPECIALIZED_IDS["medical"],
                medical_payload
                or {
                    "displayName": "@item_name_medpen",
                    "healAmount": 35,
                    "effectDuration": 12,
                    "overdoseThreshold": 4,
                    "toxicity": 0.1,
                },
            ),
            (
                "Commodity.Test",
                "Data/Libs/Foundry/Records/Commodities/Tradable/ore.xml",
                SPECIALIZED_IDS["commodity"],
                {
                    "displayName": "@commodity_name_ore",
                    "basePrice": 12.5,
                    "buyPrice": 10,
                    "sellPrice": 14,
                },
            ),
            (
                "Crafting.Test",
                "Data/Libs/Foundry/Records/Crafting/Recipes/laser.xml",
                SPECIALIZED_IDS["crafting"],
                {
                    "recipeName": "@recipe_name_laser",
                    "craftTime": 45,
                    "outputCount": 2,
                    "requiredRank": 3,
                },
            ),
            (
                "Journal.Test",
                "Data/Libs/Foundry/Records/Journal/Entries/cave.xml",
                SPECIALIZED_IDS["journal"],
                {
                    "title": "@journal_title_cave",
                    "body": "@journal_body_cave",
                    "entryCategory": "Discovery",
                    "discoveryTag": "CAVE",
                },
            ),
        ]
    )
    source.__post_init__()
    return source


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


def test_specialized_catalog_emits_typed_evidence_for_every_provider():
    catalog = extract_entity_catalog(DataForgeIndex(specialized_fixture()))

    assert len(catalog.provider_results) == len(entity_providers()) == 8
    assert all(
        result.capability.status is CapabilityStatus.AVAILABLE
        for result in catalog.provider_results
    )
    assert len(catalog.facts) == 8
    assert catalog.status_counts == (
        (CapabilityStatus.AVAILABLE, 8),
        (CapabilityStatus.DEGRADED, 0),
        (CapabilityStatus.UNAVAILABLE, 0),
    )
    assert catalog.evidence_links == sum(len(fact.values) for fact in catalog.facts)
    assert not catalog.corrections_applied
    expected = {
        SHIP_WEAPON_PROVIDER.provider: ("damage", 650.0),
        FPS_WEAPON_PROVIDER.provider: ("magazine-capacity", 30),
        MEDICAL_PROVIDER.provider: ("health-restored", 35.0),
        COMMODITY_PROVIDER.provider: ("base-price", 12.5),
        CRAFTING_PROVIDER.provider: ("output-count", 2),
        JOURNAL_PROVIDER.provider: ("discovery-tag", "CAVE"),
    }
    for provider, (field, value) in expected.items():
        result = catalog.for_provider(provider)
        fact_value = result.facts[0].get(field)
        assert fact_value.value == value
        assert fact_value.evidence.record_id == result.facts[0].entity_id
        assert fact_value.evidence.field_path.startswith("$.")


def test_specialized_schema_drift_does_not_contaminate_peer_capabilities():
    catalog = extract_entity_catalog(
        DataForgeIndex(specialized_fixture(medical_payload={"healAmount": 35}))
    )

    medical = catalog.for_provider(MEDICAL_PROVIDER.provider)
    assert medical.capability.status is CapabilityStatus.DEGRADED
    assert medical.capability.facts_emitted == 1
    assert medical.facts[0].get("health-restored").value == 35.0
    assert all(
        result.capability.status is CapabilityStatus.AVAILABLE
        for result in catalog.provider_results
        if result.capability.provider != MEDICAL_PROVIDER.provider
    )


def test_invalid_optional_scalar_degrades_only_its_provider():
    source = specialized_fixture()
    medical_payload = next(
        payload
        for _name, _path, record_id, payload in source.entries
        if record_id == SPECIALIZED_IDS["medical"]
    )
    medical_payload["toxicity"] = "not-a-number"
    source.__post_init__()

    catalog = extract_entity_catalog(DataForgeIndex(source))
    medical = catalog.for_provider(MEDICAL_PROVIDER.provider)

    assert medical.capability.status is CapabilityStatus.DEGRADED
    assert "medical-field-conversion-schema-drift" in {
        item.code for item in medical.capability.diagnostics
    }
    assert all(
        result.capability.status is CapabilityStatus.AVAILABLE
        for result in catalog.provider_results
        if result.capability.provider != MEDICAL_PROVIDER.provider
    )


def test_catalog_suppresses_ambiguous_cross_provider_classification():
    overlap_id = "10000000-0000-0000-0000-000000000099"
    source = entity_fixture()
    source.entries.append(
        (
            "Overlap.Test",
            "Data/Libs/Foundry/Records/Overlap/test.xml",
            overlap_id,
            {"displayName": "@overlap_name"},
        )
    )
    source.__post_init__()
    field = FieldSpec("name", ("displayName",), ScalarKind.LOCALE_KEY, True)
    providers = (
        LocalEntityProvider(
            ProviderSpec("overlap-a", "1", EntityKind.COMPONENT, ("/overlap/",), (field,))
        ),
        LocalEntityProvider(
            ProviderSpec("overlap-b", "1", EntityKind.COMMODITY, ("/overlap/",), (field,))
        ),
        LocalEntityProvider(VEHICLE_PROVIDER),
    )

    catalog = extract_entity_catalog(DataForgeIndex(source), providers=providers)

    assert catalog.for_provider(VEHICLE_PROVIDER.provider).capability.status is CapabilityStatus.AVAILABLE
    for provider in ("overlap-a", "overlap-b"):
        result = catalog.for_provider(provider)
        assert result.capability.status is CapabilityStatus.UNAVAILABLE
        assert not result.facts
        assert result.capability.diagnostics[-1].code == (
            "provider-classification-overlap-schema-drift"
        )


def test_catalog_rejects_duplicate_provider_identity():
    provider = LocalEntityProvider(VEHICLE_PROVIDER)
    with pytest.raises(ValueError, match="provider ids"):
        extract_entity_catalog(DataForgeIndex(entity_fixture()), providers=(provider, provider))

    with pytest.raises(ValueError, match="pass corrections"):
        extract_entity_catalog(
            DataForgeIndex(entity_fixture()),
            providers=(provider,),
            corrections=CorrectionRegistry(),
        )


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


def test_component_provider_excludes_records_owned_by_specialized_peers():
    source = entity_fixture()
    source.entries.append(
        (
            "Weapon.Test",
            "Data/Libs/Foundry/Records/Entities/SCItem/Weapons/Test/weapon.xml",
            "10000000-0000-0000-0000-000000000003",
            {"displayName": "@weapon_name_test", "itemSize": 2},
        )
    )
    source.__post_init__()

    component = baseline_entity_providers()[0].extract(DataForgeIndex(source))

    assert component.capability.records_examined == 1
    assert {fact.entity_id for fact in component.facts} == {COMPONENT_ID}


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


def test_default_catalog_reports_applied_corrections():
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

    catalog = extract_entity_catalog(
        DataForgeIndex(specialized_fixture()),
        build_version="SC-42",
        corrections=CorrectionRegistry((correction,)),
    )

    assert catalog.corrections_applied == (correction.correction_id,)
    vehicle = catalog.for_provider(VEHICLE_PROVIDER.provider)
    assert vehicle.facts[0].get("mass").value == 25500.0


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
    with pytest.raises(ValueError, match="ids"):
        CorrectionRegistry(
            (
                correction,
                BuildCorrection(**{**correction.__dict__, "correction_id": "ONE"}),
            )
        )
    with pytest.raises(ValueError, match="one correction"):
        CorrectionRegistry((correction, BuildCorrection(**{**correction.__dict__, "correction_id": "two"})))


def test_provider_and_correction_declarations_reject_ambiguous_types():
    with pytest.raises(ValueError, match="source names"):
        FieldSpec("name", ("Name", "name"), ScalarKind.STRING)
    with pytest.raises(ValueError, match="path fragments"):
        ProviderSpec(
            "test",
            "1",
            EntityKind.COMPONENT,
            ("",),
            (FieldSpec("name", ("name",), ScalarKind.STRING),),
        )
    with pytest.raises(ValueError, match="require fields"):
        ProviderSpec("test", "1", EntityKind.COMPONENT, ("/test/",), ())
    with pytest.raises(ValueError, match="scalar type"):
        BuildCorrection(
            "typed",
            VEHICLE_PROVIDER.provider,
            "42",
            VEHICLE_ID,
            "$.mass",
            1,
            2.0,
            "reviewed",
            "local test",
        )
