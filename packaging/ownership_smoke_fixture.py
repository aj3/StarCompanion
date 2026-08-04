"""Build invented C4 catalog/log inputs for frozen CLI smoke tests."""

from __future__ import annotations

import argparse
from pathlib import Path

from starcompanion import cache
from starcompanion.model import BlueprintPool, Contract, ContractSet, Org, Reward


def build(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    org = Org("synthetic", "Synthetic Org")
    contracts = ContractSet(
        [
            Contract(
                "Synthetic_Blueprint_Mission",
                org,
                "BlueprintMission",
                reward=Reward(
                    blueprint_pools=[
                        BlueprintPool(
                            items=["Synthetic Coda"],
                            item_ids={
                                "Synthetic Coda": "11111111-1111-1111-1111-111111111111"
                            },
                            item_categories={"Synthetic Coda": "weapons"},
                        )
                    ]
                ),
            )
        ],
        {org.id: org},
    )
    cache_path = root / "catalog-cache.json"
    cache.save(contracts, cache_path, source="game:LIVE:synthetic:english")
    log_path = root / "Game.log"
    log_path.write_text(
        '<2026-03-26T17:15:41.684Z> [Notice] '
        '<SHUDEvent_OnNotification> Added notification '
        '"Received Blueprint: Synthetic Coda: " [23] to queue.\n',
        encoding="utf-8",
    )
    return cache_path, log_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    for path in build(args.root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
