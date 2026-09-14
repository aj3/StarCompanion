from __future__ import annotations

from dataclasses import replace

import pytest

from starcompanion.extract.dataforge import CapabilityStatus, Confidence, DataForgeIndex
from starcompanion.extract.mission_tactical import (
    CLASSIFICATION_PROVIDER,
    ENGAGEMENT_PROVIDER,
    SPAWN_PROVIDER,
    MissionTacticalProvider,
    TacticalFieldSpec,
    TacticalProviderSpec,
    extract_mission_tactical_catalog,
)
from test_dataforge import SyntheticDataCore


ID = {
    "mission-a": "20000000-0000-0000-0000-000000000001",
    "mission-b": "20000000-0000-0000-0000-000000000002",
    "spawn": "20000000-0000-0000-0000-000000000003",
    "engagement": "20000000-0000-0000-0000-000000000004",
    "missing": "20000000-0000-0000-0000-000000000099",
}


def tactical_fixture(*, spawn_reference=None, direct_spawn=None):
    contract = {
        "title": "@Bounty_Title",
        "description": "@Bounty_Desc",
        "missionType": "Bounty",
        "difficultyCode": "H",
        "spawnConfig": spawn_reference or ID["spawn"],
        "engagementConfig": ID["engagement"],
    }
    if direct_spawn:
        contract.update(direct_spawn)
    return SyntheticDataCore(
        [
            (
                "Contract.Bounty",
                "records/contracts/contractgenerator/bounty.xml",
                ID["mission-a"],
                contract,
            ),
            (
                "Spawn.Bounty",
                "records/missions/spawns/bounty.xml",
                ID["spawn"],
                {
                    "friendlyCount": 2,
                    "hostileCount": 7,
                    "hasAcePilot": True,
                    "acePilotChance": 0.25,
                },
            ),
            (
                "Engagement.Bounty",
                "records/missions/engagements/bounty.xml",
                ID["engagement"],
                {
                    "turretCount": 3,
                    "engagementDistance": 2500,
                    "combatStyle": "Interdiction",
                },
            ),
        ],
        version=51,
    )


def test_tactical_catalog_extracts_all_families_with_field_evidence():
    catalog = extract_mission_tactical_catalog(tactical_fixture(), build_version="SC-51")

    assert len(catalog.provider_results) == 3
    assert all(
        result.capability.status is CapabilityStatus.AVAILABLE
        for result in catalog.provider_results
    )
    classification = catalog.for_provider(CLASSIFICATION_PROVIDER.provider).facts[0]
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider).facts[0]
    engagement = catalog.for_provider(ENGAGEMENT_PROVIDER.provider).facts[0]

    assert classification.get("mission-type").value == "Bounty"
    assert classification.get("difficulty").value == "H"
    assert spawn.get("friendly-spawns").value == 2
    assert spawn.get("hostile-spawns").value == 7
    assert spawn.get("ace-pilot").confidence is Confidence.HIGH
    assert spawn.get("ace-probability").confidence is Confidence.MEDIUM
    assert spawn.confidence is Confidence.MEDIUM
    assert engagement.get("turret-count").value == 3
    assert engagement.get("engagement-distance").value == 2500.0
    assert engagement.get("engagement-type").value == "Interdiction"
    assert spawn.match_keys == ("Bounty_Title", "Bounty_Desc")
    assert catalog.evidence_links == sum(
        len(value.evidence) for fact in catalog.facts for value in fact.values
    )


def test_reference_join_preserves_edge_and_target_evidence():
    catalog = extract_mission_tactical_catalog(tactical_fixture())
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider).facts[0]
    value = spawn.get("hostile-spawns")

    assert [item.record_id for item in value.evidence] == [ID["mission-a"], ID["spawn"]]
    assert value.evidence[0].field_path == "$.spawnConfig"
    assert value.evidence[1].field_path == "$.hostileCount"
    assert set(spawn.source_record_ids) == {ID["mission-a"], ID["spawn"]}


def test_missing_spawn_reference_does_not_contaminate_peer_providers():
    source = tactical_fixture(spawn_reference=ID["missing"])
    source.entries = [entry for entry in source.entries if entry[2] != ID["spawn"]]
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)

    assert catalog.for_provider(CLASSIFICATION_PROVIDER.provider).capability.status is CapabilityStatus.AVAILABLE
    assert catalog.for_provider(ENGAGEMENT_PROVIDER.provider).capability.status is CapabilityStatus.AVAILABLE
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider)
    assert spawn.capability.status is CapabilityStatus.UNAVAILABLE
    assert {item.code for item in spawn.capability.diagnostics} >= {
        "missing-reference-target",
        f"{SPAWN_PROVIDER.provider}-evidence-unavailable",
    }


def test_live_shape_aliases_join_type_and_sum_role_scoped_spawn_limits():
    mission_type = "20000000-0000-0000-0000-000000000010"
    source = SyntheticDataCore(
        [
            (
                "Contract.LiveShape",
                "records/contracts/contractgenerator/live.xml",
                ID["mission-a"],
                {
                    "title": "@Live_Title",
                    "missionTypeOverride": mission_type,
                    "contractResults": {
                        "difficulty": {
                            "riskOfLoss": "High",
                            "gameKnowledge": "Medium",
                            "mentalLoad": "Low",
                            "mechanicalSkill": "VeryHigh",
                        }
                    },
                    "spawnDescriptions": [
                        {
                            "options": [
                                {
                                    "autoSpawnSettings": {
                                        "missionAlliedMarker": False,
                                        "maxSpawns": 4,
                                    }
                                },
                                {
                                    "autoSpawnSettings": {
                                        "missionAlliedMarker": True,
                                        "maxSpawns": 2,
                                    }
                                },
                            ]
                        }
                    ],
                },
            ),
            (
                "MissionType.Live",
                "records/contracts/types/bounty.xml",
                mission_type,
                {"LocalisedTypeName": "@MissionType_Bounty"},
            ),
        ]
    )
    catalog = extract_mission_tactical_catalog(source)

    classification = catalog.for_provider(CLASSIFICATION_PROVIDER.provider).facts[0]
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider).facts[0]
    assert classification.get("mission-type").value == "MissionType_Bounty"
    assert classification.get("difficulty-risk").value == "High"
    assert spawn.get("friendly-spawns").value == 2
    assert spawn.get("hostile-spawns").value == 4
    assert len(spawn.get("friendly-spawns").evidence) == 2


def test_spawn_totals_keep_identical_field_paths_from_distinct_records():
    second_spawn = "20000000-0000-0000-0000-000000000011"
    def spawn_payload(limit):
        return {
            "spawnDescriptions": [
                {
                    "autoSpawnSettings": {
                        "missionAlliedMarker": False,
                        "maxSpawns": limit,
                    }
                }
            ]
        }

    source = SyntheticDataCore(
        [
            (
                "Contract.MultiSpawn",
                "records/contracts/contractgenerator/multi.xml",
                ID["mission-a"],
                {
                    "title": "@Multi_Title",
                    "spawnConfig": ID["spawn"],
                    "spawnDefinition": second_spawn,
                },
            ),
            (
                "Spawn.First",
                "records/missions/spawns/first.xml",
                ID["spawn"],
                spawn_payload(3),
            ),
            (
                "Spawn.Second",
                "records/missions/spawns/second.xml",
                second_spawn,
                spawn_payload(5),
            ),
        ]
    )

    spawn = extract_mission_tactical_catalog(source).for_provider(
        SPAWN_PROVIDER.provider
    ).facts[0]

    assert spawn.get("hostile-spawns").value == 8
    assert len(spawn.get("hostile-spawns").evidence) == 6


def test_shared_description_is_suppressed_using_all_missions_not_only_emitted_facts():
    source = tactical_fixture()
    source.entries.append(
        (
            "Contract.Shared",
            "records/contracts/contractgenerator/shared.xml",
            ID["mission-b"],
            {
                "title": "@Other_Title",
                "description": "@Bounty_Desc",
                "missionType": "Delivery",
            },
        )
    )
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider).facts[0]

    assert spawn.description_keys == ("Bounty_Desc",)
    assert spawn.match_keys == ("Bounty_Title",)
    assert spawn.confidence is Confidence.MEDIUM
    assert spawn.diagnostics[-1].code == "shared-description-ambiguous"


def test_shared_description_only_mission_has_no_unsafe_match_key():
    source = tactical_fixture()
    first_payload = source.entries[0][3]
    first_payload.pop("title")
    source.entries.append(
        (
            "Contract.Shared",
            "records/contracts/contractgenerator/shared.xml",
            ID["mission-b"],
            {"description": "@Bounty_Desc", "missionType": "Delivery"},
        )
    )
    source.__post_init__()

    result = MissionTacticalProvider(CLASSIFICATION_PROVIDER).extract(DataForgeIndex(source))
    first = next(fact for fact in result.facts if fact.mission_id == ID["mission-a"])

    assert first.match_keys == ()
    assert first.confidence is Confidence.LOW


def test_conflicting_direct_and_referenced_value_is_suppressed_and_degraded():
    catalog = extract_mission_tactical_catalog(
        tactical_fixture(direct_spawn={"hostileCount": 99})
    )
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider)

    assert spawn.capability.status is CapabilityStatus.DEGRADED
    assert spawn.facts[0].get("hostile-spawns") is None
    assert spawn.facts[0].get("friendly-spawns").value == 2
    assert f"{SPAWN_PROVIDER.provider}-field-ambiguous-schema-drift" in {
        item.code for item in spawn.capability.diagnostics
    }


def test_duplicate_reference_target_is_diagnostic_and_never_guessed():
    source = tactical_fixture()
    source.entries.append(
        (
            "Spawn.Duplicate",
            "records/missions/spawns/duplicate.xml",
            ID["spawn"],
            {"friendlyCount": 500},
        )
    )
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider)

    assert spawn.capability.status is CapabilityStatus.UNAVAILABLE
    assert not spawn.facts
    assert "ambiguous-reference" in {item.code for item in spawn.capability.diagnostics}


def test_reference_cycle_is_bounded_and_retains_direct_target_facts():
    source = tactical_fixture()
    spawn_payload = source.entries[1][3]
    spawn_payload["spawnProfile"] = ID["spawn"]
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)
    spawn = catalog.for_provider(SPAWN_PROVIDER.provider)

    assert spawn.facts[0].get("friendly-spawns").value == 2
    assert "reference-cycle" in {item.code for item in spawn.capability.diagnostics}


def test_converging_reference_aliases_are_not_mislabeled_as_a_cycle():
    source = tactical_fixture()
    source.entries[0][3]["spawnProfile"] = ID["spawn"]
    source.__post_init__()

    spawn = MissionTacticalProvider(SPAWN_PROVIDER).extract(DataForgeIndex(source))

    assert spawn.facts[0].get("friendly-spawns").value == 2
    assert "reference-cycle" not in {item.code for item in spawn.capability.diagnostics}


def test_reference_hop_limit_stops_adversarial_deep_graph():
    next_id = "20000000-0000-0000-0000-000000000005"
    source = tactical_fixture()
    source.entries[1][3]["spawnProfile"] = next_id
    source.entries.append(
        (
            "Spawn.Deep",
            "records/missions/spawns/deep.xml",
            next_id,
            {"hostileCount": 999},
        )
    )
    source.__post_init__()
    provider = MissionTacticalProvider(replace(SPAWN_PROVIDER, max_reference_hops=1))

    result = provider.extract(DataForgeIndex(source))

    assert result.facts[0].get("friendly-spawns").value == 2
    assert result.facts[0].get("hostile-spawns").value == 7
    assert "reference-hop-limit" in {item.code for item in result.capability.diagnostics}


def test_invalid_scalar_degrades_only_the_owning_provider():
    source = tactical_fixture()
    source.entries[2][3]["turretCount"] = "many"
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)

    engagement = catalog.for_provider(ENGAGEMENT_PROVIDER.provider)
    assert engagement.capability.status is CapabilityStatus.DEGRADED
    assert engagement.facts[0].get("turret-count") is None
    assert catalog.for_provider(CLASSIFICATION_PROVIDER.provider).capability.status is CapabilityStatus.AVAILABLE
    assert catalog.for_provider(SPAWN_PROVIDER.provider).capability.status is CapabilityStatus.AVAILABLE


def test_adversarial_ranges_and_text_are_suppressed_not_emitted():
    source = tactical_fixture()
    contract = source.entries[0][3]
    contract["missionType"] = "X" * 10_000
    spawn = source.entries[1][3]
    spawn["friendlyCount"] = -1
    spawn["acePilotChance"] = 9
    source.__post_init__()

    catalog = extract_mission_tactical_catalog(source)
    classification = catalog.for_provider(CLASSIFICATION_PROVIDER.provider)
    spawn_result = catalog.for_provider(SPAWN_PROVIDER.provider)

    assert classification.capability.status is CapabilityStatus.DEGRADED
    assert classification.facts[0].get("mission-type") is None
    assert classification.facts[0].get("difficulty").value == "H"
    assert spawn_result.capability.status is CapabilityStatus.DEGRADED
    assert spawn_result.facts[0].get("friendly-spawns") is None
    assert spawn_result.facts[0].get("ace-probability") is None
    assert {
        f"{CLASSIFICATION_PROVIDER.provider}-field-value-invalid-schema-drift",
        f"{SPAWN_PROVIDER.provider}-field-range-schema-drift",
    } <= {
        item.code
        for result in (classification, spawn_result)
        for item in result.capability.diagnostics
    }


def test_tactical_provider_declarations_and_catalog_ids_are_strict():
    field = TacticalFieldSpec("type", ("missionType",), CLASSIFICATION_PROVIDER.fields[0].kind)
    with pytest.raises(ValueError, match="reference hops"):
        TacticalProviderSpec("bad", "1", (field,), max_reference_hops=17)

    duplicate = MissionTacticalProvider(CLASSIFICATION_PROVIDER)
    with pytest.raises(ValueError, match="provider ids"):
        extract_mission_tactical_catalog(tactical_fixture(), providers=(duplicate, duplicate))


def test_provider_exception_is_isolated_from_healthy_capabilities(monkeypatch):
    broken_spec = replace(ENGAGEMENT_PROVIDER, provider="mission-broken-v1")
    broken = MissionTacticalProvider(broken_spec)

    def fail_extract(*_args, **_kwargs):
        raise RuntimeError("sensitive implementation detail")

    monkeypatch.setattr(broken, "extract", fail_extract)
    catalog = extract_mission_tactical_catalog(
        tactical_fixture(),
        providers=(MissionTacticalProvider(CLASSIFICATION_PROVIDER), broken),
    )

    healthy = catalog.for_provider(CLASSIFICATION_PROVIDER.provider)
    failed = catalog.for_provider("mission-broken-v1")
    assert healthy.capability.status is CapabilityStatus.AVAILABLE
    assert failed.capability.status is CapabilityStatus.UNAVAILABLE
    assert failed.capability.diagnostics[0].code == "mission-broken-v1-provider-exception"
    assert "sensitive" not in failed.capability.diagnostics[0].message
