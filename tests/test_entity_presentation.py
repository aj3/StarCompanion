from starcompanion.entity_presentation import (
    PRESENTATION_PROVIDER,
    attach_entity_presentation,
)
from starcompanion.extract.dataforge import DataForgeIndex
from starcompanion.extract.entities import extract_entity_catalog
from starcompanion.ini import LocalizationFile
from starcompanion.model import ContractSet, ProviderStatus
from starcompanion.render import Renderer, RenderOptions
from test_entities import entity_fixture, specialized_fixture


def localization() -> LocalizationFile:
    return LocalizationFile.loads(
        "\n".join(
            (
                "vehicle_name_test=Test Vehicle",
                "item_name_test=Test Component",
                "item_name_ship_laser=Laser Cannon",
                "item_name_fps_rifle=Rifle",
                "item_name_medpen=MedPen",
                "commodity_name_ore=Ore",
                "recipe_name_laser=Laser Recipe",
                "journal_title_cave=Cave",
            )
        )
    )


def test_entity_catalog_joins_only_local_names_and_keeps_typed_evidence():
    catalog = extract_entity_catalog(DataForgeIndex(specialized_fixture()))
    contracts = attach_entity_presentation(ContractSet(), catalog, localization())

    assert len(contracts.entities) == 6
    component = next(item for item in contracts.entities if item.kind == "component")
    assert component.base_text == "Test Component"
    assert component.attribute("size").value == 2
    assert component.attribute("grade") is None
    assert component.attribute("class").value == "Military"
    assert all(item.provider == "local-dataforge-components" for item in component.evidence)
    capability = next(
        item for item in contracts.capabilities if item.provider == PRESENTATION_PROVIDER
    )
    assert capability.status is ProviderStatus.AVAILABLE
    assert capability.matched_facts == 6
    assert not capability.unmatched_reason_counts


def test_generic_and_cross_domain_localization_keys_are_suppressed():
    source = entity_fixture(
        vehicle_payload={"vehicleName": "@ui_generic_label", "mass": 10},
        component_payload={"displayName": "@vehicle_name_wrong", "itemSize": 2},
    )
    catalog = extract_entity_catalog(DataForgeIndex(source))
    strings = LocalizationFile.loads(
        "ui_generic_label=Open\nvehicle_name_wrong=Wrong domain"
    )
    contracts = attach_entity_presentation(ContractSet(), catalog, strings)

    assert not contracts.entities
    capability = next(
        item for item in contracts.capabilities if item.provider == PRESENTATION_PROVIDER
    )
    assert capability.status is ProviderStatus.UNAVAILABLE
    assert dict(capability.unmatched_reason_counts) == {
        "unsupported-localization-family": 2
    }


def test_entity_tag_builder_is_default_off_typed_bounded_and_provenanced():
    catalog = extract_entity_catalog(DataForgeIndex(specialized_fixture()))
    contracts = attach_entity_presentation(ContractSet(), catalog, localization())
    component = next(item for item in contracts.entities if item.kind == "component")

    assert Renderer().render_all(contracts).values == {}
    renderer = Renderer(RenderOptions(entity_tag_builder_enabled=True))
    rendered = renderer.render_all(contracts)
    assert rendered.values[component.localization_key] == (
        "[Component S2 Military] Test Component"
    )
    assert {
        item.provider for item in rendered.provenance[component.localization_key]
    } == {"local-dataforge-components"}

    bounded = Renderer(
        RenderOptions(
            entity_tag_builder_enabled=True,
            entity_tag_max_characters=16,
        )
    ).render_all(contracts)
    assert bounded.values[component.localization_key] == "[Component S2] Test Component"


def test_shared_cross_domain_localization_key_is_suppressed_before_grouping():
    source = entity_fixture(
        vehicle_payload={"vehicleName": "@shared_name", "mass": 10},
        component_payload={"displayName": "@shared_name", "itemSize": 2},
    )
    catalog = extract_entity_catalog(DataForgeIndex(source))
    strings = LocalizationFile.loads("shared_name=Shared")
    contracts = attach_entity_presentation(ContractSet(), catalog, strings)

    assert not contracts.entities
    capability = next(
        item for item in contracts.capabilities if item.provider == PRESENTATION_PROVIDER
    )
    assert capability.status is ProviderStatus.UNAVAILABLE
    assert capability.facts_seen == 2
    assert capability.matched_facts == 0
    assert capability.unmatched_facts == 2
    assert dict(capability.unmatched_reason_counts) == {
        "unsupported-localization-family": 2
    }


def test_shared_component_name_keeps_kind_but_suppresses_disagreed_size():
    source = entity_fixture()
    source.entries.append(
        (
            "Component.Test",
            "Data/Libs/Foundry/Records/Entities/SCItem/Ships/Cooler/other.xml",
            "10000000-0000-0000-0000-000000000099",
            {"displayName": "@item_name_test", "itemSize": 3},
        )
    )
    source.__post_init__()
    catalog = extract_entity_catalog(DataForgeIndex(source))
    contracts = attach_entity_presentation(ContractSet(), catalog, localization())
    component = next(item for item in contracts.entities if item.kind == "component")

    assert component.attribute("size") is None
    capability = next(
        item for item in contracts.capabilities if item.provider == PRESENTATION_PROVIDER
    )
    assert capability.facts_seen == 3
    assert capability.matched_facts == 3
    assert capability.unmatched_facts == 0
    rendered = Renderer(
        RenderOptions(entity_tag_builder_enabled=True)
    ).render_all(contracts)
    assert rendered.values[component.localization_key] == "[Component] Test Component"


def test_missile_classification_is_bounded_to_ship_weapon_record_evidence():
    source = specialized_fixture()
    source.entries = [
        entry
        for entry in source.entries
        if entry[2] != "10000000-0000-0000-0000-000000000011"
    ]
    source.entries.append(
        (
            "ShipWeapon.Test",
            "Data/Libs/Foundry/Records/Entities/SCItem/Weapons/Ship/Missiles/test.xml",
            "10000000-0000-0000-0000-000000000011",
            {"displayName": "@item_name_missile", "itemSize": 3},
        )
    )
    source.__post_init__()
    catalog = extract_entity_catalog(DataForgeIndex(source))
    strings = LocalizationFile.loads(
        localization().dumps() + "\nitem_name_missile=Test Missile"
    )
    contracts = attach_entity_presentation(ContractSet(), catalog, strings)
    missile = next(item for item in contracts.entities if item.kind == "missile")

    rendered = Renderer(
        RenderOptions(
            entity_tag_builder_enabled=True,
            entity_tag_kinds=frozenset({"missile"}),
            entity_tag_placement="suffix",
        )
    ).render_all(contracts)
    assert rendered.values[missile.localization_key] == "Test Missile [Missile S3]"
