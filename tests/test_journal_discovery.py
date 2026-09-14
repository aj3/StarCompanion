from starcompanion.extract.dataforge import DataForgeIndex
from starcompanion.extract.entities import (
    EntityCatalogResult,
    extract_entity_catalog,
    runtime_entity_providers,
)
from starcompanion.ini import LocalizationFile
from starcompanion.journal_discovery import (
    DISCOVERY_PROVIDER,
    JOURNAL_RESOLUTION_PROVIDER,
    attach_journal_discovery,
    build_journal_discovery,
)
from starcompanion.model import ContractSet, ProviderStatus
from test_entities import SPECIALIZED_IDS, specialized_fixture


def _strings(*, include_ship=True, include_body=True):
    values = {
        "item_name_test": "Power plant",
        "item_name_fps_rifle": "Rifle",
        "item_name_medpen": "Medpen",
        "commodity_name_ore": "Ore",
        "recipe_name_laser": "Laser recipe",
        "vehicle_name_test": "Test vehicle",
        "journal_title_cave": "Cave discovery",
    }
    if include_ship:
        values["item_name_ship_laser"] = "Ship laser"
    if include_body:
        values["journal_body_cave"] = "Local journal body."
    return LocalizationFile.loads(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    )


def _catalog(source=None):
    return extract_entity_catalog(
        DataForgeIndex(source or specialized_fixture()),
        providers=runtime_entity_providers(),
    )


def test_journal_resolution_uses_only_selected_local_language_text():
    result = build_journal_discovery(_catalog(), _strings(include_ship=False))

    assert len(result.journals) == 1
    journal = result.journals[0]
    assert journal.record_id == SPECIALIZED_IDS["journal"]
    assert journal.title == "Cave discovery"
    assert journal.body == "Local journal body."
    assert journal.category == "Discovery"
    assert journal.discovery_tag == "CAVE"
    assert {item.field_path for item in journal.evidence} == {
        "$.title",
        "$.body",
        "$.entryCategory",
        "$.discoveryTag",
    }
    assert [(item.localization_key, item.role) for item in result.discoveries] == [
        ("item_name_ship_laser", "name")
    ]
    capabilities = {item.provider: item for item in result.capabilities}
    assert capabilities[JOURNAL_RESOLUTION_PROVIDER].status is ProviderStatus.AVAILABLE
    assert capabilities[DISCOVERY_PROVIDER].unmatched_reason_counts == (
        ("localization-missing", 1),
    )


def test_missing_journal_body_is_reported_without_inventing_text():
    result = build_journal_discovery(_catalog(), _strings(include_body=False))

    assert not result.journals
    capability = next(
        item for item in result.capabilities if item.provider == JOURNAL_RESOLUTION_PROVIDER
    )
    assert capability.status is ProviderStatus.DEGRADED
    assert capability.unmatched_reason_counts == (("body-localization-missing", 1),)


def test_shared_journal_localization_is_suppressed_as_ambiguous():
    source = specialized_fixture()
    source.entries.append(
        (
            "Journal.Other",
            "Data/Libs/Foundry/Records/Journal/Entries/other.xml",
            "10000000-0000-0000-0000-000000000099",
            {
                "title": "@journal_title_cave",
                "body": "@journal_body_cave",
                "entryCategory": "Lore",
                "discoveryTag": "OTHER",
            },
        )
    )
    source.__post_init__()

    result = build_journal_discovery(_catalog(source), _strings())

    assert not result.journals
    capability = next(
        item for item in result.capabilities if item.provider == JOURNAL_RESOLUTION_PROVIDER
    )
    assert capability.status is ProviderStatus.DEGRADED
    assert capability.unmatched_reason_counts == (
        ("shared-localization-ambiguous", 2),
    )
    assert capability.diagnostics == (
        "warning:shared-journal-localization-suppressed",
    )


def test_discovery_limit_is_bounded_and_degrades_only_discovery_provider():
    result = build_journal_discovery(_catalog(), LocalizationFile.loads(""), max_discoveries=2)

    assert len(result.discoveries) == 2
    capabilities = {item.provider: item for item in result.capabilities}
    assert capabilities[DISCOVERY_PROVIDER].status is ProviderStatus.DEGRADED
    assert capabilities[DISCOVERY_PROVIDER].diagnostics == (
        "warning:localization-discovery-limit-reached",
    )
    assert capabilities[JOURNAL_RESOLUTION_PROVIDER].status is ProviderStatus.DEGRADED


def test_missing_catalog_is_unavailable_and_attach_deduplicates_capabilities():
    contracts = ContractSet()
    first = attach_journal_discovery(
        contracts,
        EntityCatalogResult(()),
        LocalizationFile.loads(""),
    )
    attach_journal_discovery(
        contracts,
        EntityCatalogResult(()),
        LocalizationFile.loads(""),
    )

    assert not first.journals
    assert not first.discoveries
    assert all(item.status is ProviderStatus.UNAVAILABLE for item in first.capabilities)
    assert len(contracts.capabilities) == len(first.capabilities) == 3


def test_invalid_discovery_bounds_fail_closed():
    for value in (0, 50_001):
        try:
            build_journal_discovery(_catalog(), _strings(), max_discoveries=value)
        except ValueError as exc:
            assert "between 1 and 50,000" in str(exc)
        else:
            raise AssertionError("invalid discovery limit was accepted")
