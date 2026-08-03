"""Combine sources, or compare them without combining.

Two deliberately separate operations:

- `apply_ownership` / `apply_pools` **change** the contract set.
- `compare_pools` **changes nothing** and reports where two sources disagree.

Keeping them apart matters: reward data comes from a community source, and
being able to see disagreements without adopting them is how you tell a genuine
correction from a stale entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import BlueprintPool, ContractSet
from .scmdb import ScmdbExport


@dataclass
class OwnershipResult:
    pools_marked: int = 0
    items_marked: int = 0
    fully_owned_pools: int = 0
    unmatched: set[str] = field(default_factory=set)
    """Owned blueprints that match nothing in the contract data -- usually
    items awarded outside contracts, not an error."""

    def summary(self) -> str:
        return (
            f"{self.items_marked:,} items marked owned across "
            f"{self.pools_marked:,} pools ({self.fully_owned_pools:,} complete)"
        )


@dataclass
class Disagreement:
    contract: str
    only_ours: list[str] = field(default_factory=list)
    only_theirs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.only_ours:
            parts.append(f"-{len(self.only_ours)}")
        if self.only_theirs:
            parts.append(f"+{len(self.only_theirs)}")
        return f"{self.contract}: {' '.join(parts)}"


@dataclass
class ComparisonResult:
    """Read-only. Nothing here has been applied."""

    agreed: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)
    only_in_ours: list[str] = field(default_factory=list)
    only_in_theirs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.agreed:,} agree, {len(self.disagreements):,} differ, "
            f"{len(self.only_in_ours):,} only local, "
            f"{len(self.only_in_theirs):,} only in the export"
        )

    @property
    def is_clean(self) -> bool:
        return not self.disagreements


def apply_ownership(contracts: ContractSet, export: ScmdbExport) -> OwnershipResult:
    """Mark pool items the player already owns.

    Only sets what the export actually says. An empty `owned` set on a pool
    means unknown, never 'owns nothing'.
    """
    owned = export.owned_blueprints
    result = OwnershipResult()

    if not owned:
        return result

    matched: set[str] = set()

    for contract in contracts.contracts:
        for pool in contract.reward.blueprint_pools:
            present = {item for item in pool.items if item in owned}
            if not present:
                continue

            pool.owned = present
            matched |= present
            result.pools_marked += 1
            result.items_marked += len(present)
            if pool.fully_owned:
                result.fully_owned_pools += 1

    result.unmatched = owned - matched
    return result


def apply_pools(contracts: ContractSet, export: ScmdbExport, *, overwrite: bool = False) -> int:
    """Fill in blueprint pools from an export.

    By default only contracts with *no* pool are filled, so a community export
    supplements local data rather than silently replacing it. Pass
    `overwrite=True` to prefer the export.
    """
    if not export.pools:
        return 0

    changed = 0
    for contract in contracts.contracts:
        items = _lookup(export, contract)
        if not items:
            continue

        has_pool = bool(contract.reward.blueprint_pools)
        if has_pool and not overwrite:
            continue

        contract.reward.blueprint_pools = [
            BlueprintPool(items=list(items), caveat=f"pool from {export.source_file or 'SCMDB'}")
        ]
        changed += 1

    return changed


def compare_pools(contracts: ContractSet, export: ScmdbExport) -> ComparisonResult:
    """Report differences without changing anything."""
    result = ComparisonResult()
    if not export.pools:
        return result

    seen: set[str] = set()

    for contract in contracts.contracts:
        theirs = _lookup(export, contract)
        ours = sorted({item for p in contract.reward.blueprint_pools for item in p.items})

        if theirs is None:
            if ours:
                result.only_in_ours.append(contract.id)
            continue

        seen.add(_match_key(export, contract))

        if not ours:
            result.only_in_theirs.append(contract.id)
            continue

        only_ours = sorted(set(ours) - set(theirs))
        only_theirs = sorted(set(theirs) - set(ours))

        if only_ours or only_theirs:
            result.disagreements.append(
                Disagreement(contract.id, only_ours, only_theirs)
            )
        else:
            result.agreed += 1

    result.only_in_theirs.extend(sorted(set(export.pools) - seen))
    return result


def _match_key(export: ScmdbExport, contract) -> str:
    for candidate in (contract.id, *contract.all_keys()):
        if candidate in export.pools:
            return candidate
    return contract.id


def _lookup(export: ScmdbExport, contract) -> list[str] | None:
    """Match an export entry to a contract by id or any localization key."""
    for candidate in (contract.id, *contract.all_keys()):
        if candidate in export.pools:
            return export.pools[candidate]
    return None
