import copy

from starcompanion.regression import aggregate_snapshot
from test_cache import sample_set


def test_aggregate_snapshot_contains_no_extracted_text_and_is_deterministic():
    contracts = sample_set()
    first = aggregate_snapshot(contracts, game_version="synthetic")
    changed_text = copy.deepcopy(contracts)
    changed_text.contracts[0].texts["t"] = "Different extracted prose"
    changed_text.contracts[0].base_texts["t"] = "Different extracted prose"
    second = aggregate_snapshot(changed_text, game_version="synthetic")

    assert first == second
    assert "Different extracted prose" not in repr(second)
    assert first["placeholder_reward_labels"] == 0
    assert len(first["contract_identity_sha256"]) == 64
    assert len(first["reward_shape_sha256"]) == 64
