from starcompanion.enhancements import (
    MissionEnhancementProvider,
    apply_enhancements,
    unavailable_mission_enhancements,
)
from starcompanion.extract.dataforge import (
    BlueprintPoolFacts,
    CapabilityReport,
    CapabilityStatus,
    Confidence,
    Evidence as RawEvidence,
    MissionExtractionResult,
    MissionFacts,
)
from starcompanion.model import Contract, ContractSet, Org, ProviderStatus, StringKind
from starcompanion.render import Renderer


def contracts() -> ContractSet:
    org = Org("foxwell", "Foxwell")
    contract = Contract(
        id="Foxwell_Test",
        org=org,
        family="Test",
        keys={StringKind.TITLE: ["Test_Title"], StringKind.DESC: ["Test_Desc"]},
        texts={"Test_Title": "Test Mission", "Test_Desc": "Do the mission."},
        base_texts={"Test_Title": "Test Mission", "Test_Desc": "Do the mission."},
    )
    return ContractSet([contract], {org.id: org})


def mission_result(status=CapabilityStatus.AVAILABLE) -> MissionExtractionResult:
    evidence = (
        RawEvidence("mission-guid", "mission/test.xml", "$.reward", 1250),
        RawEvidence("pool-guid", "rewards/pool.xml", "$.items[0]", "Laser_Name"),
    )
    fact = MissionFacts(
        mission_id="mission-guid",
        title_keys=("Test_Title",),
        description_keys=("Test_Desc",),
        reputation=(1250,),
        blueprint_pools=(BlueprintPoolFacts("pool-guid", ("Laser_Name",), 0.25),),
        item_rewards=("Suit_Name",),
        source_record_ids=("mission-guid", "pool-guid"),
        evidence=evidence,
        confidence=Confidence.HIGH,
    )
    report = CapabilityReport(
        "dataforge-mission-facts", status, 8, 1, 1
    )
    return MissionExtractionResult((fact,), report)


def test_mission_provider_merges_local_rewards_and_resolves_display_names():
    names = {"Laser_Name": "CF-117 Bulldog", "Suit_Name": "Novikov Undersuit"}
    enhancement_set = MissionEnhancementProvider(names.get).build(mission_result())

    result = apply_enhancements(contracts(), [enhancement_set])
    contract = result.contracts[0]

    assert contract.reward.reputation == [1250]
    assert contract.reward.blueprint_pools[0].items == ["CF-117 Bulldog"]
    assert contract.reward.blueprint_pools[0].chance == 0.25
    assert contract.reward.blueprints_conditional
    assert contract.reward.item_rewards == ["Novikov Undersuit"]
    assert {item.provider for item in contract.evidence} == {"local-dataforge-missions"}
    assert result.capabilities[0].status is ProviderStatus.AVAILABLE
    assert result.capabilities[0].contracts_enhanced == 1
    assert result.capabilities[0].evidence_links == 2
    assert result.capabilities[0].reward_facts == 1
    assert result.capabilities[0].matched_facts == 1
    assert result.capabilities[0].unmatched_facts == 0


def test_mission_provider_never_renders_placeholder_reward_labels():
    result = mission_result()
    fact = result.facts[0].__class__(
        **{
            **result.facts[0].__dict__,
            "blueprint_pools": (
                BlueprintPoolFacts(
                    "pool-guid",
                    ("LOC_UNINITIALIZED", "Laser_Name"),
                    0.25,
                ),
            ),
            "item_rewards": ("LOC_PLACEHOLDER", "Suit_Name", "Bad_Name"),
        }
    )
    source = MissionExtractionResult((fact,), result.capability)
    names = {
        "Laser_Name": "CF-117 Bulldog",
        "Suit_Name": "Novikov Undersuit",
        "Bad_Name": "<= UNINITIALIZED =>",
    }

    merged = apply_enhancements(
        contracts(),
        [MissionEnhancementProvider(names.get).build(source)],
    )
    contract = merged.contracts[0]

    assert contract.reward.blueprint_pools[0].items == ["CF-117 Bulldog"]
    assert contract.reward.item_rewards == ["Novikov Undersuit"]


def test_provider_is_deterministic_and_disabling_removes_only_its_output():
    base = contracts()
    provider = MissionEnhancementProvider()
    enhancement_set = provider.build(mission_result())

    first = apply_enhancements(base, [enhancement_set])
    second = apply_enhancements(base, [enhancement_set])
    disabled = apply_enhancements(base, [])

    assert first == second
    assert disabled == base
    assert base.contracts[0].reward.is_empty
    assert not first.contracts[0].reward.is_empty


def test_unavailable_provider_fails_independently_without_mutating_contracts():
    base = contracts()
    result = apply_enhancements(
        base,
        [unavailable_mission_enhancements("test-build", "synthetic drift")],
    )

    assert result.contracts == base.contracts
    assert result.capabilities[0].status is ProviderStatus.UNAVAILABLE
    assert result.capabilities[0].diagnostics == ("synthetic drift",)


def test_unmatched_reward_fact_is_reported_with_bounded_samples():
    result = mission_result()
    unmatched_fact = result.facts[0].__class__(
        **{**result.facts[0].__dict__, "mission_id": "unmatched", "title_keys": ("Missing_Title",), "description_keys": ()}
    )
    source = MissionExtractionResult((unmatched_fact,), result.capability)

    merged = apply_enhancements(
        contracts(), [MissionEnhancementProvider().build(source)]
    )
    capability = merged.capabilities[0]

    assert capability.reward_facts == 1
    assert capability.matched_facts == 0
    assert capability.unmatched_facts == 1
    assert capability.unmatched_reason_counts == (("localization-missing", 1),)
    assert capability.unmatched_samples == (
        "localization-missing: unmatched: Missing_Title",
    )


def test_unmatched_fact_distinguishes_localized_but_filtered_contract():
    result = mission_result()
    fact = result.facts[0].__class__(
        **{**result.facts[0].__dict__, "mission_id": "filtered", "title_keys": ("Filtered_Title",), "description_keys": ()}
    )
    source = MissionExtractionResult((fact,), result.capability)

    merged = apply_enhancements(
        contracts(),
        [MissionEnhancementProvider(lambda _key: "Localized text").build(source)],
    )

    assert merged.capabilities[0].unmatched_reason_counts == (("contract-filtered", 1),)


def test_provider_matches_datacore_key_to_plural_localization_alias():
    base = contracts()
    contract = base.contracts[0]
    contract.keys = {
        StringKind.TITLE: ["Test_Title,P"],
        StringKind.DESC: ["Test_Desc,P"],
    }
    contract.texts = {
        "Test_Title,P": "Test Missions",
        "Test_Desc,P": "Do the missions.",
    }
    contract.base_texts = dict(contract.texts)

    result = apply_enhancements(
        base,
        [MissionEnhancementProvider(lambda _key: "localized").build(mission_result())],
    )

    assert result.capabilities[0].matched_facts == 1
    assert result.contracts[0].reward.reputation == [1250]


def test_placeholder_only_fact_is_reported_separately_from_missing_localization():
    result = mission_result()
    fact = result.facts[0].__class__(
        **{**result.facts[0].__dict__, "mission_id": "placeholder", "title_keys": ("LOC_UNINITIALIZED",), "description_keys": ()}
    )
    source = MissionExtractionResult((fact,), result.capability)

    merged = apply_enhancements(
        contracts(),
        [MissionEnhancementProvider(lambda _key: "placeholder text").build(source)],
    )

    assert merged.capabilities[0].unmatched_reason_counts == (("placeholder-key", 1),)


def test_local_reward_golden_render_and_per_key_provenance():
    names = {"Laser_Name": "CF-117 Bulldog", "Suit_Name": "Novikov Undersuit"}
    result = apply_enhancements(
        contracts(), [MissionEnhancementProvider(names.get).build(mission_result())]
    )

    rendered = Renderer().render_all(result)

    assert rendered.values["Test_Desc"] == (
        "Do the mission.\\n\\n<EM4>Reputation Awarded:</EM4> 1,250\\n"
        "<EM4>Item Rewards:</EM4> Novikov Undersuit\\n\\n"
        "<EM4>Potential Blueprints</EM4>\\n"
        "<EM4>Award chance: 25.0%</EM4>\\n- CF-117 Bulldog"
    )
    assert len(rendered.provenance["Test_Title"]) == 2
    assert len(rendered.provenance["Test_Desc"]) == 2
