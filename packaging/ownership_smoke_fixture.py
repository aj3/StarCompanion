"""Build invented C4 catalog/log inputs for frozen CLI smoke tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    cache_path = root / "catalog-cache.json"
    # Keep this fixture dependency-free: packaging/verify_offline.py must be
    # runnable by a clean CPython after the application itself is frozen. The
    # committed cache version is intentionally explicit so format drift fails
    # the packaged smoke instead of being hidden by importing source helpers.
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": 6,
                "source": "game:LIVE:synthetic:english",
                "generated": "2026-03-26T17:15:41+00:00",
                "orgs": {
                    "synthetic": {
                        "id": "synthetic",
                        "name": "Synthetic Org",
                        "rank_ladder": [],
                    }
                },
                "evidence": [],
                "contracts": [
                    {
                        "id": "Synthetic_Blueprint_Mission",
                        "org": "synthetic",
                        "family": "BlueprintMission",
                        "difficulty": None,
                        "keys": {},
                        "texts": {},
                        "base_texts": {},
                        "reward": {
                            "reputation": [],
                            "scenario_points": [],
                            "scrip": False,
                            "blueprint_pools": [
                                {
                                    "items": ["Synthetic Coda"],
                                    "item_ids": {
                                        "Synthetic Coda": (
                                            "11111111-1111-1111-1111-111111111111"
                                        )
                                    },
                                    "item_categories": {
                                        "Synthetic Coda": "weapons"
                                    },
                                    "gates": [],
                                    "label": None,
                                    "example_locations": [],
                                    "caveat": None,
                                    "chance": None,
                                    "owned": [],
                                }
                            ],
                            "item_rewards": [],
                        },
                        "evidence_ids": [],
                    }
                ],
                "capabilities": [],
                "unparsed": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
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
