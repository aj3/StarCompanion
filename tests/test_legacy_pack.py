import json

import pytest

from starcompanion.cache import dumps, loads
from starcompanion.ini import LocalizationFile
from starcompanion.legacy_pack import (
    PROVIDER,
    SIGNATURES,
    SOURCE_COMMIT,
    SUPPORTED_BUILD,
    attach_legacy_mining_pack,
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
    restored = loads(dumps(original))

    assert restored.legacy_signatures == original.legacy_signatures
    assert restored.capabilities == original.capabilities

    damaged = json.loads(dumps(original))
    damaged["legacy_signatures"][0]["evidence_ids"] = [999]
    with pytest.raises(ValueError, match="invalid cached presentation"):
        loads(json.dumps(damaged))

    damaged = json.loads(dumps(original))
    damaged["legacy_signatures"][0]["signature"] = "3885"
    with pytest.raises(ValueError, match="invalid cached presentation"):
        loads(json.dumps(damaged))
