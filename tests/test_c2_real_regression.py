import json
import os
from pathlib import Path

import pytest

from starcompanion import cache
from starcompanion.install import identify
from starcompanion.operations import read_contracts
from starcompanion.regression import aggregate_snapshot


SNAPSHOTS = Path(__file__).with_name("real_build_snapshots.json")
REAL_CACHE = os.environ.get("STARCOMPANION_REAL_CACHE")
REAL_INSTALL = os.environ.get("STARCOMPANION_REAL_INSTALL")


@pytest.mark.skipif(
    not (REAL_CACHE or REAL_INSTALL),
    reason="set STARCOMPANION_REAL_CACHE or STARCOMPANION_REAL_INSTALL",
)
def test_real_build_aggregate_snapshot_matches_reviewed_counts_and_hashes():
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    if REAL_CACHE:
        contracts = cache.load(Path(REAL_CACHE))
        version = os.environ.get("STARCOMPANION_REAL_VERSION")
        if not version:
            pytest.fail("STARCOMPANION_REAL_VERSION is required with a real cache")
    else:
        install = identify(Path(REAL_INSTALL))
        contracts = read_contracts(install)
        version = install.version

    if version not in expected:
        pytest.skip(f"no reviewed aggregate snapshot for build {version}")

    assert aggregate_snapshot(contracts, game_version=version) == expected[version]
