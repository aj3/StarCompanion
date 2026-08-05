import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from starcompanion.config import (
    SCHEMA_VERSION,
    Profile,
    UnsupportedProfileVersion,
    builtin_profiles,
    load_builtin,
)
from starcompanion.inject import MergeMode
from starcompanion.model import Contract, ContractSet, Org, Reward, StringKind
from starcompanion.sources import contracts_ini

SAMPLES = Path(__file__).parent / "samples"


def make_contract() -> Contract:
    base = "Do the thing."
    return Contract(
        id="Foxwell_Test",
        org=Org(id="foxwell", name="Foxwell"),
        family="Test",
        keys={StringKind.TITLE: ["t"], StringKind.DESC: ["d"]},
        texts={"t": base, "d": base},
        base_texts={"t": base, "d": base},
        reward=Reward(reputation=[100], scrip=True),
    )


# --- persistence -------------------------------------------------------------


def test_defaults_round_trip_losslessly():
    profile = Profile()
    assert Profile.loads(profile.dumps()) == profile


def test_customised_profile_round_trips_losslessly():
    profile = Profile.model_validate(
        {
            "name": "custom",
            "description": "hand tuned",
            "fields": {"scrip": False, "caveats": False},
            "formatting": {
                "emphasis": "EM3",
                "by_field": {"reputation": "EM4", "gates": "EM"},
                "max_pool_items": 5,
                "title": {"prefix": "org_rank", "bracket_bp": False},
            },
            "templates": {"foxwell": {"title": "{{ base }}!"}},
            "injection": {"mode": "overwrite", "backup": False},
        }
    )
    assert Profile.loads(profile.dumps()) == profile


def test_save_and_load_via_disk(tmp_path):
    path = tmp_path / "p.json"
    profile = Profile(name="disk")
    profile.save(path)
    assert Profile.load(path) == profile


def test_saved_json_is_human_editable(tmp_path):
    path = tmp_path / "p.json"
    Profile().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert path.read_text(encoding="utf-8").endswith("\n")


# --- versioning --------------------------------------------------------------


def test_newer_schema_version_is_rejected_with_guidance():
    with pytest.raises(UnsupportedProfileVersion, match="Upgrade StarCompanion"):
        Profile.loads('{"schema_version": 99}')


def test_unsupported_older_schema_version_is_rejected_with_guidance():
    with pytest.raises(UnsupportedProfileVersion, match="newer profile"):
        Profile.loads('{"schema_version": 0}')


def test_v1_profile_migrates_to_structured_mode():
    profile = Profile.loads('{"schema_version": 1, "name": "legacy"}')

    assert profile.schema_version == SCHEMA_VERSION
    assert profile.wording.mode == "structured"


def test_v1_profile_with_templates_preserves_them_in_advanced_mode():
    profile = Profile.loads(
        '{"schema_version": 1, "templates": {"foxwell": {"title": "CUSTOM"}}}'
    )

    assert profile.wording.mode == "advanced"
    assert profile.templates["foxwell"].title == "CUSTOM"


def test_missing_schema_version_assumes_current():
    assert Profile.loads('{"name": "x"}').schema_version == SCHEMA_VERSION


def test_duplicate_profile_keys_are_rejected():
    with pytest.raises(ValueError, match="duplicate profile key 'mode'"):
        Profile.loads(
            '{"wording": {"mode": "structured", "mode": "advanced"}}'
        )


def test_profile_json_must_be_an_object():
    with pytest.raises(ValueError, match="must contain one object"):
        Profile.loads("[]")


# --- validation --------------------------------------------------------------


def test_unrenderable_emphasis_tag_is_rejected():
    with pytest.raises(ValidationError, match="not renderable in-game"):
        Profile.model_validate({"formatting": {"emphasis": "span"}})


def test_void_tag_is_not_a_valid_emphasis():
    # <None> cannot wrap text, so it is not a legal emphasis choice.
    with pytest.raises(ValidationError):
        Profile.model_validate({"formatting": {"emphasis": "None"}})


def test_unknown_emphasis_field_is_rejected():
    with pytest.raises(ValidationError, match="unknown field"):
        Profile.model_validate({"formatting": {"by_field": {"nonsense": "EM4"}}})


def test_bad_tag_inside_by_field_is_rejected():
    with pytest.raises(ValidationError, match="not renderable in-game"):
        Profile.model_validate({"formatting": {"by_field": {"reputation": "blink"}}})


def test_unknown_title_prefix_is_rejected():
    with pytest.raises(ValidationError):
        Profile.model_validate({"formatting": {"title": {"prefix": "sideways"}}})


def test_unknown_key_is_rejected_rather_than_silently_ignored():
    with pytest.raises(ValidationError):
        Profile.model_validate({"fields": {"reputaiton": True}})


def test_max_pool_items_must_be_positive():
    with pytest.raises(ValidationError):
        Profile.model_validate({"formatting": {"max_pool_items": 0}})


@pytest.mark.parametrize(
    "label",
    ["", " padded", "ends:", "bad<tag>", r"bad\nline", "line\u2028break", "safe\u202eevil"],
)
def test_unsafe_structured_wording_label_is_rejected(label):
    with pytest.raises(ValidationError, match="wording labels"):
        Profile.model_validate({"wording": {"labels": {"reputation": label}}})


@pytest.mark.parametrize(
    "order",
    [
        ["reputation", "scrip", "items", "scenario"],
        ["reputation", "scrip", "items", "scenario", "scenario"],
    ],
)
def test_incomplete_or_duplicate_section_order_is_rejected(order):
    with pytest.raises(ValidationError, match="every reward section exactly once"):
        Profile.model_validate({"wording": {"section_order": order}})


def test_unknown_org_is_reported_against_real_data():
    contracts = ContractSet(orgs={"foxwell": Org(id="foxwell", name="Foxwell")})
    profile = Profile.model_validate({"templates": {"nosuchorg": {"title": "x"}}})

    problems = profile.validate_against(contracts)
    assert len(problems) == 1 and "nosuchorg" in problems[0]


def test_known_org_passes_validation():
    contracts = ContractSet(orgs={"foxwell": Org(id="foxwell", name="Foxwell")})
    profile = Profile.model_validate({"templates": {"foxwell": {"title": "x"}}})
    assert profile.validate_against(contracts) == []


# --- effect on rendering -----------------------------------------------------


def test_field_toggles_change_rendered_output():
    contract = make_contract()

    full = Profile().build_renderer().render_key(contract, "d")
    assert "Reputation Awarded" in full and "MG Scrip" in full

    trimmed = (
        Profile.model_validate({"fields": {"reputation": False, "scrip": False}})
        .build_renderer()
        .render_key(contract, "d")
    )
    assert "Reputation Awarded" not in trimmed and "MG Scrip" not in trimmed


def test_per_field_emphasis_is_applied():
    profile = Profile.model_validate(
        {"formatting": {"emphasis": "EM4", "by_field": {"scrip": "EM"}}}
    )
    value = profile.build_renderer().render_key(make_contract(), "d")

    assert "<EM4>Reputation Awarded:</EM4>" in value
    assert "<EM>MG Scrip:</EM>" in value


def test_title_prefix_flows_through_from_profile():
    profile = Profile.model_validate({"formatting": {"title": {"prefix": "org"}}})
    assert profile.build_renderer().render_key(make_contract(), "t").startswith("[Foxwell]")


def test_inline_org_template_is_used():
    profile = Profile.model_validate(
        {"templates": {"foxwell": {"title": "CUSTOM {{ base }}"}}}
    )
    assert profile.build_renderer().render_key(make_contract(), "t") == "CUSTOM Do the thing."


def test_inline_template_applies_only_to_its_org():
    profile = Profile.model_validate({"templates": {"covalex": {"title": "CUSTOM"}}})
    assert profile.build_renderer().render_key(make_contract(), "t") != "CUSTOM"


def test_structured_mode_keeps_stored_templates_inactive():
    profile = Profile.model_validate(
        {
            "wording": {"mode": "structured"},
            "templates": {"foxwell": {"title": "CUSTOM"}},
        }
    )

    assert profile.build_renderer().render_key(make_contract(), "t") != "CUSTOM"
    profile.wording.mode = "advanced"
    assert profile.build_renderer().render_key(make_contract(), "t") == "CUSTOM"


def test_injection_mode_maps_to_merge_mode():
    assert Profile().injection.merge_mode is MergeMode.MERGE
    overwrite = Profile.model_validate({"injection": {"mode": "overwrite"}})
    assert overwrite.injection.merge_mode is MergeMode.OVERWRITE


# --- shipped profiles --------------------------------------------------------


def test_builtin_profiles_are_present():
    assert set(builtin_profiles()) >= {"default", "minimal", "rank-first"}


@pytest.mark.parametrize("name", ["default", "minimal", "rank-first"])
def test_builtin_profiles_load_and_round_trip(name):
    profile = load_builtin(name)
    assert profile.name == name
    assert profile.description
    assert Profile.loads(profile.dumps()) == profile


def test_unknown_builtin_raises_with_options():
    with pytest.raises(KeyError, match="default"):
        load_builtin("nope")


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists(), reason="real sample not present"
)
def test_builtin_profiles_render_real_corpus_without_errors():
    contracts = contracts_ini.load(SAMPLES / "contracts.ini")

    for name in ("default", "minimal", "rank-first"):
        profile = load_builtin(name)
        assert profile.validate_against(contracts) == []

        result = profile.build_renderer().render_all(contracts)
        assert result.skipped == [], f"{name} produced unusable values"
        assert len(result.values) == sum(len(c.all_keys()) for c in contracts.contracts)


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists(), reason="real sample not present"
)
def test_profiles_produce_materially_different_output():
    contracts = contracts_ini.load(SAMPLES / "contracts.ini")
    rendered = {
        name: load_builtin(name).build_renderer().render_all(contracts).values
        for name in ("default", "minimal", "rank-first")
    }

    assert rendered["default"] != rendered["minimal"]
    assert rendered["default"] != rendered["rank-first"]
