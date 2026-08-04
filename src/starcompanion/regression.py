"""Aggregate-only real-build regression snapshots.

Snapshots intentionally contain counts and hashes, never extracted CIG text.
They make classification and provider-coverage drift reviewable after a patch.
"""

from __future__ import annotations

import hashlib
import json

from .model import ContractSet


def aggregate_snapshot(
    contracts: ContractSet,
    *,
    game_version: str,
) -> dict[str, object]:
    provider = contracts.capabilities[0] if contracts.capabilities else None
    identity_rows = sorted(
        (
            contract.id,
            contract.org.id,
            contract.difficulty.code if contract.difficulty else None,
            tuple(sorted(contract.all_keys(), key=str.casefold)),
        )
        for contract in contracts.contracts
    )
    reward_rows = sorted(
        (
            contract.id,
            tuple(contract.reward.reputation),
            tuple(
                (pool.label, pool.chance, tuple(pool.items))
                for pool in contract.reward.blueprint_pools
            ),
            tuple(contract.reward.item_rewards),
        )
        for contract in contracts.contracts
        if not contract.reward.is_empty
    )
    all_evidence = [
        item for contract in contracts.contracts for item in contract.evidence
    ]
    reward_labels = [
        item
        for contract in contracts.contracts
        for pool in contract.reward.blueprint_pools
        for item in pool.items
    ] + [
        item
        for contract in contracts.contracts
        for item in contract.reward.item_rewards
    ]
    placeholder_values = {
        "loc_uninitialized",
        "loc_placeholder",
        "<= uninitialized =>",
        "<= placeholder =>",
    }
    return {
        "snapshot_version": 1,
        "game_version": game_version,
        "contracts": len(contracts.contracts),
        "orgs": len(contracts.orgs),
        "keys": sum(len(contract.all_keys()) for contract in contracts.contracts),
        "enhanced_contracts": sum(
            not contract.reward.is_empty for contract in contracts.contracts
        ),
        "reputation_contracts": sum(
            bool(contract.reward.reputation) for contract in contracts.contracts
        ),
        "blueprint_contracts": sum(
            bool(contract.reward.blueprint_pools) for contract in contracts.contracts
        ),
        "item_reward_contracts": sum(
            bool(contract.reward.item_rewards) for contract in contracts.contracts
        ),
        "blueprint_items": sum(
            len(pool.items)
            for contract in contracts.contracts
            for pool in contract.reward.blueprint_pools
        ),
        "direct_items": sum(
            len(contract.reward.item_rewards) for contract in contracts.contracts
        ),
        "placeholder_reward_labels": sum(
            label.strip().casefold() in placeholder_values for label in reward_labels
        ),
        "evidence_links": len(all_evidence),
        "unique_evidence": len(set(all_evidence)),
        "provider": (
            {
                "id": provider.provider,
                "version": provider.version,
                "status": provider.status.value,
                "facts_seen": provider.facts_seen,
                "reward_facts": provider.reward_facts,
                "matched_facts": provider.matched_facts,
                "unmatched_facts": provider.unmatched_facts,
                "unmatched_reasons": dict(provider.unmatched_reason_counts),
                "unresolved_localizations": len(
                    provider.unresolved_localizations
                ),
                "diagnostics": dict(provider.diagnostic_counts),
            }
            if provider is not None
            else None
        ),
        "contract_identity_sha256": _hash_rows(identity_rows),
        "reward_shape_sha256": _hash_rows(reward_rows),
    }


def _hash_rows(rows: list | tuple) -> str:
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["aggregate_snapshot"]
