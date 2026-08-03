"""Build contracts from the game's own DataCore.

**Scope, established empirically against a real `Game2.dcb`:**

- Every one of the 67 LOCALE-bearing structs was scanned. `MissionBrokerEntry`
  is the only one carrying contract strings: 2,492 entries, ~1,007 distinct
  localization keys.
- Of those, only ~95 overlap the ~1,449 keys StarStrings annotates -- but ~912
  are contracts StarStrings does *not* cover.
- **Rewards are not reachable from here.** `BlueprintReward` and `MissionReward`
  exist in the schema with *zero* instances; the values live behind the
  Subsumption mission-XML layer this module does not read (roadmap Phase 9b).

So this is a discovery source: it finds contracts and their localization keys.
It is not yet a replacement for `contracts_ini`, which still supplies rewards.
Rather than invent reward data, contracts from here carry an empty `Reward`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..extract import datacore
from ..extract.datacore import DataCore, Record
from ..model import Contract, ContractSet, Org, StringKind

MISSION_STRUCT = "MissionBrokerEntry"

# LOCALE fields on a broker entry, and which string each represents.
STRING_FIELDS: dict[str, StringKind] = {
    "title": StringKind.TITLE,
    "titleHUD": StringKind.TITLE,
    "description": StringKind.DESC,
}

# Placeholders CIG leaves in unfinished records.
PLACEHOLDERS = frozenset({"LOC_UNINITIALIZED", "LOC_PLACEHOLDER", ""})


def load(path: Path) -> ContractSet:
    return extract(datacore.load(path))


def extract(core: DataCore) -> ContractSet:
    if core.struct_index(MISSION_STRUCT) is None:
        raise datacore.DataCoreError(
            f"{MISSION_STRUCT} is not in this DataCore; the schema changed and "
            f"the extraction rules need updating"
        )

    result = ContractSet()
    orgs: dict[str, Org] = {}

    for record in core.records_of(MISSION_STRUCT):
        keys, texts = _strings(core.read_record(record))

        if not keys:
            result.unparsed.append((record.name, "no localization keys"))
            continue

        result.contracts.append(
            Contract(
                id=record.name,
                org=_org_for(record, orgs),
                family=_family(record),
                keys=keys,
                texts=texts,
                base_texts=dict(texts),
                # Left empty deliberately: reward values are not in the
                # DataCore. Inventing zeros here would read as real data.
            )
        )

    result.orgs = orgs
    return result


def resolve_texts(contracts: ContractSet, strings) -> int:
    """Fill in the actual text for each key from a `LocalizationFile`.

    Extraction yields keys; the strings themselves live in global.ini. Returns
    how many were resolved.
    """
    resolved = 0
    for contract in contracts.contracts:
        for key in contract.all_keys():
            value = strings.get(key)
            if value is not None:
                contract.texts[key] = value
                contract.base_texts[key] = value
                resolved += 1
    return resolved


def _strings(instance: dict[str, Any]) -> tuple[dict[StringKind, list[str]], dict[str, str]]:
    """LOCALE values are stored as `@key`, naming a global.ini entry."""
    keys: dict[StringKind, list[str]] = {}
    texts: dict[str, str] = {}

    for field, kind in STRING_FIELDS.items():
        value = instance.get(field)
        if not isinstance(value, str) or not value.startswith("@"):
            continue

        key = value[1:]
        if key in PLACEHOLDERS:
            continue

        bucket = keys.setdefault(kind, [])
        if key not in bucket:
            bucket.append(key)
            texts[key] = ""

    return keys, texts


def _org_for(record: Record, orgs: dict[str, Org]) -> Org:
    """`MissionBrokerEntry.PU_Bounty` -> org `PU`, family `Bounty`."""
    token = record.name.split(".", 1)[-1].split("_", 1)[0] or "unknown"
    org_id = token.casefold()

    if org_id not in orgs:
        orgs[org_id] = Org(id=org_id, name=token)
    return orgs[org_id]


def _family(record: Record) -> str:
    stem = record.name.split(".", 1)[-1]
    _, _, rest = stem.partition("_")
    return rest or stem
