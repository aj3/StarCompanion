import json

import pytest

from starcompanion.cache import dumps, loads
from starcompanion.ini import LocalizationFile
from starcompanion.legacy_pack import (
    DIRECT_PRESENTATION_RULES,
    PRESENTATION_PROVIDER,
    PROVIDER,
    SIGNATURES,
    SOURCE_COMMIT,
    SUPPORTED_BUILD,
    attach_legacy_mining_pack,
    attach_legacy_presentation_pack,
    _group_mining_compendium,
)
from starcompanion.model import ContractSet, ProviderStatus
from starcompanion.render import Renderer, RenderOptions


def stock(*, drift: str | None = None) -> LocalizationFile:
    return LocalizationFile.loads(
        "\n".join(
            f"mineabletype_primary_{name}="
            f"{('Changed by build' if drift == name else expected)}"
            for name, expected, _signature in SIGNATURES
        )
    )


def presentation_stock(*, drift: str | None = None) -> LocalizationFile:
    return LocalizationFile.loads(
        "\n".join(
            f"{key}={'Changed by build' if drift == rule_id else expected}"
            for rule_id, key, expected, _replacement, _source in DIRECT_PRESENTATION_RULES
        )
    )


def test_exact_build_pack_is_default_off_and_keeps_numeric_source_provenance():
    contracts = attach_legacy_mining_pack(
        ContractSet(), stock(), SUPPORTED_BUILD
    )

    assert len(contracts.legacy_signatures) == len(SIGNATURES) == 26
    assert Renderer().render_all(contracts).values == {}
    rendered = Renderer(
        RenderOptions(legacy_mining_pack_enabled=True)
    ).render_all(contracts)
    assert rendered.values["mineabletype_primary_agricium"] == "Agricium (RS 3885)"
    evidence = rendered.provenance["mineabletype_primary_agricium"][0]
    assert evidence.provider == PROVIDER
    assert evidence.value == 3885
    assert SOURCE_COMMIT in evidence.record_path


def test_pack_fails_closed_for_unreviewed_build_and_stock_drift():
    unsupported = attach_legacy_mining_pack(ContractSet(), stock(), "next-build")
    assert not unsupported.legacy_signatures
    assert unsupported.capabilities[-1].status is ProviderStatus.UNAVAILABLE
    assert dict(unsupported.capabilities[-1].unmatched_reason_counts) == {
        "unsupported-build": 26
    }

    drifted = attach_legacy_mining_pack(
        ContractSet(), stock(drift="agricium"), SUPPORTED_BUILD
    )
    assert len(drifted.legacy_signatures) == 25
    assert drifted.capabilities[-1].status is ProviderStatus.DEGRADED
    assert dict(drifted.capabilities[-1].unmatched_reason_counts) == {
        "stock-value-drift": 1
    }


def test_pack_round_trips_through_cache_with_shared_evidence():
    original = attach_legacy_mining_pack(ContractSet(), stock(), SUPPORTED_BUILD)
    attach_legacy_presentation_pack(original, presentation_stock(), SUPPORTED_BUILD)
    restored = loads(dumps(original))

    assert restored.legacy_signatures == original.legacy_signatures
    assert restored.legacy_presentations == original.legacy_presentations
    assert restored.capabilities == original.capabilities

    damaged = json.loads(dumps(original))
    damaged["legacy_signatures"][0]["evidence_ids"] = [999]
    with pytest.raises(ValueError, match="invalid cached presentation"):
        loads(json.dumps(damaged))

    damaged = json.loads(dumps(original))
    damaged["legacy_signatures"][0]["signature"] = "3885"
    with pytest.raises(ValueError, match="invalid cached presentation"):
        loads(json.dumps(damaged))

    damaged = json.loads(dumps(original))
    damaged["legacy_presentations"][0]["base_text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="invalid cached data"):
        loads(json.dumps(damaged))


def test_exact_legacy_wording_rules_are_default_off_and_fail_closed_on_drift():
    contracts = attach_legacy_presentation_pack(
        ContractSet(), presentation_stock(), SUPPORTED_BUILD
    )

    assert len(contracts.legacy_presentations) == len(DIRECT_PRESENTATION_RULES)
    assert contracts.capabilities[-1].provider == PRESENTATION_PROVIDER
    assert contracts.capabilities[-1].status is ProviderStatus.DEGRADED
    assert dict(contracts.capabilities[-1].unmatched_reason_counts) == {
        "localization-missing": 2
    }
    assert Renderer().render_all(contracts).values == {}
    rendered = Renderer(
        RenderOptions(legacy_mining_pack_enabled=True)
    ).render_all(contracts)
    assert rendered.values["items_commodities_hephaestanite_raw"] == "Heph (Raw)"
    assert rendered.values["items_commodities_maze"] == "<EM3>[!]</EM3> Maze"
    assert rendered.values["item_Namegrin_multitool_01_tractorbeam"] == "Tractorbeam"
    assert len(rendered.provenance["items_commodities_maze"]) == 2

    drifted = attach_legacy_presentation_pack(
        ContractSet(), presentation_stock(drift="illegal-maze"), SUPPORTED_BUILD
    )
    assert len(drifted.legacy_presentations) == len(DIRECT_PRESENTATION_RULES) - 1
    assert dict(drifted.capabilities[-1].unmatched_reason_counts) == {
        "localization-missing": 2,
        "stock-value-drift": 1,
    }

    unsupported = attach_legacy_presentation_pack(
        ContractSet(), presentation_stock(), "next-build"
    )
    assert not unsupported.legacy_presentations
    assert dict(unsupported.capabilities[-1].unmatched_reason_counts) == {
        "unsupported-build": len(DIRECT_PRESENTATION_RULES) + 2
    }


def test_mining_compendium_transform_reuses_only_complete_local_stock_entries():
    groups = (
        ("Legendary", ("Quantainium", "Savrilium", "Stileron")),
        ("Epic", ("Lindinium", "Ouratite", "Riccite")),
        ("Rare", ("Beryl", "Bexalite", "Borase", "Gold", "Taranite")),
        ("Uncommon", ("Agricium", "Aslarite", "Laranite", "Titanium", "Torite", "Tungsten")),
        ("Common", ("Aluminium", "Copper", "Corundum", "Hephaestanite", "Ice", "Iron", "Quartz", "Silicon", "Tin")),
        ("Hand Mineables", ("Aphorite", "Beradon", "Caranite", "Dolivine", "Feynmaline", "Glacosite", "Hadanite", "Janalite", "Sadaryx")),
    )
    names = [name for _label, values in groups for name in reversed(values)]
    source = "Intro" + "".join(f"\\n\\n{name} - local detail" for name in names)

    rendered = _group_mining_compendium(source)
    assert rendered is not None
    assert rendered.startswith("Intro\\n\\n** Legendary **")
    assert rendered.count("local detail") == len(names)
    assert _group_mining_compendium(source + "\\n\\nUnknown - value") is None
