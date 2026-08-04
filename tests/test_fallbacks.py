import json

import pytest

from starcompanion import cache
from starcompanion.__main__ import EXIT_OK, main
from starcompanion.enhancements import MissionEnhancementProvider, apply_enhancements
from starcompanion.extract.dataforge import (
    BlueprintPoolFacts,
    CapabilityReport,
    CapabilityStatus,
    MissionExtractionResult,
    MissionFacts,
)
from starcompanion.fallbacks import (
    FallbackDocument,
    FallbackError,
    apply_to_localization,
    record_usage,
    template_from_contracts,
)
from starcompanion.ini import BOM, LocalizationFile
from starcompanion.model import (
    ContractSet,
    ProviderCapability,
    ProviderStatus,
    UnresolvedLocalization,
)
from starcompanion.sources import game_strings


def mission_result() -> MissionExtractionResult:
    fact = MissionFacts(
        mission_id="mission-1",
        title_keys=("Missing_Title",),
        description_keys=("Missing_Desc",),
        blueprint_pools=(BlueprintPoolFacts("pool", ("Reward_Name",), 1.0),),
    )
    report = CapabilityReport(
        "dataforge-mission-facts",
        CapabilityStatus.AVAILABLE,
        8,
        1,
        1,
    )
    return MissionExtractionResult((fact,), report)


def document(**values: str) -> FallbackDocument:
    unresolved = UnresolvedLocalization(
        "mission-1",
        "localization-missing",
        ("Missing_Title", "Missing_Desc"),
    )
    return FallbackDocument("test-build", "english", (unresolved,), values)


def test_template_exports_only_authorable_missing_keys():
    capability = ProviderCapability(
        "local-dataforge-missions",
        "3",
        ProviderStatus.AVAILABLE,
        "8",
        unresolved_localizations=(
            UnresolvedLocalization(
                "missing",
                "localization-missing",
                ("Missing_Title", "Missing_Desc"),
            ),
            UnresolvedLocalization(
                "placeholder",
                "placeholder-key",
                ("LOC_UNINITIALIZED",),
            ),
        ),
    )
    template = template_from_contracts(
        ContractSet(capabilities=[capability]),
        game_version="test-build",
        language="english",
    )

    assert template.values == {"Missing_Title": "", "Missing_Desc": ""}
    assert [item.source_id for item in template.unresolved] == ["missing"]


def test_fallback_document_rejects_values_outside_structured_metadata(tmp_path):
    path = tmp_path / "fallbacks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game_version": "test-build",
                "language": "english",
                "unresolved": [
                    {
                        "source_id": "mission-1",
                        "reason": "localization-missing",
                        "keys": ["Missing_Title", "Missing_Desc"],
                    }
                ],
                "values": {"Invented_Key": "Invented text"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FallbackError, match="not in unresolved metadata"):
        FallbackDocument.load(path)


def test_context_rejects_cross_build_and_cross_language_reuse():
    fallback = document()
    with pytest.raises(FallbackError, match="build"):
        fallback.validate_context(game_version="other-build", language="english")
    with pytest.raises(FallbackError, match="language"):
        fallback.validate_context(game_version="test-build", language="french")


def test_contextual_application_requires_complete_exact_missing_group():
    strings = LocalizationFile.loads(BOM + "Reward_Name=Reward\n")
    with pytest.raises(FallbackError, match="incomplete"):
        apply_to_localization(
            strings,
            mission_result(),
            document(Missing_Title="My title"),
        )
    assert strings.get("Missing_Title") is None


def test_user_text_creates_contract_reward_and_provenance_without_guessing():
    strings = LocalizationFile.loads(BOM + "Reward_Name=Reward\n")
    fallback = document(
        Missing_Title="My authored title",
        Missing_Desc="My authored description.",
    )
    applied = apply_to_localization(strings, mission_result(), fallback)
    groups = (
        game_strings.MissionKeyEvidence(
            ("Missing_Title",),
            ("Missing_Desc",),
        ),
    )
    contracts = game_strings.parse(strings, evidenced_groups=groups)
    enhancement = MissionEnhancementProvider(strings.get).build(mission_result())
    contracts = apply_enhancements(contracts, [enhancement])
    record_usage(contracts, applied, game_version="test-build")

    assert len(contracts.contracts) == 1
    contract = contracts.contracts[0]
    assert contract.texts["Missing_Title"] == "My authored title"
    assert contract.reward.blueprint_pools[0].items == ["Reward"]
    assert {item.provider for item in contract.evidence} == {
        "user-localization-fallbacks"
    }
    assert contracts.capabilities[0].unmatched_facts == 0
    assert contracts.capabilities[1].provider == "user-localization-fallbacks"
    assert contracts.capabilities[1].matched_facts == 1
    assert contracts.capabilities[1].reward_facts == 1


def test_cli_template_and_validate_round_trip_structured_cache(tmp_path, capsys):
    contracts = ContractSet(
        capabilities=[
            ProviderCapability(
                "local-dataforge-missions",
                "3",
                ProviderStatus.AVAILABLE,
                "8",
                unresolved_localizations=(
                    UnresolvedLocalization(
                        "mission-1",
                        "localization-missing",
                        ("Missing_Title", "Missing_Desc"),
                    ),
                ),
            )
        ]
    )
    cache_path = tmp_path / "cache.json"
    output = tmp_path / "fallbacks.json"
    cache.save(contracts, cache_path, source="game:LIVE:test-build")

    assert main([
        "fallbacks", "template", "--cache", str(cache_path), "--out", str(output)
    ]) == EXIT_OK
    assert main(["fallbacks", "validate", "--file", str(output)]) == EXIT_OK

    raw = json.loads(output.read_text(encoding="utf-8"))
    assert raw["game_version"] == "test-build"
    assert raw["values"] == {"Missing_Desc": "", "Missing_Title": ""}
    assert "no text was generated" in capsys.readouterr().out
