"""Discover contracts from the game's own string table.

This is the primary source. It reads the stock `global.ini` extracted from
`Data.p4k` and finds contracts by the naming convention in `naming.py` -- no
community file, no network, nothing but the user's install.

What it yields: which contracts exist, their mission giver, family, difficulty
rank, and the real title and description text.

What it cannot yield: reputation amounts and blueprint pools. Those are not in
the client at all -- established by scanning every text-like file in the archive
(docs/format-notes.md §4). They arrive later as an optional overlay from a
community source, and until then a contract simply has no reward, which is
honest rather than a zero.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..ini import LocalizationFile
from ..model import Contract, ContractSet, Org, StringKind
from . import naming


def load(path: Path) -> ContractSet:
    """Read a `global.ini` from disk."""
    return parse(LocalizationFile.load(Path(path)))


def from_install(install, language: str = "english") -> ContractSet:
    """Read the pristine strings straight out of the game archive.

    Preferred over the loose file: an install that already carries an override
    would otherwise feed its own annotations back in.
    """
    from ..extract.p4k import P4KArchive

    with P4KArchive(install.archive) as archive:
        data = archive.read_localization(language)

    return parse(LocalizationFile.loads(data.decode("utf-8", errors="replace")))


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


def parse(strings: LocalizationFile) -> ContractSet:
    entries = [e for e in strings.entries() if naming.looks_like_contract(e.key)]

    grouped: dict[str, dict[StringKind, list[tuple[str, str]]]] = {}
    # Taken from the original key, not the base: a key whose first segment is
    # the kind (`Name_Arken_Mallor...`) loses it during splitting, which would
    # otherwise shift the org token.
    org_tokens: dict[str, str] = {}

    for entry in entries:
        base, kind = naming.split_key(entry.key)
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
        if is_contract(parts):
            token = org_tokens[base]
            casings.setdefault(token.casefold(), Counter())[token] += 1

    orgs = {
        org_id: Org(id=org_id, name=naming.display_name(org_id, seen))
        for org_id, seen in casings.items()
    }

    result = ContractSet(orgs=orgs)

    for base, parts in grouped.items():
        if not is_contract(parts):
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
