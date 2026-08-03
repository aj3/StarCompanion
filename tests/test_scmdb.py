import json

import pytest

from starcompanion.model import BlueprintPool, Contract, ContractSet, Org, Reward, StringKind
from starcompanion.sources import merge, scmdb
from starcompanion.sources.scmdb import ExportKind, ScmdbError, UnrecognisedExportError

# The one shape whose schema is actually known, from the community script in
# StarStrings issue #49.
TRACKING = {
    "blueprints": [
        {"name": "Aves Core", "completed": True},
        {"name": "Aves Legs", "completed": False},
        {"name": "Lumin V SMG", "completed": True},
    ]
}


def contract(id="Foxwell_bombingrun_VE", items=("Aves Core", "Aves Legs")) -> Contract:
    return Contract(
        id=id,
        org=Org(id="foxwell", name="Foxwell"),
        family="bombingrun",
        keys={StringKind.TITLE: [f"{id}_title"]},
        texts={f"{id}_title": "x"},
        base_texts={f"{id}_title": "x"},
        reward=Reward(blueprint_pools=[BlueprintPool(items=list(items))]) if items else Reward(),
    )


def write(tmp_path, data, name="scmdb-tracking.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- format detection --------------------------------------------------------


def test_detects_blueprint_tracking():
    assert scmdb.detect(TRACKING) is ExportKind.BLUEPRINT_TRACKING


def test_detects_contract_pools():
    assert scmdb.detect({"contracts": []}) is ExportKind.CONTRACT_POOLS


def test_detects_resources():
    assert scmdb.detect({"resources": []}) is ExportKind.RESOURCES


def test_unknown_shape_is_reported_not_guessed():
    """Mis-reading an export would put wrong reward data into game text."""
    with pytest.raises(UnrecognisedExportError, match="does not look like"):
        scmdb.parse({"somethingElse": 1})


def test_unrecognised_error_says_what_it_saw():
    with pytest.raises(UnrecognisedExportError) as exc:
        scmdb.parse({"alpha": 1, "beta": 2})
    assert "alpha" in str(exc.value) and "beta" in str(exc.value)


def test_invalid_json_is_reported_with_the_filename(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScmdbError, match="broken.json"):
        scmdb.load(path)


# --- blueprint tracking ------------------------------------------------------


def test_reads_owned_blueprints(tmp_path):
    export = scmdb.load(write(tmp_path, TRACKING))
    assert export.kind is ExportKind.BLUEPRINT_TRACKING
    assert export.owned_blueprints == {"Aves Core", "Lumin V SMG"}
    assert export.all_blueprints == {"Aves Core", "Aves Legs", "Lumin V SMG"}


def test_entries_without_a_name_are_skipped(tmp_path):
    export = scmdb.load(write(tmp_path, {"blueprints": [{"name": "", "completed": True}, "junk"]}))
    assert export.blueprints == []


def test_load_latest_picks_the_newest(tmp_path):
    import os, time

    old = write(tmp_path, {"blueprints": [{"name": "Old"}]}, "scmdb-a.json")
    time.sleep(0.01)
    new = write(tmp_path, {"blueprints": [{"name": "New"}]}, "scmdb-b.json")
    os.utime(old, (1, 1))

    assert scmdb.load_latest(tmp_path).all_blueprints == {"New"}


def test_load_latest_reports_when_nothing_matches(tmp_path):
    with pytest.raises(ScmdbError, match="no scmdb"):
        scmdb.load_latest(tmp_path)


# --- ownership ---------------------------------------------------------------


def test_ownership_marks_only_what_the_export_says(tmp_path):
    contracts = ContractSet(contracts=[contract()])
    result = merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))

    pool = contracts.contracts[0].reward.blueprint_pools[0]
    assert pool.owned == {"Aves Core"}
    assert pool.is_owned("Aves Core")
    assert not pool.is_owned("Aves Legs")
    assert result.items_marked == 1


def test_partially_owned_pool_is_not_marked_complete(tmp_path):
    contracts = ContractSet(contracts=[contract()])
    merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))
    assert not contracts.contracts[0].reward.blueprint_pools[0].fully_owned


def test_fully_owned_pool_is_detected(tmp_path):
    contracts = ContractSet(contracts=[contract(items=("Aves Core", "Lumin V SMG"))])
    result = merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))

    assert contracts.contracts[0].reward.blueprint_pools[0].fully_owned
    assert result.fully_owned_pools == 1


def test_empty_owned_set_means_unknown_not_none_owned():
    """A pool with no ownership data must not read as 'you own nothing'."""
    pool = BlueprintPool(items=["A"])
    assert pool.owned == set()
    assert not pool.fully_owned


def test_owned_items_matching_no_contract_are_reported(tmp_path):
    contracts = ContractSet(contracts=[contract(items=("Aves Core",))])
    result = merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))
    assert result.unmatched == {"Lumin V SMG"}


# --- pools -------------------------------------------------------------------

POOLS = {"contracts": [{"key": "Foxwell_bombingrun_VE", "blueprints": ["Aves Core", "New Item"]}]}


def test_pools_fill_gaps_by_default(tmp_path):
    contracts = ContractSet(contracts=[contract(items=())])
    assert merge.apply_pools(contracts, scmdb.load(write(tmp_path, POOLS))) == 1
    assert contracts.contracts[0].reward.blueprint_pools[0].items == ["Aves Core", "New Item"]


def test_existing_pools_are_left_alone_unless_overwriting(tmp_path):
    contracts = ContractSet(contracts=[contract()])
    export = scmdb.load(write(tmp_path, POOLS))

    assert merge.apply_pools(contracts, export) == 0
    assert contracts.contracts[0].reward.blueprint_pools[0].items == ["Aves Core", "Aves Legs"]

    assert merge.apply_pools(contracts, export, overwrite=True) == 1
    assert "New Item" in contracts.contracts[0].reward.blueprint_pools[0].items


def test_applied_pools_record_their_provenance(tmp_path):
    contracts = ContractSet(contracts=[contract(items=())])
    merge.apply_pools(contracts, scmdb.load(write(tmp_path, POOLS)))
    assert "scmdb" in contracts.contracts[0].reward.blueprint_pools[0].caveat.lower()


def test_pools_match_on_localization_key_too(tmp_path):
    data = {"contracts": [{"key": "Foxwell_bombingrun_VE_title", "blueprints": ["X"]}]}
    contracts = ContractSet(contracts=[contract(items=())])
    assert merge.apply_pools(contracts, scmdb.load(write(tmp_path, data))) == 1


# --- comparison --------------------------------------------------------------


def test_comparison_changes_nothing(tmp_path):
    contracts = ContractSet(contracts=[contract()])
    before = list(contracts.contracts[0].reward.blueprint_pools[0].items)

    merge.compare_pools(contracts, scmdb.load(write(tmp_path, POOLS)))

    assert contracts.contracts[0].reward.blueprint_pools[0].items == before


def test_comparison_reports_both_directions(tmp_path):
    contracts = ContractSet(contracts=[contract()])
    result = merge.compare_pools(contracts, scmdb.load(write(tmp_path, POOLS)))

    assert len(result.disagreements) == 1
    disagreement = result.disagreements[0]
    assert disagreement.only_ours == ["Aves Legs"]
    assert disagreement.only_theirs == ["New Item"]
    assert not result.is_clean


def test_agreement_is_counted(tmp_path):
    data = {"contracts": [{"key": "Foxwell_bombingrun_VE", "blueprints": ["Aves Core", "Aves Legs"]}]}
    result = merge.compare_pools(ContractSet(contracts=[contract()]), scmdb.load(write(tmp_path, data)))

    assert result.agreed == 1
    assert result.is_clean


def test_contracts_only_we_have_are_listed(tmp_path):
    data = {"contracts": [{"key": "Something_Else", "blueprints": ["X"]}]}
    result = merge.compare_pools(ContractSet(contracts=[contract()]), scmdb.load(write(tmp_path, data)))

    assert result.only_in_ours == ["Foxwell_bombingrun_VE"]
    assert result.only_in_theirs == ["Something_Else"]


# --- resources ---------------------------------------------------------------


def test_resources_parse(tmp_path):
    data = {"resources": [{"location": "Yela", "resources": ["Quantainium", "Bexalite"]}]}
    export = scmdb.load(write(tmp_path, data))

    assert export.kind is ExportKind.RESOURCES
    assert export.resources[0].location == "Yela"
    assert export.resources[0].resources == ["Quantainium", "Bexalite"]


# --- rendering ---------------------------------------------------------------


def test_owned_items_are_marked_in_output(tmp_path):
    from starcompanion.config import Profile

    contracts = ContractSet(contracts=[contract()])
    merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))

    key = contracts.contracts[0].key(StringKind.TITLE)
    contracts.contracts[0].keys[StringKind.DESC] = ["d"]
    contracts.contracts[0].base_texts["d"] = "Body."

    value = Profile().build_renderer().render_key(contracts.contracts[0], "d")
    assert "- Aves Core [Owned]" in value
    assert "- Aves Legs" in value and "- Aves Legs [Owned]" not in value


def test_ownership_marking_can_be_switched_off(tmp_path):
    from starcompanion.config import Profile

    contracts = ContractSet(contracts=[contract()])
    merge.apply_ownership(contracts, scmdb.load(write(tmp_path, TRACKING)))
    contracts.contracts[0].keys[StringKind.DESC] = ["d"]
    contracts.contracts[0].base_texts["d"] = "Body."

    profile = Profile.model_validate({"fields": {"owned": False}})
    assert "[Owned]" not in profile.build_renderer().render_key(contracts.contracts[0], "d")


# --- no network --------------------------------------------------------------


def test_module_makes_no_network_calls():
    """SCMDB's robots.txt excludes automated access to its data endpoints, so
    this reads files the user exported and never fetches."""
    import inspect

    source = inspect.getsource(scmdb)
    for forbidden in ("requests", "urllib", "http.client", "socket", "aiohttp", "httpx"):
        assert forbidden not in source
