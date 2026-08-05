from pathlib import Path

import pytest

from starcompanion.model import (
    BlueprintPool,
    Contract,
    Difficulty,
    Gate,
    GateKind,
    Org,
    Reward,
    ScenarioPoints,
    StringKind,
)
from starcompanion.render import RenderOptions, Renderer, TemplateRenderError
from starcompanion.render.renderer import TitlePrefix
from starcompanion.sources import contracts_ini
from starcompanion.validate import Severity, validate_value

SAMPLES = Path(__file__).parent / "samples"


def make_contract(**kwargs) -> Contract:
    org = kwargs.pop("org", None) or Org(id="foxwell", name="Foxwell")
    reward = kwargs.pop("reward", None) or Reward(reputation=[100])
    base = kwargs.pop("base", "Do the thing.")
    return Contract(
        id="Foxwell_Test_VE",
        org=org,
        family="Test",
        difficulty=kwargs.pop("difficulty", Difficulty.VERY_EASY),
        keys={StringKind.TITLE: ["t"], StringKind.DESC: ["d"]},
        texts={"t": base, "d": base},
        base_texts={"t": base, "d": base},
        reward=reward,
        **kwargs,
    )


def render(contract, key="d", **opts):
    return Renderer(RenderOptions(**opts)).render_key(contract, key)


# --- output safety -----------------------------------------------------------


def test_template_newlines_become_literal_escapes():
    value = render(make_contract())
    assert "\n" not in value
    assert r"\n" in value


def test_rendered_output_always_passes_validation():
    value = render(make_contract())
    assert validate_value(value) == []


def test_renderer_preserves_cig_placeholder_tags_from_stock():
    from starcompanion.model import ContractSet

    contract = make_contract(base="Available in <years>")
    result = Renderer().render_all(ContractSet([contract], {contract.org.id: contract.org}))

    assert not result.skipped
    assert "<years>" in result.values["d"]


def test_renderer_does_not_report_a_stock_warning_as_generated():
    from starcompanion.model import ContractSet

    contract = make_contract(base="CIG text <EM>with mismatched close</EM4>")
    result = Renderer().render_all(ContractSet([contract], {contract.org.id: contract.org}))

    assert not result.skipped
    assert not result.warnings


def test_base_text_literal_escapes_survive_untouched():
    contract = make_contract(base=r"Line one\nLine two")
    assert r"Line one\nLine two" in render(contract)


def test_a_template_emitting_a_real_newline_cannot_reach_the_output():
    # A raw newline would blank the contract in-game, so it must be converted.
    renderer = Renderer(overrides={"desc.j2": "A\nB"})
    assert renderer.render_key(make_contract(), "d") == r"A\nB"


def test_values_failing_validation_are_skipped_not_emitted():
    from starcompanion.model import ContractSet

    renderer = Renderer(overrides={"desc.j2": "<bogus>x</bogus>", "title.j2": "ok"})
    result = renderer.render_all(ContractSet(contracts=[make_contract()]))

    assert "d" not in result.values
    assert any(key == "d" for key, _ in result.skipped)
    assert "t" in result.values


def test_template_errors_name_the_key_and_template():
    renderer = Renderer(overrides={"desc.j2": "{{ nonexistent_variable }}"})
    with pytest.raises(TemplateRenderError) as exc:
        renderer.render_key(make_contract(), "d")

    assert exc.value.key == "d"
    assert exc.value.template == "desc.j2"


@pytest.mark.parametrize(
    "expression",
    [
        "{{ cycler.__init__.__globals__.os.getcwd() }}",
        "{{ contract.__class__.__mro__ }}",
    ],
)
def test_template_cannot_reach_python_internals(expression):
    renderer = Renderer(overrides={"desc.j2": expression})
    with pytest.raises(TemplateRenderError, match="unsafe|SecurityError|access"):
        renderer.render_key(make_contract(), "d")


# --- options -----------------------------------------------------------------


def test_reputation_toggle():
    assert "Reputation Awarded" in render(make_contract())
    assert "Reputation Awarded" not in render(make_contract(), show_reputation=False)


def test_blueprint_toggle():
    contract = make_contract(
        reward=Reward(reputation=[100], blueprint_pools=[BlueprintPool(items=["Aves Core"])])
    )
    assert "Aves Core" in render(contract)
    assert "Aves Core" not in render(contract, show_blueprints=False)


def test_scenario_points_toggle_and_split_flag():
    contract = make_contract(
        reward=Reward(scenario_points=[ScenarioPoints(120000, split=True)])
    )
    assert "Scenario Progress Points 120,000 (Split)" in render(contract)
    assert "Scenario" not in render(contract, show_scenario_points=False)


def test_scrip_toggle():
    contract = make_contract(reward=Reward(reputation=[10], scrip=True))
    assert "MG Scrip" in render(contract)
    assert "MG Scrip" not in render(contract, show_scrip=False)


def test_rank_gate_toggle():
    contract = make_contract(
        reward=Reward(
            blueprint_pools=[
                BlueprintPool(items=["X"], gates=[Gate(GateKind.RANK, "Jr. Contractor")])
            ]
        )
    )
    assert "Awarded from Jr. Contractor level variants" in render(contract)
    assert "Awarded from" not in render(contract, show_rank_gates=False)


def test_regional_variant_toggle():
    contract = make_contract(
        reward=Reward(
            blueprint_pools=[BlueprintPool(items=["X"], example_locations=["Ruin Station"])]
        )
    )
    assert "Ruin Station" in render(contract)
    assert "Ruin Station" not in render(contract, show_regional_variants=False)


def test_caveat_toggle():
    contract = make_contract(
        reward=Reward(blueprint_pools=[BlueprintPool(items=["X"], caveat="Warning: check scmdb")])
    )
    assert "Warning: check scmdb" in render(contract)
    assert "Warning" not in render(contract, show_caveats=False)


def test_emphasis_tag_is_configurable():
    assert "<EM4>Reputation Awarded:</EM4>" in render(make_contract())
    assert "<EM3>Reputation Awarded:</EM3>" in render(make_contract(), emphasis="EM3")


def test_unrenderable_emphasis_tag_is_rejected_up_front():
    with pytest.raises(ValueError, match="not renderable"):
        RenderOptions(emphasis="span")


def test_max_pool_items_truncates():
    contract = make_contract(
        reward=Reward(blueprint_pools=[BlueprintPool(items=["A", "B", "C", "D"])])
    )
    value = render(contract, max_pool_items=2)
    assert "- A" in value and "- B" in value
    assert "- C" not in value


def test_structured_labels_are_applied_without_template_source():
    from starcompanion.render import RenderLabels

    value = render(
        make_contract(),
        labels=RenderLabels(reputation="Standing earned"),
    )
    assert "Standing earned" in value
    assert "Reputation Awarded" not in value


def test_direct_render_labels_reject_control_and_markup_content():
    from starcompanion.render import RenderLabels

    with pytest.raises(ValueError, match="cannot contain"):
        RenderLabels(reputation="bad<tag>")
    with pytest.raises(ValueError, match="direction overrides"):
        RenderLabels(reputation="safe\u202eevil")


def test_structured_section_order_is_respected():
    contract = make_contract(
        reward=Reward(
            reputation=[100],
            item_rewards=["Aves Core"],
            blueprint_pools=[BlueprintPool(items=["Atlas Frame"])],
        )
    )
    value = render(
        contract,
        section_order=("blueprints", "items", "reputation", "scrip", "scenario"),
    )
    assert value.index("Potential Blueprints") < value.index("Item Rewards")
    assert value.index("Item Rewards") < value.index("Reputation Awarded")


def test_structured_number_formatting_is_applied():
    contract = make_contract(reward=Reward(reputation=[12000, 24000]))
    assert "12,000 • 24,000" in render(contract, reputation_separator=" • ")
    assert "12000/24000" in render(
        contract,
        reputation_separator="/",
        thousands_separator=False,
    )


def test_render_options_reject_incomplete_section_order():
    with pytest.raises(ValueError, match="exactly once"):
        RenderOptions(section_order=("reputation",))


# --- titles ------------------------------------------------------------------


def test_title_bracket_tags():
    contract = make_contract(
        reward=Reward(reputation=[100], blueprint_pools=[BlueprintPool(items=["X"])])
    )
    assert render(contract, "t").endswith("<EM4>[100 Rep] [BP]</EM4>")


def test_title_rep_is_compact_but_body_is_spaced():
    contract = make_contract(reward=Reward(reputation=[300, 16000]))
    assert "[300/16000 Rep]" in render(contract, "t")
    assert "300 / 16,000" in render(contract, "d")


def test_gated_pool_marks_blueprints_as_conditional():
    gated = make_contract(
        reward=Reward(
            blueprint_pools=[
                BlueprintPool(items=["X"], gates=[Gate(GateKind.RANK, "Master")])
            ]
        )
    )
    assert "[BP]*" in render(gated, "t")

    ungated = make_contract(reward=Reward(blueprint_pools=[BlueprintPool(items=["X"])]))
    assert "[BP]" in render(ungated, "t") and "[BP]*" not in render(ungated, "t")


def test_title_bracket_toggles():
    contract = make_contract(
        reward=Reward(reputation=[100], blueprint_pools=[BlueprintPool(items=["X"])])
    )
    assert "Rep]" not in render(contract, "t", title_bracket_rep=False)
    assert "[BP]" not in render(contract, "t", title_bracket_bp=False)


@pytest.mark.parametrize(
    "prefix,expected",
    [
        (TitlePrefix.NONE, "Do the thing."),
        (TitlePrefix.ORG, "[Foxwell] Do the thing."),
        (TitlePrefix.RANK, "[1] Do the thing."),
        (TitlePrefix.ORG_RANK, "[Foxwell 1] Do the thing."),
    ],
)
def test_title_prefix_schemes(prefix, expected):
    assert render(make_contract(), "t", title_prefix=prefix).startswith(expected)


# --- template resolution -----------------------------------------------------


def test_org_override_is_used_when_present():
    renderer = Renderer(overrides={"orgs/foxwell/desc.j2": "OVERRIDDEN"})
    assert renderer.render_key(make_contract(), "d") == "OVERRIDDEN"


def test_org_override_does_not_affect_other_orgs():
    renderer = Renderer(overrides={"orgs/foxwell/desc.j2": "OVERRIDDEN"})
    other = make_contract(org=Org(id="covalex", name="Covalex"))
    assert renderer.render_key(other, "d") != "OVERRIDDEN"


def test_template_dir_overrides_builtin(tmp_path):
    (tmp_path / "desc.j2").write_text("FROM DISK")
    renderer = Renderer(template_dir=tmp_path)
    assert renderer.render_key(make_contract(), "d") == "FROM DISK"


# --- against real data -------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    if not (SAMPLES / "contracts.ini").exists():
        pytest.skip("real sample not present")
    return contracts_ini.load(SAMPLES / "contracts.ini")


def test_real_corpus_renders_every_key_without_errors(real):
    result = Renderer().render_all(real)
    assert result.skipped == []
    assert len(result.values) == sum(len(c.all_keys()) for c in real.contracts)


def test_real_corpus_output_is_all_valid(real):
    result = Renderer().render_all(real)
    errors = [
        (key, issue)
        for key, value in result.values.items()
        for issue in validate_value(value)
        if issue.severity is Severity.ERROR
    ]
    assert errors == []


def test_real_corpus_reaches_parity_on_most_entries(real):
    """Defaults reproduce StarStrings closely; the residue is deliberate
    (blank-line spacing, an added MG Scrip line, and reward data propagated to
    every alternate phrasing rather than just the first)."""
    result = Renderer().render_all(real)

    matching = sum(
        1
        for c in real.contracts
        for key in c.all_keys()
        if result.values.get(key, "").rstrip("\\n")
        == (c.text(key) or "").rstrip("\\n").rstrip()
    )
    assert matching / len(result.values) > 0.70


def test_real_foxwell_bombing_run_matches_exactly(real):
    """A hand-checked contract, byte for byte."""
    contract = next(c for c in real.contracts if c.id == "Foxwell_bombingrun_VE")
    rendered = Renderer().render(contract)

    for key in contract.all_keys():
        assert rendered[key] == contract.text(key).rstrip("\\n")
