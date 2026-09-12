import pytest

from starcompanion.model import (
    Contract,
    ContractSet,
    Evidence,
    FactConfidence,
    MissionDetail,
    Org,
    StringKind,
)
from starcompanion.render import Renderer, RenderOptions
from starcompanion.route_presentation import resource_signature, route_fragment


def contract(title_key, bodies, title="Cargo Run"):
    keys = {StringKind.TITLE: [title_key], StringKind.DESC: list(bodies)}
    texts = {title_key: title, **bodies}
    return Contract("route", Org("org", "Org"), "Haul", keys=keys, texts=texts, base_texts=dict(texts))


def test_route_uses_only_endpoints_shared_by_description_variants():
    item = contract(
        "Org_HaulCargo_title",
        {
            "d1": "From ~mission(Location|Address) to ~mission(Destination|Address)",
            "d2": "From ~mission(Location1|Address) to ~mission(Destination|Address)",
        },
    )
    assert route_fragment(item) == "to ~mission(Destination|Address)"


def test_route_supports_numbered_endpoints_and_typed_display_choices():
    item = contract(
        "Org_Courier_title",
        {"d": "~mission(Pickup1|Address) then ~mission(Destination1|Address)"},
    )
    assert route_fragment(item, arrow="->", detail="name") == (
        "~mission(Pickup1|Address) -> ~mission(Destination1|name)"
    )
    with pytest.raises(ValueError):
        route_fragment(item, arrow="⇒")


def test_non_route_family_and_title_with_existing_endpoint_are_unchanged():
    unrelated = contract("Org_Bounty_title", {"d": "at ~mission(Location)"})
    existing = contract(
        "Org_Delivery_title",
        {"d": "to ~mission(Destination)"},
        title="Delivery to ~mission(Destination)",
    )
    assert route_fragment(unrelated) == ""
    assert route_fragment(existing) == ""


def test_battaglia_resource_signature_uses_only_exact_stock_tokens():
    item = contract(
        "Battaglia_RPT_ScanMine_01_title",
        {"d": "Mine ~mission(Resources) ~mission(MineableType); ignore ~mission(Rarity)"},
    )
    assert resource_signature(item) == "~mission(Resources) ~mission(MineableType)"
    assert resource_signature(contract("Org_Mining_title", {"d": "~mission(Resources)"})) == ""


def test_renderer_appends_or_replaces_route_and_tracks_token_evidence():
    item = contract(
        "Org_HaulCargo_title",
        {"d": "From ~mission(Location) to ~mission(Destination)"},
    )
    append = Renderer(RenderOptions(route_titles_enabled=True)).render_key(item, "Org_HaulCargo_title")
    replace = Renderer(RenderOptions(route_titles_enabled=True, route_title_mode="replace")).render_key(item, "Org_HaulCargo_title")
    assert append == "Cargo Run [~mission(Location|Address) > ~mission(Destination|Address)]"
    assert replace == "~mission(Location|Address) > ~mission(Destination|Address)"

    rendered = Renderer(RenderOptions(route_titles_enabled=True)).render_all(
        ContractSet([item], {"org": item.org})
    )
    assert {e.provider for e in rendered.provenance["Org_HaulCargo_title"]} == {
        "local-stock-mission-tokens"
    }


def test_route_provenance_excludes_disagreed_and_unrendered_tokens():
    item = contract(
        "Org_HaulCargo_title",
        {
            "d1": "~mission(Location) to ~mission(Destination)",
            "d2": "~mission(Location2) to ~mission(Destination)",
        },
    )
    rendered = Renderer(RenderOptions(route_titles_enabled=True)).render_all(
        ContractSet([item], {"org": item.org})
    )

    evidence = rendered.provenance["Org_HaulCargo_title"]
    assert {item.field_path for item in evidence} == {"token:Destination"}
    assert {item.record_path for item in evidence} == {
        "localization:d1",
        "localization:d2",
    }


def test_route_and_resource_tags_are_omitted_as_whole_units_at_the_budget():
    route = contract(
        "Org_HaulCargo_title",
        {"d": "~mission(Location123456789) to ~mission(Destination123456789)"},
    )
    mining = contract(
        "Battaglia_RPT_ScanMine_01_title",
        {"d": "Mine ~mission(Resources) and ~mission(MineableType)"},
    )
    options = RenderOptions(
        route_titles_enabled=True,
        route_title_mode="replace",
        mining_signature_enabled=True,
        tag_builder_max_characters=16,
    )

    assert Renderer(options).render_key(route, "Org_HaulCargo_title") == "Cargo Run"
    assert Renderer(options).render_key(
        mining, "Battaglia_RPT_ScanMine_01_title"
    ) == "Cargo Run"
    rendered = Renderer(options).render_all(
        ContractSet([route, mining], {"org": route.org})
    )
    assert all(not evidence for evidence in rendered.provenance.values())


def test_renderer_appends_resource_signature_with_rendered_only_evidence():
    item = contract(
        "Battaglia_RPT_ScanMine_01_title",
        {"d": "Mine ~mission(Resources); ignore ~mission(Rarity)"},
    )
    rendered = Renderer(RenderOptions(mining_signature_enabled=True)).render_all(
        ContractSet([item], {"org": item.org})
    )

    assert rendered.values["Battaglia_RPT_ScanMine_01_title"] == (
        "Cargo Run [~mission(Resources)]"
    )
    assert {item.field_path for item in rendered.provenance[
        "Battaglia_RPT_ScanMine_01_title"
    ]} == {"token:Resources"}


def test_route_and_tactical_tags_share_one_complete_unit_budget():
    item = contract(
        "Org_HaulCargo_title",
        {"d": "~mission(Location) to ~mission(Destination)"},
    )
    item.mission_details.append(
        MissionDetail(
            "mission-type",
            "Hauling",
            FactConfidence.HIGH,
            (Evidence("local-tactical", "r", "p", "f", "Hauling"),),
        )
    )
    options = RenderOptions(
        mission_fact_groups=frozenset({"mission_type"}),
        tag_builder_enabled=True,
        tag_builder_fields=("mission_type",),
        tag_builder_max_characters=64,
        route_titles_enabled=True,
    )

    rendered = Renderer(options).render_key(item, "Org_HaulCargo_title")
    assert rendered.startswith("[Hauling] Cargo Run")
    assert "~mission(" not in rendered


def test_advanced_template_that_omits_route_gets_no_route_provenance():
    item = contract(
        "Org_HaulCargo_title",
        {"d": "~mission(Location) to ~mission(Destination)"},
    )
    rendered = Renderer(
        RenderOptions(route_titles_enabled=True),
        overrides={"orgs/org/title.j2": "{{ base }}"},
    ).render_all(ContractSet([item], {"org": item.org}))

    assert rendered.values["Org_HaulCargo_title"] == "Cargo Run"
    assert not rendered.provenance["Org_HaulCargo_title"]
