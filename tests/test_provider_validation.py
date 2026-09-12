from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from starcompanion.extract import datacore
from starcompanion.extract.entities import entity_providers
from starcompanion.extract.mission_tactical import mission_tactical_providers
from starcompanion.extract.provider_validation import (
    aggregate_provider_validation,
    assert_redacted_snapshot,
    snapshot_json,
)
from test_dataforge import SyntheticDataCore
from test_entities import specialized_fixture


SNAPSHOTS = Path(__file__).with_name("g5_provider_snapshots.json")


def test_aggregate_snapshot_runs_every_provider_without_record_content():
    source = specialized_fixture()
    snapshot = aggregate_provider_validation(
        source,
        channel="LIVE",
        build_version="synthetic-1",
    )

    assert snapshot["redaction"] == "aggregate-only"
    assert set(snapshot["entities"]) == {
        provider.spec.provider for provider in entity_providers()
    }
    assert set(snapshot["mission_tactical"]) == {
        provider.spec.provider for provider in mission_tactical_providers()
    }
    encoded = snapshot_json(snapshot)
    assert "10000000-0000-0000-0000-000000000001" not in encoded
    assert "Data/Libs/Foundry" not in encoded
    assert "@item_name" not in encoded


def test_redaction_gate_rejects_ids_paths_and_localization_values():
    for value in (
        "10000000-0000-0000-0000-000000000001",
        r"C:\\Users\\player\\Game2.dcb",
        "/libs/foundry/records/entities/item.xml",
        "@Item_Name",
    ):
        with pytest.raises(ValueError, match="non-aggregate"):
            assert_redacted_snapshot({"leak": value})


def test_archive_validation_scope_is_channel_isolated():
    source = SyntheticDataCore([])

    hotfix = aggregate_provider_validation(
        source,
        channel="hotfix",
        build_version="synthetic-2",
    )
    assert hotfix["channel"] == "HOTFIX"
    with pytest.raises(ValueError, match="LIVE and HOTFIX"):
        aggregate_provider_validation(
            source,
            channel="PTU",
            build_version="synthetic-2",
        )


def test_reviewed_provider_snapshots_are_aggregate_only_and_complete():
    snapshots = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))

    for channels in snapshots.values():
        for snapshot in channels.values():
            assert_redacted_snapshot(snapshot)
            assert set(snapshot["entities"]) == {
                provider.spec.provider for provider in entity_providers()
            }
            assert set(snapshot["mission_tactical"]) == {
                provider.spec.provider for provider in mission_tactical_providers()
            }


@pytest.mark.skipif(
    not os.environ.get("STARCOMPANION_PROVIDER_DCB"),
    reason="set STARCOMPANION_PROVIDER_DCB for opt-in local regression",
)
def test_local_provider_snapshot_matches_reviewed_build():
    path = Path(os.environ["STARCOMPANION_PROVIDER_DCB"])
    channel = os.environ.get("STARCOMPANION_PROVIDER_CHANNEL", "LIVE").upper()
    version = os.environ.get("STARCOMPANION_PROVIDER_VERSION")
    if not version:
        pytest.fail("STARCOMPANION_PROVIDER_VERSION is required")
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    if version not in expected or channel not in expected[version]:
        pytest.skip(f"no reviewed provider snapshot for {channel} {version}")

    actual = aggregate_provider_validation(
        datacore.load(path),
        channel=channel,
        build_version=version,
    )

    assert actual == expected[version][channel]
