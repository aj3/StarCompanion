from pathlib import Path

import pytest

from starcompanion.ini import BOM, LocalizationFile
from starcompanion.model import Difficulty, GateKind, StringKind
from starcompanion.sources import contracts_ini
from starcompanion.sources.contracts_ini import _split_key

SAMPLES = Path(__file__).parent / "samples"


def parse(text: str):
    return contracts_ini.parse(LocalizationFile.loads(BOM + text))


# --- key splitting -----------------------------------------------------------


@pytest.mark.parametrize(
    "key,base,kind",
    [
        ("Foxwell_bombingrun_VE_title_001", "Foxwell_bombingrun_VE", StringKind.TITLE),
        ("Foxwell_bombingrun_VE_desc_001", "Foxwell_bombingrun_VE", StringKind.DESC),
        # kind segment mid-key, not at the end
        ("Foxwell_ShipWave_Title_VE_001", "Foxwell_ShipWave_VE", StringKind.TITLE),
        ("Foxwell_ShipWave_Desc_VE_001", "Foxwell_ShipWave_VE", StringKind.DESC),
        ("Covalex_HaulCargo_AToB_title", "Covalex_HaulCargo_AToB", StringKind.TITLE),
        # CIG's own `_Dec` typo
        ("TheCollector_Ships_Golem_Dec", "TheCollector_Ships_Golem", StringKind.DESC),
        # `_name` is functionally a title
        ("RAIN_intro_name_01", "RAIN_intro", StringKind.TITLE),
        ("Unsuffixed_Key", "Unsuffixed_Key", None),
    ],
)
def test_split_key(key, base, kind):
    assert _split_key(key) == (base, kind)


def test_title_and_desc_pair_onto_one_contract():
    result = parse(
        "Foxwell_ShipWave_Title_VE_001=Patrol <EM4>[100 Rep]</EM4>\n"
        "Foxwell_ShipWave_Desc_VE_001=Body text.\n"
    )
    assert len(result.contracts) == 1
    c = result.contracts[0]
    assert c.key(StringKind.TITLE) == "Foxwell_ShipWave_Title_VE_001"
    assert c.key(StringKind.DESC) == "Foxwell_ShipWave_Desc_VE_001"
    assert c.difficulty is Difficulty.VERY_EASY
    assert not c.has_variants


def test_alternate_phrasings_group_but_keep_every_key():
    result = parse(
        "Org_x_desc_001=First phrasing. <EM4>Reputation Awarded:</EM4> 50\n"
        "Org_x_desc_002=Second phrasing. <EM4>Reputation Awarded:</EM4> 50\n"
        "Org_x_desc_003=Third phrasing. <EM4>Reputation Awarded:</EM4> 50\n"
    )
    assert len(result.contracts) == 1
    c = result.contracts[0]
    assert c.has_variants
    assert c.keys_of(StringKind.DESC) == ["Org_x_desc_001", "Org_x_desc_002", "Org_x_desc_003"]
    assert c.text("Org_x_desc_002").startswith("Second")
    assert c.desc.startswith("First")


def test_duplicate_keys_are_not_double_counted():
    result = parse("Org_x_title=A <EM4>[10 Rep]</EM4>\nOrg_x_title=A <EM4>[10 Rep]</EM4>\n")
    assert result.contracts[0].keys_of(StringKind.TITLE) == ["Org_x_title"]


# --- org normalisation -------------------------------------------------------


def test_inconsistent_org_casing_collapses_to_one_org():
    result = parse(
        "headhunters_a_title=A\nHeadhunters_b_title=B\nHeadHunters_c_title=C\n"
    )
    assert len(result.orgs) == 1
    assert len(result.by_org("headhunters")) == 3


def test_org_display_name_uses_override():
    result = parse("cfp_a_title=A\n")
    assert result.orgs["cfp"].name == "Citizens for Prosperity"


def test_org_display_name_falls_back_to_modal_casing():
    result = parse("Shubin_a_title=A\nShubin_b_title=B\nshubin_c_title=C\n")
    assert result.orgs["shubin"].name == "Shubin"


# --- rewards -----------------------------------------------------------------


def test_reputation_from_description():
    result = parse("Org_x_desc=Text <EM4>Reputation Awarded:</EM4> 250\n")
    assert result.contracts[0].reward.reputation == [250]


def test_reputation_range_by_difficulty():
    result = parse(
        "Org_x_desc=T <EM4>Reputation Awarded (by difficulty):</EM4> 300 / 16,000\n"
    )
    assert result.contracts[0].reward.reputation == [300, 16000]


def test_negative_reputation_is_preserved():
    result = parse(
        "Org_x_desc=T <EM4>Reputation Awarded:</EM4> -190,500 / 400 / 2,400\n"
    )
    assert result.contracts[0].reward.reputation == [-190500, 400, 2400]


def test_reputation_falls_back_to_title_bracket():
    result = parse("Org_x_title=Do a thing <EM4>[150 Rep]</EM4>\n")
    assert result.contracts[0].reward.reputation == [150]


def test_scenario_points_with_split_flag():
    result = parse("Org_x_desc=T <EM4>Scenario Progress Points 120,000 (Split)</EM4>\n")
    points = result.contracts[0].reward.scenario_points
    assert points[0].amount == 120000 and points[0].split


def test_scrip_detected():
    result = parse("Org_x_desc=Pays ~mission(ScripAmount) MG Scrip today\n")
    assert result.contracts[0].reward.scrip


def test_reward_absent_is_recorded_as_unparsed():
    result = parse("Org_x_desc=Just prose, no rewards stated.\n")
    assert result.contracts[0].reward.is_empty
    assert result.unparsed == [("Org_x", "no reward data found")]


# --- blueprint pools ---------------------------------------------------------


def test_pool_items_collected():
    result = parse(
        r"Org_x_desc=T\n<EM4>Potential Blueprints</EM4>\n- Aves Core\n- Aves Legs\n"
    )
    pool = result.contracts[0].reward.blueprint_pools[0]
    assert pool.items == ["Aves Core", "Aves Legs"]
    assert not pool.is_gated


def test_rank_gate_from_awarded_from_phrasing():
    result = parse(
        r"Org_x_desc=T\n<EM4>Potential Blueprints</EM4>\n"
        r"<EM4>Awarded from Jr. Contractor level variants</EM4>\n- Item\n"
    )
    gate = result.contracts[0].reward.blueprint_pools[0].rank_gate
    assert gate.kind is GateKind.RANK and gate.label == "Jr. Contractor"


def test_only_suffix_is_normalised_to_match_awarded_from():
    result = parse(
        r"A_x_desc=T\n<EM4>Potential Blueprints (Contractor only)</EM4>\n- I\n"
        "\n"
        r"A_y_desc=T\n<EM4>Potential Blueprints</EM4>\n"
        r"<EM4>Awarded from Contractor level variants</EM4>\n- I\n"
    )
    labels = {
        p.rank_gate.label
        for c in result.contracts
        for p in c.reward.blueprint_pools
    }
    assert labels == {"Contractor"}


def test_faction_and_rank_gates_stack_on_one_pool():
    result = parse(
        r"Org_x_desc=T\n<EM4>Potential Blueprints (BitZeros Only)</EM4>\n"
        r"<EM4>Awarded from Neutral level variants</EM4>\n- Aves Core\n"
    )
    pool = result.contracts[0].reward.blueprint_pools[0]
    assert pool.gate_of(GateKind.FACTION).label == "BitZeros"
    assert pool.gate_of(GateKind.RANK).label == "Neutral"


def test_region_gate_classified():
    result = parse(
        r"Org_x_desc=T\n<EM4>Potential Blueprints (Nyx Only)</EM4>\n- Item\n"
    )
    assert result.contracts[0].reward.blueprint_pools[0].gate_of(GateKind.REGION)


def test_empty_pools_are_dropped():
    result = parse(r"Org_x_desc=T\n<EM4>Potential Blueprints</EM4>\n")
    assert result.contracts[0].reward.blueprint_pools == []


# --- against real data -------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    if not (SAMPLES / "contracts.ini").exists():
        pytest.skip("real sample not present")
    return contracts_ini.load(SAMPLES / "contracts.ini")


def test_real_import_covers_every_key(real):
    """No key may be dropped -- a lost key is a contract with no override."""
    source = LocalizationFile.load(SAMPLES / "contracts.ini")
    expected = {e.key for e in source.entries()}
    imported = {key for c in real.contracts for key in c.all_keys()}
    assert imported == expected


def test_real_texts_match_the_source_verbatim(real):
    """Compared via `get()`, which is first-occurrence-wins -- the source
    genuinely defines a few keys twice with differing values, and the importer
    resolves them the same way."""
    source = LocalizationFile.load(SAMPLES / "contracts.ini")
    for key in source.keys():
        contract = real.by_key(key)
        assert contract is not None, key
        assert contract.text(key) == source.get(key)


def test_real_import_leaves_almost_nothing_unparsed(real):
    # The known residue is dynamic-reward hauling, which states no fixed reward.
    assert len(real.unparsed) <= 5


def test_real_foxwell_shape(real):
    foxwell = real.by_org("foxwell")
    assert len(foxwell) == 57
    assert sum(len(c.all_keys()) for c in foxwell) == 109

    ranked = {c.rank for c in foxwell if c.rank}
    assert ranked == {1, 2, 3, 4, 5, 6}


def test_real_rank_ladders_are_ordered_not_alphabetical(real):
    ladder = real.orgs["headhunters"].rank_ladder
    assert ladder.index("Jr. Contractor") < ladder.index("Sr. Contractor")
    assert ladder.index("Neutral") == 0
    assert len(ladder) == len(set(ladder))


def test_real_data_exercises_every_gate_kind(real):
    kinds = {
        g.kind
        for c in real.contracts
        for p in c.reward.blueprint_pools
        for g in p.gates
    }
    assert kinds == set(GateKind)


def test_real_regional_variants_carry_locations(real):
    regional = [
        p
        for c in real.contracts
        for p in c.reward.blueprint_pools
        if p.example_locations
    ]
    assert len(regional) > 100
