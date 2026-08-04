from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from starcompanion.extract.datacore import Record, StructDefinition
from starcompanion.extract.dataforge import (
    CapabilityStatus,
    Confidence,
    DataForgeIndex,
    Diagnostic,
    DiagnosticCategory,
    Severity,
    ScalarKind,
    FieldValue,
    convert_scalar,
    extract_mission_facts,
    normalize_record_path,
)
from test_datacore import REAL_DCB


GUID = {
    "contract": "00000000-0000-0000-0000-000000000001",
    "rep": "00000000-0000-0000-0000-000000000002",
    "pool": "00000000-0000-0000-0000-000000000003",
    "blueprint": "00000000-0000-0000-0000-000000000004",
    "entity": "00000000-0000-0000-0000-000000000005",
    "item": "00000000-0000-0000-0000-000000000006",
    "a": "00000000-0000-0000-0000-000000000007",
    "b": "00000000-0000-0000-0000-000000000008",
    "missing": "00000000-0000-0000-0000-000000000099",
}


def test_optional_graph_gaps_do_not_masquerade_as_schema_drift():
    optional = Diagnostic("missing-reference-target", "optional")
    untitled = Diagnostic("mission-title-missing", "no title")
    drift = Diagnostic("reputation-outcome-schema-drift", "changed shape")

    assert optional.category is DiagnosticCategory.OPTIONAL_REFERENCE
    assert not optional.degrades_capability
    assert untitled.category is DiagnosticCategory.DATA_GAP
    assert not untitled.degrades_capability
    assert drift.category is DiagnosticCategory.SCHEMA_DRIFT
    assert drift.degrades_capability


def test_scope_field_filters_reuse_the_one_flattened_walk():
    from starcompanion.extract import dataforge

    class NoRetraversal:
        def iter_value_fields(self, *_args, **_kwargs):
            raise AssertionError("scope was traversed again")

    scope = dataforge._RecordScope(
        "$",
        {},
        (FieldValue("$.title", "T"), FieldValue("$.nested.chance", 0.5)),
    )

    assert list(dataforge._scope_fields(NoRetraversal(), None, scope, "chance")) == [
        FieldValue("$.nested.chance", 0.5)
    ]


@dataclass
class SyntheticDataCore:
    """Semantic fixture: invented records behind the real DataCore boundary."""

    entries: list[tuple[str, str, str, dict[str, Any]]]
    version: int = 8

    def __post_init__(self) -> None:
        self.structs = [StructDefinition("Synthetic", -1, 0, 0, 0)]
        self.records = [
            Record(name, path, None, 0, guid, position, 0)
            for position, (name, path, guid, _payload) in enumerate(self.entries)
        ]
        self.reads = 0

    def read_record(self, record: Record, *, max_depth: int = 0) -> dict[str, Any]:
        assert max_depth == 0
        self.reads += 1
        return self.entries[record.instance_index][3]


def full_fixture() -> SyntheticDataCore:
    return SyntheticDataCore(
        [
            (
                "Contract.Test",
                "Data/Libs/Foundry/Records/Contracts/ContractGenerator/Test.xml",
                GUID["contract"],
                {
                    "parameters": [
                        {
                            "ContractStringParam": {
                                "param": "Title",
                                "value": "@Test_Title",
                            }
                        },
                        {
                            "ContractStringParam": {
                                "param": "Description",
                                "value": "@Test_Desc",
                            }
                        },
                    ],
                    "missionResultReputationRewards": {
                        "SReputationAmountListParams": {
                            "amounts": [
                                {
                                    "$type": "SReputationAmountParams",
                                    "reputationScope": GUID["a"],
                                    "reward": GUID["rep"],
                                },
                                {
                                    "$type": "SReputationAmountParams",
                                    "reputationScope": GUID["b"],
                                    "reward": GUID["missing"],
                                },
                            ]
                        }
                    },
                    "BlueprintRewards": [
                        {"blueprintPool": GUID["pool"], "chance": 0.25}
                    ],
                    "itemRewards": {
                        "ContractResult_Item": {"entityClass": GUID["item"]}
                    },
                },
            ),
            (
                "Rep.1000",
                "libs/foundry/records/reputation/rewards/missionrewards_reputation/1000.xml",
                GUID["rep"],
                {"reputationAmount": "1000"},
            ),
            (
                "Pool.Test",
                "libs/foundry/records/crafting/blueprintrewards/bp_rewards_test.xml",
                GUID["pool"],
                {"rewards": [{"BlueprintReward": {"blueprintRecord": GUID["blueprint"]}}]},
            ),
            (
                "Blueprint.Test",
                "libs/foundry/records/crafting/blueprints/crafting/bp_craft_laser.xml",
                GUID["blueprint"],
                {"process": {"$type": "CraftingProcess_Creation", "entityClass": GUID["entity"]}},
            ),
            (
                "Entity.Laser",
                "libs/foundry/records/entities/scitem/weapons/laser.xml",
                GUID["entity"],
                {"displayName": "@Laser_Name"},
            ),
            (
                "Entity.Reward",
                "libs/foundry/records/entities/scitem/reward.xml",
                GUID["item"],
                {"displayName": "@Reward_Item_Name"},
            ),
        ]
    )


def diagnostic_codes(result) -> set[str]:
    return {diagnostic.code for diagnostic in result.capability.diagnostics}


def test_paths_are_normalized_for_case_slashes_and_dot_segments():
    assert normalize_record_path(r"Data\Libs\.\Foundry\Records\..\Mission\X.XML") == (
        "data/libs/foundry/mission/x.xml"
    )


def test_typed_scalar_conversion_rejects_lossy_values():
    assert convert_scalar(FieldValue("$.amount", "1000.0"), ScalarKind.INTEGER).value == 1000
    assert convert_scalar(FieldValue("$.amount", "1000.5"), ScalarKind.INTEGER).value is None
    assert convert_scalar(FieldValue("$.enabled", "false"), ScalarKind.BOOLEAN).value is False
    assert convert_scalar(FieldValue("$.key", "@Mission_Title"), ScalarKind.LOCALE_KEY).value == "Mission_Title"
    assert convert_scalar(FieldValue("$.ref", GUID["rep"]), ScalarKind.UUID).value == GUID["rep"]


def test_index_supports_uuid_path_filename_and_struct_lookups():
    index = DataForgeIndex(full_fixture())

    assert index.by_guid[GUID["contract"]][0].record.name == "Contract.Test"
    assert index.by_filename["test.xml"][0].record.name == "Contract.Test"
    assert index.records_of("synthetic")
    assert index.records_under("records/contracts/contractgenerator")


def test_payload_cache_is_bounded_and_decodes_lazily():
    core = full_fixture()
    index = DataForgeIndex(core, payload_cache_size=2)
    assert core.reads == 0

    for node in index.nodes[:3]:
        index.payload(node)

    assert core.reads == 3
    assert index.cached_payload_count == 2


def test_graph_resolves_references_and_reports_cycles_with_breadcrumbs():
    core = SyntheticDataCore(
        [
            ("A", "records/a.xml", GUID["a"], {"next": GUID["b"]}),
            ("B", "records/b.xml", GUID["b"], {"next": GUID["a"]}),
        ]
    )
    index = DataForgeIndex(core)
    nodes, diagnostics = index.walk_references(index.by_guid[GUID["a"]][0])

    assert [node.id for node in nodes] == [GUID["a"], GUID["b"]]
    cycle = next(d for d in diagnostics if d.code == "reference-cycle")
    assert cycle.breadcrumbs
    assert "$.next" in " ".join(cycle.breadcrumbs)


def test_nested_datacore_instance_pointers_are_lazy_bounded_and_cycle_safe():
    class PointerCore(SyntheticDataCore):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.pointed = {
                (1, 10): {"title": "@Pointed_Title", "next": {"$struct": 1, "$instance": 11}},
                (1, 11): {"description": "@Pointed_Desc", "back": {"$struct": 1, "$instance": 10}},
            }

        def read_instance(self, struct_index: int, instance_index: int, *, max_depth: int = 0):
            self.reads += 1
            return self.pointed.get((struct_index, instance_index), {})

    core = PointerCore([("Root", "records/root.xml", GUID["a"], {"child": {"$struct": 1, "$instance": 10}})])
    index = DataForgeIndex(core, payload_cache_size=2)

    assert [field.value for field in index.iter_fields(index.nodes[0], key="title")] == ["@Pointed_Title"]
    assert [field.value for field in index.iter_fields(index.nodes[0], key="description")] == ["@Pointed_Desc"]
    assert index.cached_payload_count <= 2
    assert any(d.code == "pointer-cycle" for d in index.diagnostics)


def test_missing_and_duplicate_reference_targets_are_never_guessed():
    core = SyntheticDataCore(
        [
            ("A", "records/a.xml", GUID["a"], {"missing": GUID["missing"], "dup": GUID["b"]}),
            ("B1", "records/b1.xml", GUID["b"], {}),
            ("B2", "records/b2.xml", GUID["b"], {}),
        ]
    )
    index = DataForgeIndex(core)

    assert index.resolve(GUID["missing"]).target is None
    duplicate = index.resolve(GUID["b"])
    assert duplicate.target is None
    assert duplicate.diagnostics[0].code == "ambiguous-reference"
    assert any(d.code == "duplicate-guid" for d in index.diagnostics)


def test_extracts_reputation_blueprint_pool_items_and_provenance():
    result = extract_mission_facts(full_fixture())
    fact = result.facts[0]

    assert result.capability.status is CapabilityStatus.AVAILABLE
    assert fact.title_keys == ("Test_Title",)
    assert fact.description_keys == ("Test_Desc",)
    assert fact.reputation == (1000,)
    assert fact.blueprint_pools[0].items == ("Laser_Name",)
    assert fact.blueprint_pools[0].item_ids == (GUID["entity"],)
    assert fact.blueprint_pools[0].item_categories == ("weapons",)
    assert fact.blueprint_pools[0].chance == 0.25
    assert fact.item_rewards == ("Reward_Item_Name",)
    assert fact.confidence is Confidence.HIGH

    paths = {e.field_path for e in fact.evidence}
    assert any("reward" in path for path in paths)
    assert any("reputationAmount" in path for path in paths)
    assert any("blueprintPool" in path for path in paths)
    assert any("blueprintRecord" in path for path in paths)
    assert any("chance" in path for path in paths)
    assert any("displayName" in path for path in paths)
    assert set(fact.source_record_ids) >= {
        GUID["contract"], GUID["rep"], GUID["pool"], GUID["blueprint"], GUID["entity"], GUID["item"]
    }


def test_blueprint_name_prefers_item_localization_over_nested_interaction_labels():
    core = full_fixture()
    core.entries[4][3].clear()
    core.entries[4][3].update({
        "Components": [
            {
                "Interactable": {
                    "SharedInteractions": [
                        {"DisplayName": "@interaction_carry", "Name": "Carry"}
                    ]
                }
            },
            {"AttachDef": {"Localization": {"Name": "@Laser_Name"}}},
        ]
    })

    fact = extract_mission_facts(core).facts[0]

    assert fact.blueprint_pools[0].items == ("Laser_Name",)


def test_placeholder_reward_names_fall_back_to_record_filenames():
    core = full_fixture()
    core.entries[4][3]["displayName"] = "@LOC_UNINITIALIZED"
    core.entries[5][3]["displayName"] = "@LOC_PLACEHOLDER"

    result = extract_mission_facts(core)
    fact = result.facts[0]

    assert fact.blueprint_pools[0].items == ("laser",)
    assert fact.item_rewards == ("reward",)
    assert "blueprint-name-fallback" in diagnostic_codes(result)
    assert "item-name-fallback" in diagnostic_codes(result)


def test_only_first_success_outcome_and_primary_reputation_scope_are_counted():
    core = full_fixture()
    contract = core.entries[0][3]
    contract["missionResultReputationRewards"]["SReputationAmountListParams"] = [
        {
            "amounts": [
                {"$type": "SReputationAmountParams", "reputationScope": "primary", "reward": GUID["rep"]},
                {"$type": "SReputationAmountParams", "reputationScope": "primary", "reward": GUID["rep"]},
                {"$type": "SReputationAmountParams", "reputationScope": "bonus", "reward": GUID["rep"]},
            ]
        },
        {
            "amounts": [
                {"$type": "SReputationAmountParams", "reputationScope": "primary", "reward": GUID["rep"]}
            ]
        },
    ]

    assert extract_mission_facts(core).facts[0].reputation == (2000,)


def test_broker_reputation_joins_to_generator_by_localization_key_with_evidence():
    class MixedCore(SyntheticDataCore):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.structs.append(StructDefinition("MissionBrokerEntry", -1, 0, 0, 0))
            self.records[-1] = replace(self.records[-1], struct_index=1)

    contract = (
        "Contract.Shared",
        "records/contracts/contractgenerator/shared.xml",
        GUID["contract"],
        {"title": "@Shared_Title"},
    )
    reputation = (
        "Rep.1000",
        "records/reputation/rewards/missionrewards_reputation/1000.xml",
        GUID["rep"],
        {"reputationAmount": 1000},
    )
    broker = (
        "MissionBrokerEntry.Shared",
        "records/missionbroker/shared.xml",
        GUID["b"],
        {
            "title": "@Shared_Title",
            "missionResultReputationRewards": {
                "SReputationAmountListParams": {
                    "reputationAmounts": [
                        {
                            "$type": "SReputationAmountParams",
                            "reputationScope": GUID["a"],
                            "reward": GUID["rep"],
                        }
                    ]
                }
            },
        },
    )

    fact = extract_mission_facts(MixedCore([contract, reputation, broker])).facts[0]

    assert fact.reputation == (1000,)
    assert GUID["b"] in fact.source_record_ids
    assert any(e.record_id == GUID["b"] and e.field_path == "$.title" for e in fact.evidence)


def test_nested_career_and_list_contracts_emit_separate_facts():
    entries = full_fixture().entries
    root_contract = entries[0][3]
    nested = {
        "ContractGeneratorHandler_Career": {
            "CareerContract": [
                {"title": "@Career_A", "BlueprintRewards": root_contract["BlueprintRewards"]},
                {"title": "@Career_B"},
            ]
        },
        "ContractGeneratorHandler_List": {
            "Contract": {"title": "@List_A"}
        },
    }
    core = SyntheticDataCore([(entries[0][0], entries[0][1], entries[0][2], nested), *entries[1:]])

    result = extract_mission_facts(core)

    assert [fact.title_keys for fact in result.facts] == [("Career_A",), ("Career_B",), ("List_A",)]
    assert len({fact.mission_id for fact in result.facts}) == 3
    assert result.facts[0].blueprint_pools
    assert not result.facts[1].blueprint_pools


def test_optional_provider_absence_degrades_but_keeps_contract_facts():
    contract = full_fixture().entries[0]
    result = extract_mission_facts(SyntheticDataCore([contract]))

    assert len(result.facts) == 1
    assert result.capability.status is CapabilityStatus.DEGRADED
    assert {"reputation-provider-missing", "blueprint-provider-missing"} <= diagnostic_codes(result)


def test_missing_contract_provider_isolated_as_unavailable():
    result = extract_mission_facts(SyntheticDataCore([]))

    assert result.facts == ()
    assert result.capability.status is CapabilityStatus.UNAVAILABLE
    assert "contract-generator-provider-missing" in diagnostic_codes(result)


def test_structural_contract_drift_fails_only_the_provider():
    core = SyntheticDataCore(
        [
            (
                "Future.Contract",
                "records/contracts/contractgenerator/future.xml",
                GUID["contract"],
                {"unknownFutureShape": {"x": 1}},
            )
        ]
    )
    result = extract_mission_facts(core)

    assert result.capability.status is CapabilityStatus.UNAVAILABLE
    assert "contract-generator-schema-drift" in diagnostic_codes(result)


def test_corrupt_amount_and_missing_blueprint_target_are_diagnostics_not_values():
    core = full_fixture()
    core.entries[1][3]["reputationAmount"] = "not-a-number"
    core.entries[0][3]["BlueprintRewards"][0]["blueprintPool"] = GUID["missing"]
    result = extract_mission_facts(core)

    assert result.facts[0].reputation == ()
    assert result.facts[0].blueprint_pools == ()
    assert {"invalid-reputation-amount", "missing-reference-target"} <= diagnostic_codes(result)
    assert result.capability.status is CapabilityStatus.DEGRADED


def test_duplicate_reputation_guid_is_ambiguous_and_not_counted():
    core = SyntheticDataCore(
        [
            *full_fixture().entries,
            (
                "Rep.Duplicate",
                "records/reputation/rewards/missionrewards_reputation/duplicate.xml",
                GUID["rep"],
                {"reputationAmount": 9000},
            ),
        ]
    )
    result = extract_mission_facts(core)

    assert result.facts[0].reputation == ()
    assert {"duplicate-guid", "ambiguous-reference"} <= diagnostic_codes(result)
    assert any(d.severity is Severity.ERROR for d in result.capability.diagnostics)


# --- against a locally extracted supported build ----------------------------

real = pytest.mark.skipif(
    REAL_DCB is None,
    reason="no Game2.dcb; set STARCOMPANION_DCB or place one in tests/samples/",
)


@pytest.fixture(scope="module")
def real_result():
    if REAL_DCB is None:
        pytest.skip("no Game2.dcb available")
    from starcompanion.extract import datacore

    return extract_mission_facts(datacore.load(REAL_DCB))


@real
def test_real_build_emits_mission_and_reward_evidence(real_result):
    assert real_result.capability.status is not CapabilityStatus.UNAVAILABLE
    assert len(real_result.facts) > 1_000
    assert sum(bool(fact.title_keys) for fact in real_result.facts) > 1_000
    assert any(fact.reputation for fact in real_result.facts)
    assert any(fact.blueprint_pools for fact in real_result.facts)
    assert any(fact.item_rewards for fact in real_result.facts)


@real
def test_real_displayable_rewards_retain_field_evidence(real_result):
    for fact in real_result.facts:
        paths = {e.field_path for e in fact.evidence}
        if fact.reputation:
            assert any("reputationAmount" in path for path in paths)
        if fact.blueprint_pools:
            assert any("blueprintPool" in path for path in paths)
            for pool in fact.blueprint_pools:
                assert pool.evidence
                assert all(pool.items)
        if fact.item_rewards:
            assert any("entityClass" in path for path in paths)
