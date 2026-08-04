"""Serialise a ContractSet so extraction and rendering can run separately.

Deliberately explicit rather than reflective: the cache is a file format users
will keep across upgrades, so field names change only when we decide they do.
Version-tagged, and stamped with which source produced it -- a cache built by
the interim importer and one built from the game files are not interchangeable.
"""

from __future__ import annotations

import json
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import (
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
    UnresolvedLocalization,
)

CACHE_VERSION = 6


class UnsupportedCacheVersion(ValueError):
    def __init__(self, found: object):
        super().__init__(
            f"cache_version {found!r} is not supported by this build "
            f"(expected {CACHE_VERSION}). Re-run `starcompanion import` to rebuild it."
        )


def _pool_to_dict(pool: BlueprintPool) -> dict[str, Any]:
    return {
        "items": pool.items,
        "item_ids": pool.item_ids,
        "item_categories": pool.item_categories,
        "gates": [{"kind": g.kind.value, "label": g.label} for g in pool.gates],
        "label": pool.label,
        "example_locations": pool.example_locations,
        "caveat": pool.caveat,
        "chance": pool.chance,
        # Sorted so the file is stable between runs.
        "owned": sorted(pool.owned),
    }


def _pool_from_dict(data: dict[str, Any]) -> BlueprintPool:
    return BlueprintPool(
        items=list(data.get("items", ())),
        item_ids={str(k): str(v) for k, v in data.get("item_ids", {}).items()},
        item_categories={
            str(k): str(v) for k, v in data.get("item_categories", {}).items()
        },
        gates=[Gate(GateKind(g["kind"]), g["label"]) for g in data.get("gates", ())],
        label=data.get("label"),
        example_locations=list(data.get("example_locations", ())),
        caveat=data.get("caveat"),
        chance=data.get("chance"),
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
        "item_rewards": reward.item_rewards,
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
        item_rewards=list(data.get("item_rewards", ())),
    )


def _contract_to_dict(
    contract: Contract,
    evidence_ids: dict[Evidence, int],
) -> dict[str, Any]:
    # Base text normally equals stock text. Store only actual overrides and
    # reconstruct the common values on load instead of duplicating megabytes.
    base_text_delta = {
        key: value
        for key, value in contract.base_texts.items()
        if contract.texts.get(key) != value
    }
    return {
        "id": contract.id,
        "org": contract.org.id,
        "family": contract.family,
        "difficulty": contract.difficulty.code if contract.difficulty else None,
        "keys": {kind.value: keys for kind, keys in contract.keys.items()},
        "texts": contract.texts,
        "base_texts": base_text_delta,
        "reward": _reward_to_dict(contract.reward),
        "evidence_ids": [evidence_ids[item] for item in contract.evidence],
    }


def _evidence_to_dict(item: Evidence) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "record_id": item.record_id,
        "record_path": item.record_path,
        "field_path": item.field_path,
        "value": item.value,
    }


def _evidence_from_dict(data: dict[str, Any]) -> Evidence:
    return Evidence(
        provider=data["provider"],
        record_id=data["record_id"],
        record_path=data["record_path"],
        field_path=data["field_path"],
        value=data.get("value"),
    )


def _capability_to_dict(item: ProviderCapability) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "version": item.version,
        "status": item.status.value,
        "build_version": item.build_version,
        "facts_seen": item.facts_seen,
        "contracts_enhanced": item.contracts_enhanced,
        "evidence_links": item.evidence_links,
        "diagnostics": list(item.diagnostics),
        "reward_facts": item.reward_facts,
        "matched_facts": item.matched_facts,
        "unmatched_facts": item.unmatched_facts,
        "unmatched_samples": list(item.unmatched_samples),
        "unmatched_reason_counts": [
            list(pair) for pair in item.unmatched_reason_counts
        ],
        "diagnostic_counts": [list(pair) for pair in item.diagnostic_counts],
        "unresolved_localizations": [
            {
                "source_id": unresolved.source_id,
                "reason": unresolved.reason,
                "keys": list(unresolved.keys),
            }
            for unresolved in item.unresolved_localizations
        ],
    }


def _capability_from_dict(data: dict[str, Any]) -> ProviderCapability:
    return ProviderCapability(
        provider=data["provider"],
        version=data["version"],
        status=ProviderStatus(data["status"]),
        build_version=str(data.get("build_version", "unknown")),
        facts_seen=int(data.get("facts_seen", 0)),
        contracts_enhanced=int(data.get("contracts_enhanced", 0)),
        evidence_links=int(data.get("evidence_links", 0)),
        diagnostics=tuple(data.get("diagnostics", ())),
        reward_facts=int(data.get("reward_facts", 0)),
        matched_facts=int(data.get("matched_facts", 0)),
        unmatched_facts=int(data.get("unmatched_facts", 0)),
        unmatched_samples=tuple(data.get("unmatched_samples", ())),
        unmatched_reason_counts=tuple(
            (str(reason), int(count))
            for reason, count in data.get("unmatched_reason_counts", ())
        ),
        diagnostic_counts=tuple(
            (str(category), int(count))
            for category, count in data.get("diagnostic_counts", ())
        ),
        unresolved_localizations=tuple(
            UnresolvedLocalization(
                source_id=str(item["source_id"]),
                reason=str(item["reason"]),
                keys=tuple(str(key) for key in item.get("keys", ())),
            )
            for item in data.get("unresolved_localizations", ())
        ),
    )


def _evidence_table(
    contracts: ContractSet,
) -> tuple[list[Evidence], dict[Evidence, int]]:
    table: list[Evidence] = []
    ids: dict[Evidence, int] = {}
    for contract in contracts.contracts:
        for item in contract.evidence:
            if item not in ids:
                ids[item] = len(table)
                table.append(item)
    return table, ids


def dump(contracts: ContractSet, stream, *, source: str = "unknown") -> None:
    """Write a cache incrementally instead of constructing one giant string."""
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence, evidence_ids = _evidence_table(contracts)
    stream.write("{\n \"cache_version\": ")
    json.dump(CACHE_VERSION, stream)
    stream.write(",\n \"source\": ")
    json.dump(source, stream, ensure_ascii=False)
    stream.write(",\n \"generated\": ")
    json.dump(generated, stream)
    stream.write(",\n \"orgs\": {")
    for index, org in enumerate(contracts.orgs.values()):
        stream.write("," if index else "")
        stream.write("\n  ")
        json.dump(org.id, stream, ensure_ascii=False)
        stream.write(": ")
        json.dump(
            {"id": org.id, "name": org.name, "rank_ladder": org.rank_ladder},
            stream,
            ensure_ascii=False,
        )
    stream.write("\n },\n \"evidence\": [")
    for index, item in enumerate(evidence):
        stream.write("," if index else "")
        stream.write("\n  ")
        json.dump(_evidence_to_dict(item), stream, ensure_ascii=False)
    stream.write("\n ],\n \"contracts\": [")
    for index, contract in enumerate(contracts.contracts):
        stream.write("," if index else "")
        stream.write("\n  ")
        json.dump(_contract_to_dict(contract, evidence_ids), stream, ensure_ascii=False)
    stream.write("\n ],\n \"capabilities\": [")
    for index, item in enumerate(contracts.capabilities):
        stream.write("," if index else "")
        stream.write("\n  ")
        json.dump(_capability_to_dict(item), stream, ensure_ascii=False)
    stream.write("\n ],\n \"unparsed\": [")
    for index, item in enumerate(contracts.unparsed):
        stream.write("," if index else "")
        stream.write("\n  ")
        json.dump(list(item), stream, ensure_ascii=False)
    stream.write("\n ]\n}\n")


def dumps(contracts: ContractSet, *, source: str = "unknown") -> str:
    stream = io.StringIO()
    dump(contracts, stream, source=source)
    return stream.getvalue()


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

    evidence = [_evidence_from_dict(item) for item in data.get("evidence", ())]
    contracts = [_contract_from_dict(raw, orgs, evidence) for raw in data["contracts"]]

    return ContractSet(
        contracts=contracts,
        orgs=orgs,
        unparsed=[tuple(item) for item in data.get("unparsed", ())],
        capabilities=[
            _capability_from_dict(item) for item in data.get("capabilities", ())
        ],
    )


def save(contracts: ContractSet, path: Path, *, source: str = "unknown") -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        dump(contracts, stream, source=source)


def load(path: Path) -> ContractSet:
    return loads(path.read_text(encoding="utf-8"))


def dump_lines(
    contracts: ContractSet,
    stream,
    *,
    source: str = "unknown",
) -> None:
    """Typed JSON Lines transport for incremental helper-process results."""
    evidence, evidence_ids = _evidence_table(contracts)
    _write_line(
        stream,
        {
            "type": "header",
            "cache_version": CACHE_VERSION,
            "source": source,
        },
    )
    for org in contracts.orgs.values():
        _write_line(
            stream,
            {
                "type": "org",
                "id": org.id,
                "name": org.name,
                "rank_ladder": org.rank_ladder,
            },
        )
    for item in evidence:
        _write_line(stream, {"type": "evidence", "data": _evidence_to_dict(item)})
    for contract in contracts.contracts:
        _write_line(
            stream,
            {"type": "contract", "data": _contract_to_dict(contract, evidence_ids)},
        )
    for item in contracts.capabilities:
        _write_line(stream, {"type": "capability", "data": _capability_to_dict(item)})
    for key, reason in contracts.unparsed:
        _write_line(stream, {"type": "unparsed", "key": key, "reason": reason})


def load_lines(stream) -> ContractSet:
    """Reconstruct a helper result one record at a time."""
    header = None
    orgs: dict[str, Org] = {}
    contracts: list[Contract] = []
    unparsed: list[tuple[str, str]] = []
    capabilities: list[ProviderCapability] = []
    evidence: list[Evidence] = []
    for number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        data = json.loads(line)
        kind = data.get("type")
        if number == 1:
            if kind != "header":
                raise ValueError("helper contract result does not begin with a header")
            if data.get("cache_version") != CACHE_VERSION:
                raise UnsupportedCacheVersion(data.get("cache_version"))
            header = data
        elif kind == "org":
            org = Org(
                id=data["id"],
                name=data["name"],
                rank_ladder=list(data.get("rank_ladder", ())),
            )
            orgs[org.id] = org
        elif kind == "contract":
            contracts.append(_contract_from_dict(data["data"], orgs, evidence))
        elif kind == "evidence":
            evidence.append(_evidence_from_dict(data["data"]))
        elif kind == "capability":
            capabilities.append(_capability_from_dict(data["data"]))
        elif kind == "unparsed":
            unparsed.append((data["key"], data["reason"]))
        else:
            raise ValueError(f"unknown helper contract record {kind!r} on line {number}")
    if header is None:
        raise ValueError("helper contract result is empty")
    return ContractSet(
        contracts=contracts,
        orgs=orgs,
        unparsed=unparsed,
        capabilities=capabilities,
    )


def _contract_from_dict(
    raw: dict[str, Any],
    orgs: dict[str, Org],
    evidence_table: list[Evidence],
) -> Contract:
    texts = dict(raw.get("texts", {}))
    base_texts = dict(texts)
    base_texts.update(raw.get("base_texts", {}))
    try:
        indices = [int(index) for index in raw.get("evidence_ids", ())]
        if any(index < 0 or index >= len(evidence_table) for index in indices):
            raise IndexError("evidence index is outside the cache table")
        evidence = [evidence_table[index] for index in indices]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"contract {raw.get('id')!r} has an invalid evidence reference") from exc
    return Contract(
        id=raw["id"],
        org=orgs[raw["org"]],
        family=raw["family"],
        difficulty=Difficulty.from_code(raw["difficulty"]) if raw["difficulty"] else None,
        keys={StringKind(kind): list(keys) for kind, keys in raw.get("keys", {}).items()},
        texts=texts,
        base_texts=base_texts,
        reward=_reward_from_dict(raw.get("reward", {})),
        evidence=evidence,
    )


def _write_line(stream, data: dict[str, Any]) -> None:
    json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def describe(path: Path) -> dict[str, Any]:
    """Header fields only, for reporting what a cache holds."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "cache_version": data.get("cache_version"),
        "source": data.get("source"),
        "generated": data.get("generated"),
        "contracts": len(data.get("contracts", ())),
        "providers": len(data.get("capabilities", ())),
        "enhanced_contracts": sum(
            1 for item in data.get("contracts", ()) if item.get("evidence_ids")
        ),
    }
