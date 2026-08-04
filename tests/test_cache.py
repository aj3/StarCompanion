from pathlib import Path
import json
import copy

import pytest
from io import StringIO

from starcompanion import cache
from starcompanion.cache import UnsupportedCacheVersion
from starcompanion.model import (
    BlueprintPool,
    Contract,
    ContractSet,
    Difficulty,
    Evidence,
    Gate,
    GateKind,
    Org,
    ProviderCapability,
    ProviderStatus,
    Reward,
    ScenarioPoints,
    StringKind,
)
from starcompanion.sources import contracts_ini

SAMPLES = Path(__file__).parent / "samples"


def sample_set() -> ContractSet:
    org = Org(id="foxwell", name="Foxwell", rank_ladder=["Neutral", "Contractor"])
    contract = Contract(
        id="Foxwell_Test_VE",
        org=org,
        family="Test",
        difficulty=Difficulty.VERY_EASY,
        keys={StringKind.TITLE: ["t"], StringKind.DESC: ["d_001", "d_002"]},
        texts={"t": "Title", "d_001": "First", "d_002": "Second"},
        base_texts={"t": "Title", "d_001": "First", "d_002": "Second"},
        reward=Reward(
            reputation=[100, -50],
            scenario_points=[ScenarioPoints(120000, split=True)],
            scrip=True,
            blueprint_pools=[
                BlueprintPool(
                    items=["Aves Core"],
                    item_ids={"Aves Core": "11111111-1111-1111-1111-111111111111"},
                    item_categories={"Aves Core": "armor"},
                    gates=[
                        Gate(GateKind.FACTION, "BitZeros"),
                        Gate(GateKind.RANK, "Neutral"),
                    ],
                    label="Pool 1",
                    example_locations=["Ruin Station"],
                    caveat="Warning: check scmdb",
                    chance=0.5,
                    owned={"Aves Core"},
                )
            ],
            item_rewards=["Novikov Undersuit"],
        ),
        evidence=[
            Evidence("local-test", "record", "path", "$.reward", 100)
        ],
    )
    return ContractSet(
        contracts=[contract],
        orgs={"foxwell": org},
        unparsed=[("x", "no reward")],
        capabilities=[
            ProviderCapability(
                "local-test", "1", ProviderStatus.AVAILABLE, "build", 1, 1, 1
            )
        ],
    )


def test_round_trip_preserves_everything():
    original = sample_set()
    assert cache.loads(cache.dumps(original)) == original


def test_round_trip_via_disk(tmp_path):
    original = sample_set()
    path = tmp_path / "cache.json"
    cache.save(original, path, source="test")
    assert cache.load(path) == original


def test_incremental_helper_transport_round_trips_without_whole_document():
    original = sample_set()
    stream = StringIO()

    cache.dump_lines(original, stream, source="helper-test")
    stream.seek(0)

    assert cache.load_lines(stream) == original
    assert len(stream.getvalue().splitlines()) == 6


def test_cache_interns_evidence_and_omits_duplicate_base_text():
    raw = json.loads(cache.dumps(sample_set()))

    assert len(raw["evidence"]) == 1
    assert raw["contracts"][0]["evidence_ids"] == [0]
    assert raw["contracts"][0]["base_texts"] == {}


def test_shared_evidence_is_interned_without_losing_per_contract_links():
    original = sample_set()
    second = copy.deepcopy(original.contracts[0])
    second.id = "Foxwell_Test_E"
    second.keys = {StringKind.TITLE: ["t2"], StringKind.DESC: ["d2"]}
    second.texts = {"t2": "Second", "d2": "Second body"}
    second.base_texts = dict(second.texts)
    original.contracts.append(second)

    raw = json.loads(cache.dumps(original))
    restored = cache.loads(json.dumps(raw))

    assert len(raw["evidence"]) == 1
    assert [item["evidence_ids"] for item in raw["contracts"]] == [[0], [0]]
    assert restored.contracts[0].evidence == restored.contracts[1].evidence


def test_ownership_survives_the_round_trip():
    """Dropping this silently loses SCMDB ownership marks between commands."""
    restored = cache.loads(cache.dumps(sample_set()))
    assert restored.contracts[0].reward.blueprint_pools[0].owned == {"Aves Core"}


def test_org_identity_is_shared_not_duplicated():
    restored = cache.loads(cache.dumps(sample_set()))
    assert restored.contracts[0].org is restored.orgs["foxwell"]


def test_unsupported_version_is_rejected_with_guidance():
    with pytest.raises(UnsupportedCacheVersion, match="starcompanion import"):
        cache.loads('{"cache_version": 99, "orgs": {}, "contracts": []}')


def test_missing_version_is_rejected():
    with pytest.raises(UnsupportedCacheVersion):
        cache.loads('{"orgs": {}, "contracts": []}')


def test_describe_reports_header(tmp_path):
    path = tmp_path / "cache.json"
    cache.save(sample_set(), path, source="contracts.ini:x")

    described = cache.describe(path)
    assert described["cache_version"] == cache.CACHE_VERSION
    assert described["source"] == "contracts.ini:x"
    assert described["contracts"] == 1
    assert described["generated"]


def test_non_ascii_text_survives(tmp_path):
    contracts = sample_set()
    contracts.contracts[0].texts["t"] = "Xi'an — Wikelo’s trade"
    path = tmp_path / "cache.json"
    cache.save(contracts, path)
    assert cache.load(path).contracts[0].texts["t"] == "Xi'an — Wikelo’s trade"


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists(), reason="real sample not present"
)
def test_real_corpus_round_trips(tmp_path):
    original = contracts_ini.load(SAMPLES / "contracts.ini")
    path = tmp_path / "cache.json"
    cache.save(original, path, source="contracts.ini")

    restored = cache.load(path)
    assert len(restored.contracts) == len(original.contracts)
    assert restored.orgs == original.orgs
    assert restored == original
