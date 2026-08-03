"""Import StarStrings' contracts.ini into the domain model.

Interim source, replaced by real extraction in Phase 9. It parses text that was
itself generated from a datamine, so every field is best-effort: anything that
does not match a known shape is recorded in `ContractSet.unparsed` rather than
guessed at.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..ini import LocalizationFile
from ..model import (
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

# Key segments naming the string kind. These appear anywhere in the key, not
# just at the end (`..._Title_VE_001`), and CIG's data includes a `_Dec` typo
# plus `_Name` used for what is functionally a title.
_TITLE_SEGMENTS = {"title", "name"}
_DESC_SEGMENTS = {"desc", "description", "dec"}

_DIFFICULTY = re.compile(r"_(VE|VH|[EMHS])(?=_|$)")

_REP = re.compile(r"Reputation Awarded[^<]*</EM4>\s*([-\d,\s/]+)")
# Titles state rep in a bracket tag; for title-only contracts it is the only source.
_REP_BRACKET = re.compile(r"\[\s*([-\d][\d,/\s-]*)\s*Rep\s*\]", re.IGNORECASE)
_SCENARIO = re.compile(r"Scenario Progress Points\s*([\d,]+)\s*(\(Split\))?", re.IGNORECASE)
_SCRIP = re.compile(r"~mission\(ScripAmount\)\s*MG Scrip", re.IGNORECASE)

_POOL_HEADER = re.compile(
    r"<EM4>\s*(?:Potential Blueprints?|Multiple Blueprint Pools|Pool \d+)"
    r"\s*(?:\(([^)]+)\))?\s*</EM4>",
    re.IGNORECASE,
)
_POOL_LABEL = re.compile(r"<EM4>\s*(Pool \d+)\s*</EM4>", re.IGNORECASE)
_AWARDED_FROM = re.compile(r"<EM4>\s*Awarded from (.+?) level variants\s*</EM4>", re.IGNORECASE)
_REGIONAL = re.compile(
    r"<EM4>\s*\[Regional Variants\][^:<]*:?\s*([^<]*)</EM4>", re.IGNORECASE
)
_CAVEAT = re.compile(r"(Warning:[^\\]+)")
_BULLET = re.compile(r"^-\s+(.*\S)\s*$")

# Gate phrasings that are not reputation tiers.
_FACTION_GATES = {"bitzeros"}
_REGION_HINTS = ("nyx", "pyro", "stanton", "area", "only area")

# Display names for orgs whose canonical casing cannot be derived from the key.
_DISPLAY_OVERRIDES = {
    "thecollector": "The Collector (Wikelo)",
    "cfp": "Citizens for Prosperity",
    "bhg": "Bounty Hunters Guild",
    "mg": "Mercenary Guild",
    "hh": "Headhunters",
    "headhunters": "Headhunters",
    "ors": "ORS",
    "uwc": "United Workers Coalition",
    "ftl": "FTL",
    "rain": "RAIN",
}


# Markers that begin an appended reward annotation. Everything from the
# earliest one onwards is StarStrings' addition, not CIG's original text.
_ANNOTATION_MARKERS = (
    "<EM4>Reputation Awarded",
    "<EM4>Potential Blueprint",
    "<EM4>Multiple Blueprint Pools",
    "<EM4>Scenario Progress Points",
    "<EM4>Awarded from",
    "<EM4>[Regional Variants]",
    "<EM4>Pool ",
    "Warning: Some blueprint strings",
)
# Bracket tags appended to titles, e.g. `<EM4>[100 Rep] [BP]*</EM4>`.
_TITLE_ANNOTATION = re.compile(
    r"\s*<EM4>\s*(?:\[[^\]]*\]\*?\s*)+</EM4>|\s*\[(?:BP\*?|[-\d][\d,/\s-]*\s*Rep)\]\*?",
    re.IGNORECASE,
)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text)


def _strip_annotation(value: str, kind: StringKind) -> str:
    """Recover the text as it stood before reward info was appended."""
    if kind is StringKind.TITLE:
        return _TITLE_ANNOTATION.sub("", value).strip()

    cut = min(
        (index for marker in _ANNOTATION_MARKERS if (index := value.find(marker)) != -1),
        default=-1,
    )
    if cut != -1:
        value = value[:cut]

    # Descriptions separate their annotation with literal \n escapes.
    return re.sub(r"(?:\\n|\s)+$", "", value)


def _parse_ints(blob: str) -> list[int]:
    values = []
    for part in blob.split("/"):
        cleaned = part.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", cleaned):
            values.append(int(cleaned))
    return values


def _classify_gate(label: str) -> Gate:
    # The same gate is written both ways ("Contractor only" / "Awarded from
    # Contractor level variants"); normalise so they collapse to one rank.
    cleaned = re.sub(r"\s+only$", "", label.strip(), flags=re.IGNORECASE)
    lowered = cleaned.casefold()

    if lowered in _FACTION_GATES:
        return Gate(GateKind.FACTION, cleaned)
    if lowered == "repeat":
        return Gate(GateKind.REPEAT, cleaned)
    if any(hint in lowered for hint in _REGION_HINTS):
        return Gate(GateKind.REGION, cleaned)
    return Gate(GateKind.RANK, cleaned)


def _split_key(key: str) -> tuple[str, StringKind | None]:
    """Return (base, kind), where base is shared by a title/desc pair.

    The kind segment can sit anywhere -- `Foxwell_ShipWaveAttack_Title_VE_001`
    pairs with `..._Desc_VE_001` -- so it is removed in place rather than
    trimmed off the end. Trailing sequence numbers go too.
    """
    segments = key.split("_")
    kind: StringKind | None = None
    kept: list[str] = []

    for segment in segments:
        lowered = segment.casefold()
        if kind is None and lowered in _TITLE_SEGMENTS:
            kind = StringKind.TITLE
            continue
        if kind is None and lowered in _DESC_SEGMENTS:
            kind = StringKind.DESC
            continue
        kept.append(segment)

    while len(kept) > 1 and kept[-1].isdigit():
        kept.pop()

    return "_".join(kept), kind


def _parse_reward(value: str) -> Reward:
    reward = Reward()

    if rep := _REP.search(value):
        reward.reputation = _parse_ints(rep.group(1))
    elif bracket := _REP_BRACKET.search(value):
        reward.reputation = _parse_ints(bracket.group(1))

    for amount, split in _SCENARIO.findall(value):
        reward.scenario_points.append(
            ScenarioPoints(int(amount.replace(",", "")), split=bool(split))
        )

    reward.scrip = bool(_SCRIP.search(value))
    reward.blueprint_pools = _parse_pools(value)
    return reward


def _parse_pools(value: str) -> list[BlueprintPool]:
    """Walk the value line-wise, opening a pool at each header and collecting
    the bullets beneath it."""
    pools: list[BlueprintPool] = []
    current: BlueprintPool | None = None
    pending_gates: list[Gate] = []
    pending_label: str | None = None
    pending_locations: list[str] = []

    for raw in value.split("\\n"):
        line = raw.strip()
        if not line:
            continue

        if header := _POOL_HEADER.search(line):
            gates = list(pending_gates)
            if header.group(1):
                gates.append(_classify_gate(header.group(1)))
            current = BlueprintPool(
                gates=gates,
                label=pending_label,
                example_locations=pending_locations,
            )
            pools.append(current)
            pending_gates, pending_label, pending_locations = [], None, []
            continue

        if awarded := _AWARDED_FROM.search(line):
            # Stacks with any gate already on the pool rather than replacing it.
            gate = Gate(GateKind.RANK, awarded.group(1).strip())
            if current and not current.items:
                current.gates.append(gate)
            else:
                pending_gates.append(gate)
            continue

        if label := _POOL_LABEL.search(line):
            if current and not current.items:
                current.label = label.group(1)
            else:
                pending_label = label.group(1)
            continue

        if regional := _REGIONAL.search(line):
            locations = [
                loc.strip() for loc in regional.group(1).split(",") if loc.strip()
            ]
            # A regional marker starts a fresh pool for the variant that follows.
            if current and current.items:
                current = None
                pending_locations = locations
            elif current:
                current.example_locations = locations
            else:
                pending_locations = locations
            continue

        if caveat := _CAVEAT.search(line):
            if current:
                current.caveat = caveat.group(1).strip()
            continue

        if bullet := _BULLET.match(_strip_tags(line)):
            if current is None:
                current = BlueprintPool(
                    gates=list(pending_gates),
                    label=pending_label,
                    example_locations=pending_locations,
                )
                pools.append(current)
                pending_gates, pending_label, pending_locations = [], None, []
            current.items.append(bullet.group(1))

    return [p for p in pools if p.items]


def load(path: Path) -> ContractSet:
    return parse(LocalizationFile.load(path))


def parse(source: LocalizationFile) -> ContractSet:
    entries = source.entries()

    # Orgs are spelled inconsistently across keys; pick the most common casing
    # as the display name unless we have an explicit override.
    casings: dict[str, Counter] = {}
    for entry in entries:
        token = entry.key.split("_")[0]
        casings.setdefault(token.casefold(), Counter())[token] += 1

    orgs: dict[str, Org] = {}
    for org_id, seen in casings.items():
        orgs[org_id] = Org(
            id=org_id,
            name=_DISPLAY_OVERRIDES.get(org_id, seen.most_common(1)[0][0]),
        )

    grouped: dict[str, dict[StringKind, list[tuple[str, str]]]] = {}
    result = ContractSet(orgs=orgs)

    for entry in entries:
        base, kind = _split_key(entry.key)
        if kind is None:
            # No recognisable suffix. Long prose is a description; anything
            # short is a title. Both shapes appear in real data.
            kind = StringKind.DESC if len(entry.value) > 200 else StringKind.TITLE
        variants = grouped.setdefault(base, {}).setdefault(kind, [])
        if not any(key == entry.key for key, _ in variants):
            variants.append((entry.key, entry.value))

    for base, parts in grouped.items():
        org_token = base.split("_")[0]
        org = orgs[org_token.casefold()]

        difficulty = None
        if match := _DIFFICULTY.search(base):
            difficulty = Difficulty.from_code(match.group(1))

        family = _family(base, org_token, difficulty)

        contract = Contract(
            id=base,
            org=org,
            family=family,
            difficulty=difficulty,
            keys={kind: [key for key, _ in variants] for kind, variants in parts.items()},
            texts={
                key: value for variants in parts.values() for key, value in variants
            },
            base_texts={
                key: _strip_annotation(value, kind)
                for kind, variants in parts.items()
                for key, value in variants
            },
        )

        # Rewards are stated in either string; the description carries more.
        merged = " ".join(contract.texts.values())
        contract.reward = _parse_reward(merged)

        if contract.reward.is_empty:
            result.unparsed.append((base, "no reward data found"))

        result.contracts.append(contract)

    for org in orgs.values():
        org.rank_ladder = _rank_ladder(result, org.id)

    return result


def _family(base: str, org_token: str, difficulty: Difficulty | None) -> str:
    remainder = base[len(org_token) :].lstrip("_")
    if difficulty:
        remainder = _DIFFICULTY.sub("", remainder)
    return remainder.strip("_") or "general"


# Observed progressions. Orgs use overlapping subsets of these, so a single
# ordering hint covers all of them; anything unrecognised sorts to the end.
_RANK_ORDER = [
    "Neutral",
    "Applicant",
    "Prospective Associate",
    "Probationary Guild Member",
    "Rookie",
    "Trainee",
    "Security Trainee",
    "Associate",
    "Jr. Contractor",
    "Jr. Security Contractor",
    "Trusted Associate",
    "Contractor",
    "Security Contractor",
    "Sr. Contractor",
    "Sr. Security Contractor",
    "Head Contractor",
    "Veteran Contractor",
    "Elite Contractor",
    "Master",
]


def _rank_ladder(result: ContractSet, org_id: str) -> list[str]:
    labels = {
        gate.label
        for contract in result.by_org(org_id)
        for pool in contract.reward.blueprint_pools
        for gate in pool.gates
        if gate.kind is GateKind.RANK
    }
    return sorted(
        labels,
        key=lambda label: (
            _RANK_ORDER.index(label) if label in _RANK_ORDER else len(_RANK_ORDER),
            label,
        ),
    )
