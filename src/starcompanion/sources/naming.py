"""The naming convention CIG uses for contract localization keys.

Contract strings follow a rigid shape:

    {Org}_{family...}[_{difficulty}]_{title|desc}[_{sequence}]

e.g. `Foxwell_bombingrun_VE_title_001`, `Covalex_HaulCargo_AToB_title`.

Generic discovery uses that convention because `global.ini` has no record
relationships. During a complete local import, typed localization references
from `Data/Game2.dcb` provide stronger evidence for short and unusual missions;
the naming rules remain the safety boundary for strings without that evidence.

Shared by both importers so the two cannot drift apart.
"""

from __future__ import annotations

import re
from collections import Counter

from ..model import Difficulty, StringKind

# Segments naming the string kind. They can appear anywhere in the key, not
# only at the end, and CIG's data includes a `_Dec` typo plus `_Name` used for
# what is functionally a title.
TITLE_SEGMENTS = frozenset({"title", "name"})
DESC_SEGMENTS = frozenset({"desc", "description", "dec"})
KIND_SEGMENTS = TITLE_SEGMENTS | DESC_SEGMENTS

DIFFICULTY = re.compile(r"_(VE|VH|[EMHS])(?=_|$)")

# Display names for orgs whose canonical casing cannot be derived from the key.
DISPLAY_OVERRIDES = {
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
    "gobling": "Goblin Group",
}


def canonical_key(key: str) -> str:
    """Remove CIG's plural metadata suffix for naming analysis only."""

    return key[:-2] if key.casefold().endswith(",p") else key


def split_key(key: str) -> tuple[str, StringKind | None]:
    """Return (base, kind), where base is shared by a title/desc pair.

    The kind segment can sit anywhere -- `Foxwell_ShipWave_Title_VE_001` pairs
    with `..._Desc_VE_001` -- so it is removed in place rather than trimmed off
    the end. Trailing sequence numbers go too.
    """
    kind: StringKind | None = None
    kept: list[str] = []

    for segment in canonical_key(key).split("_"):
        lowered = segment.casefold()
        if kind is None and lowered in TITLE_SEGMENTS:
            kind = StringKind.TITLE
            continue
        if kind is None and lowered in DESC_SEGMENTS:
            kind = StringKind.DESC
            continue
        kept.append(segment)

    while len(kept) > 1 and kept[-1].isdigit():
        kept.pop()

    return "_".join(kept), kind


# CIG namespaces that use the same key shape as contracts but are not missions.
# Determined by inspection: `item_*` alone accounts for over a thousand item
# names and descriptions that would otherwise be rewritten.
NON_MISSION_NAMESPACES = frozenset({
    "item", "items", "ui", "hint", "hints", "area", "pause", "terminal",
    "vehicle", "vehicles", "weapon", "weapons", "ship", "ships", "shop",
    "commodity", "commodities", "component", "components", "tutorial",
    "option", "options", "menu", "error", "generic", "debug", "test",
    "journal", "emote", "emotes", "attachment", "attachments", "paint",
    "loadout", "room", "habitation", "elevator", "kiosk",
})


def looks_like_contract(key: str) -> bool:
    """Whether a key follows the contract convention.

    Deliberately strict: this decides what gets rewritten in the user's game
    text, and prefixing an unrelated UI label with `[Org 3]` would be worse
    than missing a contract.
    """
    segments = canonical_key(key).split("_")
    if len(segments) < 3:
        return False

    if not any(s.casefold() in KIND_SEGMENTS for s in segments):
        return False

    # The first segment names the mission giver, so it must be a plausible
    # identifier rather than a number or a stray token.
    org = segments[0]
    if not org or not org[0].isalpha() or len(org) <= 1:
        return False
    return org.casefold() not in NON_MISSION_NAMESPACES


def difficulty_of(base: str) -> Difficulty | None:
    match = DIFFICULTY.search(base)
    return Difficulty.from_code(match.group(1)) if match else None


def org_token(key: str) -> str:
    return canonical_key(key).split("_")[0]


def family_of(base: str, org_token_value: str, difficulty: Difficulty | None) -> str:
    remainder = base[len(org_token_value) :].lstrip("_")
    if difficulty:
        remainder = DIFFICULTY.sub("", remainder)
    return remainder.strip("_") or "general"


def display_name(org_id: str, seen_casings: Counter[str]) -> str:
    """Curated name if we have one, else the spelling used most often.

    Source data is inconsistent -- headhunters / Headhunters / HeadHunters are
    one org -- so ids are casefolded and the display name is chosen here.
    """
    if org_id in DISPLAY_OVERRIDES:
        return DISPLAY_OVERRIDES[org_id]
    return seen_casings.most_common(1)[0][0] if seen_casings else org_id


# Observed reputation progressions. Orgs use overlapping subsets, so one
# ordering hint covers all of them; anything unrecognised sorts to the end.
RANK_ORDER = [
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


def rank_sort_key(label: str) -> tuple[int, str]:
    return (RANK_ORDER.index(label) if label in RANK_ORDER else len(RANK_ORDER), label)
