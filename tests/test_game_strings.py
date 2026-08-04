"""The primary source: contracts discovered from the game's own strings."""

from pathlib import Path

import pytest

from starcompanion.ini import BOM, LocalizationFile
from starcompanion.model import Difficulty, StringKind
from starcompanion.sources import game_strings, naming

SAMPLES = Path(__file__).parent / "samples"
STOCK = SAMPLES / "stock-global.ini"

LONG = "A" * 200


def build(*pairs: tuple[str, str]) -> LocalizationFile:
    body = "".join(f"{k}={v}\n" for k, v in pairs)
    return LocalizationFile.loads(BOM + body)


def contract_pair(base: str, desc: str = LONG) -> tuple[tuple[str, str], ...]:
    return ((f"{base}_title", "Do the thing"), (f"{base}_desc", desc))


# --- key convention ----------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("Foxwell_bombingrun_VE_title_001", True),
        ("Covalex_HaulCargo_AToB_title", True),
        ("item_Name_Something_desc", False),      # item namespace
        ("ui_button_label_title", False),         # ui namespace
        ("short_title", False),                   # too few segments
        ("Foxwell_bombingrun_VE", False),         # no kind segment
        ("9_thing_title", False),                 # org must start with a letter
    ],
)
def test_key_shape(key, expected):
    assert naming.looks_like_contract(key) is expected


def test_non_mission_namespaces_are_excluded():
    """`item_*` alone is over a thousand item names sharing the contract shape."""
    assert "item" in naming.NON_MISSION_NAMESPACES
    assert not naming.looks_like_contract("item_Name_Aves_Core_desc")


# --- content filter ----------------------------------------------------------


def test_a_title_and_a_long_description_is_a_contract():
    result = game_strings.parse(build(*contract_pair("Foxwell_bombingrun_VE")))
    assert len(result.contracts) == 1


def test_a_mission_token_marks_a_contract_even_when_short():
    result = game_strings.parse(
        build(
            ("Foxwell_thing_title", "Go to ~mission(Location|Address)"),
            ("Foxwell_thing_desc", "Short."),
        )
    )
    assert len(result.contracts) == 1


def test_a_title_without_a_description_is_not_a_contract():
    assert game_strings.parse(build(("Foxwell_thing_title", "Label"))).contracts == []


def test_short_prose_without_a_token_is_not_a_contract():
    """Guards against rewriting unrelated labels that share the key shape."""
    result = game_strings.parse(
        build(("Someprefix_thing_title", "Label"), ("Someprefix_thing_desc", "Also short."))
    )
    assert result.contracts == []


def test_dataforge_evidence_accepts_a_short_mission_pair():
    strings = build(
        ("Foxwell_short_title", "Brief job"),
        ("Foxwell_short_desc", "Recover it."),
    )

    result = game_strings.parse(
        strings,
        evidenced_keys={"Foxwell_short_title", "Foxwell_short_desc"},
    )

    assert len(result.contracts) == 1


def test_exact_dataforge_evidence_accepts_a_two_segment_mission_pair():
    strings = build(("recovery_title", "Recovery"), ("recovery_desc", "Recover it."))

    ordinary = game_strings.parse(strings)
    evidenced = game_strings.parse(
        strings,
        evidenced_keys={"recovery_title", "recovery_desc"},
    )

    assert ordinary.contracts == []
    assert len(evidenced.contracts) == 1
    assert evidenced.contracts[0].org.id == "recovery"


def test_evidence_relationship_groups_shared_title_with_variant_descriptions():
    strings = build(
        ("bhg_bounty_title_Rockcracker_001", "Rockcracker"),
        ("bhg_bounty_desc_Rockcracker_Zone1_001", "Zone one"),
        ("bhg_bounty_desc_Rockcracker_Zone2_001", "Zone two"),
    )

    result = game_strings.parse(
        strings,
        evidenced_groups=[
            game_strings.MissionKeyEvidence(
                ("bhg_bounty_title_Rockcracker_001",),
                ("bhg_bounty_desc_Rockcracker_Zone1_001",),
            ),
            game_strings.MissionKeyEvidence(
                ("bhg_bounty_title_Rockcracker_001",),
                ("bhg_bounty_desc_Rockcracker_Zone2_001",),
            ),
        ],
    )

    assert len(result.contracts) == 1
    assert result.contracts[0].keys[StringKind.DESC] == [
        "bhg_bounty_desc_Rockcracker_Zone1_001",
        "bhg_bounty_desc_Rockcracker_Zone2_001",
    ]


def test_explicit_title_segment_wins_when_malformed_fact_claims_both_roles():
    strings = build(
        ("blackbox_recover_title_H_001,P", "Recover a black box"),
        ("blackbox_recover_desc_H_001,P", "Recover it."),
    )

    result = game_strings.parse(
        strings,
        evidenced_groups=[
            game_strings.MissionKeyEvidence(
                ("blackbox_recover_title_H_001",),
                ("blackbox_recover_desc_H_001",),
            ),
            game_strings.MissionKeyEvidence(
                ("blackbox_recover_title_H_001",),
                ("blackbox_recover_title_H_001",),
            ),
        ],
    )

    assert len(result.contracts) == 1
    assert result.contracts[0].key(StringKind.TITLE) == "blackbox_recover_title_H_001,P"


def test_typed_description_role_handles_nonstandard_description_key():
    strings = build(
        ("RepairOxygenKiosk_Title", "Repair oxygen"),
        ("RepairOxygenKiosk_DescriptionLong", "Restore the kiosk."),
    )

    result = game_strings.parse(
        strings,
        evidenced_groups=[
            game_strings.MissionKeyEvidence(
                ("RepairOxygenKiosk_Title",),
                ("RepairOxygenKiosk_DescriptionLong",),
            )
        ],
    )

    assert len(result.contracts) == 1
    assert result.contracts[0].key(StringKind.DESC) == "RepairOxygenKiosk_DescriptionLong"


def test_placeholder_title_can_still_admit_an_evidenced_description_only_contract():
    strings = build(("IAE_2955_Wolf_Rosso_Desc", "Meet Rosso."))

    result = game_strings.parse(
        strings,
        evidenced_groups=[
            game_strings.MissionKeyEvidence(
                ("LOC_UNINITIALIZED",),
                ("IAE_2955_Wolf_Rosso_Desc",),
            )
        ],
    )

    assert len(result.contracts) == 1
    assert result.contracts[0].key(StringKind.TITLE) is None
    assert result.contracts[0].key(StringKind.DESC) == "IAE_2955_Wolf_Rosso_Desc"


def test_evidence_does_not_weaken_unevidenced_ui_or_item_filters():
    strings = build(
        ("Foxwell_short_title", "Brief job"),
        ("Foxwell_short_desc", "Recover it."),
        ("ui_panel_title", "Panel"),
        ("ui_panel_desc", "Panel description"),
        ("item_helmet_title", "Helmet"),
        ("item_helmet_desc", "Helmet description"),
    )

    result = game_strings.parse(
        strings,
        evidenced_keys={"Foxwell_short_title", "Foxwell_short_desc"},
    )

    assert {contract.org.id for contract in result.contracts} == {"foxwell"}


def test_typed_evidence_still_cannot_admit_denied_namespaces():
    strings = build(
        ("ui_title", "Panel"),
        ("ui_desc", "Panel description"),
        ("item_title", "Helmet"),
        ("item_desc", "Helmet description"),
    )

    result = game_strings.parse(
        strings,
        evidenced_keys={"ui_title", "ui_desc", "item_title", "item_desc"},
    )

    assert result.contracts == []


def test_plural_suffix_is_ignored_for_classification_but_preserved_as_key():
    strings = build(
        ("Foxwell_short_title,P", "Brief jobs"),
        ("Foxwell_short_desc,P", "Recover them."),
    )

    result = game_strings.parse(
        strings,
        evidenced_keys={"Foxwell_short_title", "Foxwell_short_desc"},
    )

    assert result.contracts[0].all_keys() == [
        "Foxwell_short_title,P",
        "Foxwell_short_desc,P",
    ]


# --- structure ---------------------------------------------------------------


def test_difficulty_and_rank_come_from_the_key():
    result = game_strings.parse(build(*contract_pair("Foxwell_bombingrun_VH")))
    contract = result.contracts[0]

    assert contract.difficulty is Difficulty.VERY_HARD
    assert contract.rank == 5
    assert contract.colour == "Red"


def test_org_and_family_are_derived():
    result = game_strings.parse(build(*contract_pair("Foxwell_bombingrun_VE")))
    contract = result.contracts[0]

    assert contract.org.id == "foxwell"
    assert contract.family == "bombingrun"


def test_title_and_desc_pair_onto_one_contract():
    result = game_strings.parse(build(*contract_pair("Foxwell_bombingrun_VE")))
    contract = result.contracts[0]

    assert contract.key(StringKind.TITLE).endswith("_title")
    assert contract.key(StringKind.DESC).endswith("_desc")


def test_stock_text_is_its_own_base():
    """Nothing has been appended to a stock string, so there is nothing to strip."""
    result = game_strings.parse(build(*contract_pair("Foxwell_x_VE")))
    contract = result.contracts[0]

    for key in contract.all_keys():
        assert contract.base_text(key) == contract.text(key)


def test_rewards_are_absent_not_zero():
    """Reward values are not in the client; empty must not read as zero."""
    result = game_strings.parse(build(*contract_pair("Foxwell_x_VE")))
    assert result.contracts[0].reward.is_empty


def test_org_casing_is_normalised():
    result = game_strings.parse(
        build(*contract_pair("headhunters_a"), *contract_pair("HeadHunters_b"))
    )
    assert len(result.orgs) == 1
    assert len(result.by_org("headhunters")) == 2


def test_a_key_starting_with_the_kind_segment_keeps_its_org():
    """Splitting removes the kind wherever it sits, which would otherwise
    shift the org token to the next segment."""
    result = game_strings.parse(
        build(
            ("Name_Arken_Mallor_thing", "Go to ~mission(Location)"),
            ("Desc_Arken_Mallor_thing", "Go to ~mission(Location)"),
        )
    )
    assert result.contracts and result.contracts[0].org.id == "name"


def test_orgs_only_include_givers_with_real_contracts():
    result = game_strings.parse(
        build(*contract_pair("Foxwell_a"), ("Rejected_b_title", "short"), ("Rejected_b_desc", "s"))
    )
    assert set(result.orgs) == {"foxwell"}


# --- against the real game strings -------------------------------------------

real = pytest.mark.skipif(
    not STOCK.exists(), reason="stock global.ini not extracted from a game install"
)


@pytest.fixture(scope="module")
def discovered():
    if not STOCK.exists():
        pytest.skip("stock global.ini not available")
    return game_strings.load(STOCK)


@real
def test_real_discovery_finds_contracts(discovered):
    assert len(discovered.contracts) > 1_000
    assert len(discovered.orgs) > 50


@real
def test_real_discovery_ranks_a_known_family(discovered):
    """Foxwell's bombing run runs the full six difficulty tiers."""
    ranks = {
        c.rank
        for c in discovered.by_org("foxwell")
        if c.family.lower().startswith("bombingrun") and c.rank
    }
    assert ranks == {1, 2, 3, 4, 5, 6}


@real
def test_real_discovery_excludes_item_strings(discovered):
    assert "item" not in discovered.orgs
    assert "ui" not in discovered.orgs


@real
def test_real_discovery_covers_most_of_a_community_list(discovered):
    """Sanity check against StarStrings' hand-curated set, where available."""
    source = SAMPLES / "contracts.ini"
    if not source.exists():
        pytest.skip("contracts.ini sample not present")

    from starcompanion.sources import contracts_ini

    theirs = {k for c in contracts_ini.load(source).contracts for k in c.all_keys()}
    ours = {k for c in discovered.contracts for k in c.all_keys()}

    assert len(ours & theirs) / len(theirs) > 0.75
    assert len(ours - theirs) > 1_000, "should also find contracts they do not cover"


@real
def test_real_discovery_needs_no_community_file(discovered):
    """The whole point: this works from the game install alone."""
    assert all(c.reward.is_empty for c in discovered.contracts)
    assert discovered.contracts
