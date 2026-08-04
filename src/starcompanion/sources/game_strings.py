"""Discover contracts from the game's own string table.

This is the primary source. It reads the stock `global.ini` extracted from
`Data.p4k` and finds contracts by the naming convention in `naming.py` -- no
community file, no network, nothing but the user's install.

What it yields: which contracts exist, their mission giver, family, difficulty
rank, and the real title and description text.

The localization table itself cannot yield reputation amounts or reward pools.
The local C2 mission provider joins those later from ``Data/Game2.dcb`` using
these localization keys; optional community imports remain a separate source.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..ini import LocalizationFile
from ..model import Contract, ContractSet, Org, StringKind
from . import naming


@dataclass(frozen=True)
class MissionKeyEvidence:
    """Typed DataForge relationship between one mission's localization keys."""

    title_keys: tuple[str, ...] = ()
    description_keys: tuple[str, ...] = ()


def load(path: Path) -> ContractSet:
    """Read a `global.ini` from disk."""
    return parse(LocalizationFile.load(Path(path)))


def from_install(install, language: str = "english") -> ContractSet:
    """Read the pristine strings straight out of the game archive.

    Preferred over the loose file: an install that already carries an override
    would otherwise feed its own annotations back in.
    """
    from ..extract.p4k import P4KArchive, is_localization_entry

    with tempfile.SpooledTemporaryFile(max_size=1 << 20, mode="w+b") as stream:
        with P4KArchive(install.archive, entry_filter=is_localization_entry) as archive:
            archive.stream_localization(stream.write, language)
        strings = LocalizationFile.load_stream(stream)

    return parse(strings)


def is_contract(parts: dict[StringKind, list[tuple[str, str]]]) -> bool:
    """Decide from content, not just the key shape.

    A contract has both a title and a description, and that description is
    either substantial prose or uses the game's `~mission(...)` substitution
    tokens. Item names and UI labels share the key shape but fail both tests --
    and prefixing an unrelated label with `[Org 3]` would be worse than missing
    a contract.
    """
    if StringKind.TITLE not in parts or StringKind.DESC not in parts:
        return False

    if any("~mission(" in value for group in parts.values() for _, value in group):
        return True
    return any(len(value) > 120 for _, value in parts[StringKind.DESC])


def parse(
    strings: LocalizationFile,
    *,
    evidenced_keys: tuple[str, ...] | list[str] | set[str] = (),
    evidenced_groups: tuple[MissionKeyEvidence, ...] | list[MissionKeyEvidence] = (),
) -> ContractSet:
    """Discover contracts, accepting typed DataForge mission-key evidence.

    Evidence bypasses only the prose-length/token content heuristic and may
    supply title/description roles for structurally unusual keys. Exact key
    membership, namespace denial, and a typed description remain mandatory,
    so unrelated UI or item strings cannot enter through a weaker global rule.
    """

    evidence_aliases, evidence_roles, desc_only_bases = _evidence_aliases(
        evidenced_keys,
        evidenced_groups,
    )
    canonical_evidence = set(evidence_aliases)
    evidenced_bases = {
        base.casefold() for base in evidence_aliases.values()
    }
    entries = [
        entry
        for entry in strings.entries()
        if naming.looks_like_contract(entry.key)
        or naming.canonical_key(entry.key).casefold() in canonical_evidence
    ]

    grouped: dict[str, dict[StringKind, list[tuple[str, str]]]] = {}
    # Taken from the original key, not the base: a key whose first segment is
    # the kind (`Name_Arken_Mallor...`) loses it during splitting, which would
    # otherwise shift the org token.
    org_tokens: dict[str, str] = {}

    for entry in entries:
        base, kind = naming.split_key(entry.key)
        canonical = naming.canonical_key(entry.key).casefold()
        base = evidence_aliases.get(canonical, base)
        kind = evidence_roles.get(canonical, kind)
        if kind is None:
            # No kind segment: long prose is a description, short text a title.
            kind = StringKind.DESC if len(entry.value) > 200 else StringKind.TITLE

        org_tokens.setdefault(base, naming.org_token(entry.key))
        variants = grouped.setdefault(base, {}).setdefault(kind, [])
        if not any(key == entry.key for key, _ in variants):
            variants.append((entry.key, entry.value))

    # Orgs are built from the contracts that survive filtering, so a namespace
    # that produced no contracts does not appear as a mission giver.
    casings: dict[str, Counter] = {}
    for base, parts in grouped.items():
        if is_contract(parts) or (
            base.casefold() in evidenced_bases
            and StringKind.DESC in parts
            and (
                StringKind.TITLE in parts
                or base.casefold() in desc_only_bases
            )
        ):
            token = org_tokens[base]
            casings.setdefault(token.casefold(), Counter())[token] += 1

    orgs = {
        org_id: Org(id=org_id, name=naming.display_name(org_id, seen))
        for org_id, seen in casings.items()
    }

    result = ContractSet(orgs=orgs)

    for base, parts in grouped.items():
        if not (
            is_contract(parts)
            or (
                base.casefold() in evidenced_bases
                and StringKind.DESC in parts
                and (
                    StringKind.TITLE in parts
                    or base.casefold() in desc_only_bases
                )
            )
        ):
            continue
        token = org_tokens[base]
        difficulty = naming.difficulty_of(base)
        texts = {key: value for variants in parts.values() for key, value in variants}

        result.contracts.append(
            Contract(
                id=base,
                org=orgs[token.casefold()],
                family=naming.family_of(base, token, difficulty),
                difficulty=difficulty,
                keys={
                    kind: [key for key, _ in variants]
                    for kind, variants in parts.items()
                },
                texts=texts,
                # The stock string *is* the base: nothing has been appended to
                # it, so there is no annotation to strip.
                base_texts=dict(texts),
            )
        )

    return result


def _valid_evidenced_key(key: str) -> bool:
    """Minimum safety shape for an exact typed contract-generator key."""

    canonical = naming.canonical_key(key)
    base, kind = naming.split_key(canonical)
    segments = canonical.split("_")
    if kind is None or len(segments) < 2 or not base:
        return False
    namespace = segments[0]
    return (
        len(namespace) > 1
        and namespace[0].isalpha()
        and namespace.casefold() not in naming.NON_MISSION_NAMESPACES
    )


def _evidence_aliases(
    evidenced_keys,
    groups: tuple[MissionKeyEvidence, ...] | list[MissionKeyEvidence],
) -> tuple[dict[str, str], dict[str, StringKind], set[str]]:
    """Map connected DataForge key groups onto one deterministic contract base."""

    parent: dict[str, str] = {}
    roles: dict[str, StringKind] = {}
    desc_only_roots: set[str] = set()

    def find(key: str) -> str:
        root = parent.setdefault(key, key)
        while root != parent[root]:
            root = parent[root]
        while key != root:
            next_key = parent[key]
            parent[key] = root
            key = next_key
        return root

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for key in evidenced_keys:
        canonical = naming.canonical_key(key).casefold()
        _base, kind = naming.split_key(key)
        if _valid_evidenced_key(key) and kind is not None:
            find(canonical)
            roles[canonical] = kind

    for group in groups:
        title_keys = [
            naming.canonical_key(key).casefold()
            for key in group.title_keys
            if _valid_typed_key(key)
        ]
        description_keys = [
            naming.canonical_key(key).casefold()
            for key in group.description_keys
            if _valid_typed_key(key)
        ]
        keys = [*title_keys, *description_keys]
        if not description_keys:
            continue
        for key in keys:
            find(key)
        for key in title_keys:
            _assign_evidence_role(roles, key, StringKind.TITLE)
        for key in description_keys:
            _assign_evidence_role(roles, key, StringKind.DESC)
        for key in keys[1:]:
            union(keys[0], key)
        if not title_keys:
            desc_only_roots.add(find(description_keys[0]))

    components: dict[str, list[str]] = {}
    for key in parent:
        components.setdefault(find(key), []).append(key)

    aliases: dict[str, str] = {}
    for keys in components.values():
        title_bases = sorted(
            naming.split_key(key)[0]
            for key in keys
            if roles.get(key) is StringKind.TITLE
        )
        bases = title_bases or sorted(naming.split_key(key)[0] for key in keys)
        alias = bases[0]
        aliases.update((key, alias) for key in keys)
    desc_only_bases = {
        aliases[key].casefold()
        for key in aliases
        if find(key) in {find(root) for root in desc_only_roots}
    }
    return aliases, roles, desc_only_bases


def _valid_typed_key(key: str) -> bool:
    canonical = naming.canonical_key(key)
    if canonical.casefold() in {"loc_uninitialized", "loc_placeholder"}:
        return False
    segments = canonical.split("_")
    if len(segments) < 2:
        return False
    namespace = segments[0]
    return (
        len(namespace) > 1
        and namespace[0].isalpha()
        and namespace.casefold() not in naming.NON_MISSION_NAMESPACES
    )


def _assign_evidence_role(
    roles: dict[str, StringKind],
    key: str,
    proposed: StringKind,
) -> None:
    """Prefer an explicit key kind when malformed facts claim both roles."""

    _base, named_kind = naming.split_key(key)
    if named_kind is not None:
        roles[key] = named_kind
    elif key not in roles:
        roles[key] = proposed
