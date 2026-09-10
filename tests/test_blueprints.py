from starcompanion.blueprints import (
    BlueprintQuery,
    OwnershipFilter,
    apply_ownership_snapshot,
    build_catalog,
    normalize_blueprint_name,
    query_blueprints,
)
from starcompanion.model import BlueprintPool, Contract, ContractSet, Evidence, Org, Reward
from starcompanion.ownership import Acquisition, OwnershipRecord, OwnershipState


UUID = "11111111-1111-1111-1111-111111111111"


def contracts():
    org = Org("foxwell", "Foxwell")
    evidence = Evidence("local", UUID, "records/crafting/blueprints/crafting/a.xml", "$.displayName", "Aves Core")
    contract = Contract(
        "Foxwell_Salvage_E",
        org,
        "Salvage",
        reward=Reward(
            blueprint_pools=[
                BlueprintPool(
                    items=["Aves\u00a0Core", "Legacy Item"],
                    item_ids={"Aves\u00a0Core": UUID},
                    item_categories={"Aves\u00a0Core": "armor"},
                    item_types={"Aves\u00a0Core": "helmet"},
                    item_classes={"Aves\u00a0Core": "combat"},
                    item_sizes={"Aves\u00a0Core": "medium"},
                    item_grades={"Aves\u00a0Core": "a"},
                    chance=0.25,
                )
            ]
        ),
        evidence=[evidence],
    )
    return ContractSet([contract], {org.id: org})


def test_catalog_uses_cig_identity_and_explicit_name_fallback():
    catalog = build_catalog(contracts())
    by_name = {entry.name: entry for entry in catalog.entries}
    assert by_name["Aves\u00a0Core"].blueprint_id == f"cig:{UUID}"
    assert not by_name["Aves\u00a0Core"].identity_fallback
    assert by_name["Aves\u00a0Core"].category == "armor"
    assert by_name["Legacy Item"].blueprint_id.startswith("name-sha256:")
    assert by_name["Legacy Item"].identity_fallback
    assert len(by_name["Aves\u00a0Core"].evidence) == 1


def test_catalog_is_stable_across_contract_order_and_name_normalizes_tags():
    first = build_catalog(contracts())
    changed = contracts()
    changed.contracts.reverse()
    second = build_catalog(changed)
    assert [item.blueprint_id for item in first.entries] == [item.blueprint_id for item in second.entries]
    assert normalize_blueprint_name("[AR] Aves\u00a0 Core") == "aves core"
    assert normalize_blueprint_name("[SCMDB][OWNED] Aves Core [COMPLETE]") == "aves core"
    assert first.resolve_name("[AR] Aves Core") == f"cig:{UUID}"


def test_name_only_source_folds_into_one_exact_cig_identity():
    source = contracts()
    source.contracts.append(
        Contract(
            "Community_Mission",
            source.orgs["foxwell"],
            "Community",
            reward=Reward(blueprint_pools=[BlueprintPool(items=["Aves Core"])]),
        )
    )

    catalog = build_catalog(source)

    assert len([entry for entry in catalog.entries if entry.normalized_name == "aves core"]) == 1
    entry = catalog.by_id[f"cig:{UUID}"]
    assert {source.family for source in entry.reward_sources} == {"Salvage", "Community"}


def test_queries_join_ownership_and_filter_every_backend_dimension():
    catalog = build_catalog(contracts())
    blueprint_id = f"cig:{UUID}"
    state = OwnershipState(
        "LIVE",
        {
            blueprint_id: OwnershipRecord(
                blueprint_id,
                "Aves Core",
                [Acquisition("a", "log", "2026-04-01T00:00:00Z", "Game.log", "f")],
            )
        },
    )
    query = BlueprintQuery(
        search="aves",
        ownership=OwnershipFilter.OWNED,
        reward_source="foxwell",
        category="armor",
        acquisition_source="log",
        mission="salvage_e",
        item_type="helmet",
        item_class="combat",
        size="medium",
        grade="a",
    )
    rows = query_blueprints(catalog, state, query)
    assert len(rows) == 1
    assert rows[0].owned
    assert rows[0].entry.reward_sources.pop().contract_id == "Foxwell_Salvage_E"
    assert len(query_blueprints(catalog, state, BlueprintQuery(ownership=OwnershipFilter.UNOWNED))) == 1


def test_metadata_from_later_evidence_fills_unknown_catalog_fields():
    source = contracts()
    pool = source.contracts[0].reward.blueprint_pools[0]
    pool.item_types.clear()
    source.contracts.append(
        Contract(
            "Later_Mission",
            source.orgs["foxwell"],
            "Later",
            reward=Reward(
                blueprint_pools=[
                    BlueprintPool(
                        items=["Aves Core"],
                        item_ids={"Aves Core": UUID},
                        item_types={"Aves Core": "helmet"},
                    )
                ]
            ),
        )
    )
    assert build_catalog(source).by_id[f"cig:{UUID}"].item_type == "helmet"


def test_render_ownership_join_marks_only_the_copy():
    source = contracts()
    blueprint_id = f"cig:{UUID}"
    state = OwnershipState(
        "LIVE",
        {blueprint_id: OwnershipRecord(blueprint_id, "Aves Core")},
    )

    joined = apply_ownership_snapshot(source, state)

    assert joined.contracts[0].reward.blueprint_pools[0].owned == {"Aves\u00a0Core"}
    assert not source.contracts[0].reward.blueprint_pools[0].owned
