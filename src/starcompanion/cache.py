"""Serialise a ContractSet so extraction and rendering can run separately.

Deliberately explicit rather than reflective: the cache is a file format users
will keep across upgrades, so field names change only when we decide they do.
Version-tagged, and stamped with which source produced it -- a cache built by
the interim importer and one built from the game files are not interchangeable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import (
    BlueprintPool,
    Contract,
    ContractSet,
    Difficulty,
    Gate,
    GateKind,
    Org,
    Reward,
    ScenarioPoints,
    StringKind,
)

CACHE_VERSION = 1


class UnsupportedCacheVersion(ValueError):
    def __init__(self, found: object):
        super().__init__(
            f"cache_version {found!r} is not supported by this build "
            f"(expected {CACHE_VERSION}). Re-run `starcompanion import` to rebuild it."
        )


def _pool_to_dict(pool: BlueprintPool) -> dict[str, Any]:
    return {
        "items": pool.items,
        "gates": [{"kind": g.kind.value, "label": g.label} for g in pool.gates],
        "label": pool.label,
        "example_locations": pool.example_locations,
        "caveat": pool.caveat,
        # Sorted so the file is stable between runs.
        "owned": sorted(pool.owned),
    }


def _pool_from_dict(data: dict[str, Any]) -> BlueprintPool:
    return BlueprintPool(
        items=list(data.get("items", ())),
        gates=[Gate(GateKind(g["kind"]), g["label"]) for g in data.get("gates", ())],
        label=data.get("label"),
        example_locations=list(data.get("example_locations", ())),
        caveat=data.get("caveat"),
        owned=set(data.get("owned", ())),
    )


def _reward_to_dict(reward: Reward) -> dict[str, Any]:
    return {
        "reputation": reward.reputation,
        "scenario_points": [
            {"amount": p.amount, "split": p.split} for p in reward.scenario_points
        ],
        "scrip": reward.scrip,
        "blueprint_pools": [_pool_to_dict(p) for p in reward.blueprint_pools],
    }


def _reward_from_dict(data: dict[str, Any]) -> Reward:
    return Reward(
        reputation=list(data.get("reputation", ())),
        scenario_points=[
            ScenarioPoints(p["amount"], split=p.get("split", False))
            for p in data.get("scenario_points", ())
        ],
        scrip=data.get("scrip", False),
        blueprint_pools=[_pool_from_dict(p) for p in data.get("blueprint_pools", ())],
    )


def dumps(contracts: ContractSet, *, source: str = "unknown") -> str:
    payload = {
        "cache_version": CACHE_VERSION,
        "source": source,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "orgs": {
            org.id: {"id": org.id, "name": org.name, "rank_ladder": org.rank_ladder}
            for org in contracts.orgs.values()
        },
        "contracts": [
            {
                "id": c.id,
                "org": c.org.id,
                "family": c.family,
                "difficulty": c.difficulty.code if c.difficulty else None,
                "keys": {kind.value: keys for kind, keys in c.keys.items()},
                "texts": c.texts,
                "base_texts": c.base_texts,
                "reward": _reward_to_dict(c.reward),
            }
            for c in contracts.contracts
        ],
        "unparsed": [list(item) for item in contracts.unparsed],
    }
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def loads(text: str) -> ContractSet:
    data = json.loads(text)

    found = data.get("cache_version")
    if found != CACHE_VERSION:
        raise UnsupportedCacheVersion(found)

    orgs = {
        org_id: Org(
            id=raw["id"], name=raw["name"], rank_ladder=list(raw.get("rank_ladder", ()))
        )
        for org_id, raw in data["orgs"].items()
    }

    contracts = [
        Contract(
            id=raw["id"],
            org=orgs[raw["org"]],
            family=raw["family"],
            difficulty=Difficulty.from_code(raw["difficulty"]) if raw["difficulty"] else None,
            keys={
                StringKind(kind): list(keys) for kind, keys in raw.get("keys", {}).items()
            },
            texts=dict(raw.get("texts", {})),
            base_texts=dict(raw.get("base_texts", {})),
            reward=_reward_from_dict(raw.get("reward", {})),
        )
        for raw in data["contracts"]
    ]

    return ContractSet(
        contracts=contracts,
        orgs=orgs,
        unparsed=[tuple(item) for item in data.get("unparsed", ())],
    )


def save(contracts: ContractSet, path: Path, *, source: str = "unknown") -> None:
    path.write_text(dumps(contracts, source=source), encoding="utf-8")


def load(path: Path) -> ContractSet:
    return loads(path.read_text(encoding="utf-8"))


def describe(path: Path) -> dict[str, Any]:
    """Header fields only, for reporting what a cache holds."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "cache_version": data.get("cache_version"),
        "source": data.get("source"),
        "generated": data.get("generated"),
        "contracts": len(data.get("contracts", ())),
    }
