from starcompanion.model import (
    BlueprintPool,
    Contract,
    ContractSet,
    Difficulty,
    Gate,
    GateKind,
    Org,
    Reward,
    ScenarioPoints,
    StringKind,
)


def test_difficulty_codes_map_to_ranks_and_colours():
    assert Difficulty.from_code("VE").rank == 1
    assert Difficulty.from_code("S").rank == 6
    assert Difficulty.from_code("VE").colour == "Yellow"
    assert Difficulty.from_code("M").colour == "Orange"
    assert Difficulty.from_code("VH").colour == "Red"


def test_difficulty_lookup_is_case_insensitive_and_safe():
    assert Difficulty.from_code("ve") is Difficulty.VERY_EASY
    assert Difficulty.from_code("nonsense") is None


def test_difficulty_orders_by_rank():
    assert Difficulty.VERY_EASY < Difficulty.SUPERIOR
    ordered = sorted(Difficulty, key=lambda d: d.rank)
    assert [d.code for d in ordered] == ["VE", "E", "M", "H", "VH", "S"]


def test_empty_reward_is_distinguishable_from_zero_rep():
    assert Reward().is_empty
    assert not Reward(reputation=[0]).is_empty


def test_reputation_display_formats_and_handles_negatives():
    assert Reward(reputation=[100]).reputation_display == "100"
    assert Reward(reputation=[300, 16000]).reputation_display == "300 / 16,000"
    assert Reward(reputation=[-190500, 400]).reputation_display == "-190,500 / 400"
    assert Reward().reputation_display is None


def test_pool_reports_multiple_gates():
    pool = BlueprintPool(
        items=["Aves Core"],
        gates=[Gate(GateKind.FACTION, "BitZeros"), Gate(GateKind.RANK, "Neutral")],
    )
    assert pool.is_gated
    assert pool.rank_gate.label == "Neutral"
    assert pool.gate_of(GateKind.FACTION).label == "BitZeros"
    assert pool.gate_of(GateKind.REGION) is None


def test_ungated_pool_is_not_gated():
    assert not BlueprintPool(items=["x"]).is_gated


def test_awards_blueprints_requires_items():
    assert not Reward(blueprint_pools=[BlueprintPool()]).awards_blueprints
    assert Reward(blueprint_pools=[BlueprintPool(items=["x"])]).awards_blueprints


def test_scenario_points_carry_split_flag():
    assert ScenarioPoints(120000, split=True).split
    assert not ScenarioPoints(4000).split


def test_contract_exposes_rank_and_colour():
    org = Org(id="foxwell", name="Foxwell")
    c = Contract(id="x", org=org, family="ShipAmbush", difficulty=Difficulty.VERY_HARD)
    assert c.rank == 5
    assert c.colour == "Red"


def test_contract_without_difficulty_has_no_rank():
    c = Contract(id="x", org=Org(id="o", name="O"), family="f")
    assert c.rank is None and c.colour is None


def test_org_rank_index():
    org = Org(id="o", name="O", rank_ladder=["Neutral", "Contractor"])
    assert org.rank_index("Contractor") == 1
    assert org.rank_index("Nope") is None


def test_contract_set_lookups():
    org = Org(id="foxwell", name="Foxwell")
    c = Contract(
        id="a", org=org, family="f", keys={StringKind.TITLE: ["Foxwell_a_title"]}
    )
    cs = ContractSet(contracts=[c], orgs={"foxwell": org})

    assert cs.by_org("Foxwell") == [c]
    assert cs.by_key("Foxwell_a_title") is c
    assert cs.by_key("missing") is None


def test_contract_key_accessors():
    c = Contract(
        id="a",
        org=Org(id="o", name="O"),
        family="f",
        keys={StringKind.DESC: ["d_001", "d_002"]},
        texts={"d_001": "first", "d_002": "second"},
    )
    assert c.key(StringKind.DESC) == "d_001"
    assert c.keys_of(StringKind.DESC) == ["d_001", "d_002"]
    assert c.all_keys() == ["d_001", "d_002"]
    assert c.desc == "first"
    assert c.text("d_002") == "second"
    assert c.title is None
    assert c.has_variants


def test_sorted_by_rank_puts_unranked_last():
    org = Org(id="o", name="O")
    hard = Contract(id="h", org=org, family="f", difficulty=Difficulty.SUPERIOR)
    easy = Contract(id="e", org=org, family="f", difficulty=Difficulty.VERY_EASY)
    none = Contract(id="n", org=org, family="f")

    assert ContractSet(contracts=[none, hard, easy]).sorted_by_rank() == [easy, hard, none]
