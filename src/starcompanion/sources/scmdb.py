"""Read SCMDB exports.

[SCMDB](https://scmdb.net/) is Krovax's community database of Star Citizen
missions, crafting and mining. It holds the reward data that provably is not in
the client install (see docs/format-notes.md §4).

**Access policy.** SCMDB's `robots.txt` disallows `/data/` and `/data-nda/`, so
this module never fetches anything. It reads export files *you* download from
the site. Nothing here talks to the network.

**Schema confidence.** The blueprint-tracking export shape is known and handled
exactly. Other exports (contract pools, mining) are detected structurally, and
an unrecognised file is reported with what was actually seen rather than
guessed at -- a wrong guess would silently produce wrong reward data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SOURCE_NAME = "SCMDB"
SOURCE_URL = "https://scmdb.net/"


class ExportKind(Enum):
    BLUEPRINT_TRACKING = "blueprint-tracking"
    """A player's own blueprint collection: `blueprints[].name` / `.completed`."""
    CONTRACT_POOLS = "contract-pools"
    """Contracts with their reward pools."""
    RESOURCES = "resources"
    """Mineable resources by location."""
    UNKNOWN = "unknown"


class ScmdbError(Exception):
    pass


class UnrecognisedExportError(ScmdbError):
    """The file does not match a shape we know how to read.

    Deliberately loud: silently mis-reading an export would put wrong reward
    data into the user's game text.
    """

    def __init__(self, seen: object):
        super().__init__(
            f"this does not look like a SCMDB export we recognise. "
            f"Top-level structure: {seen}. "
            f"If SCMDB has added or changed an export, StarCompanion needs "
            f"updating -- please share the file's shape so it can be supported."
        )
        self.seen = seen


@dataclass
class Blueprint:
    name: str
    owned: bool = False


@dataclass
class ResourceLocation:
    location: str
    resources: list[str] = field(default_factory=list)


@dataclass
class ScmdbExport:
    kind: ExportKind
    blueprints: list[Blueprint] = field(default_factory=list)
    pools: dict[str, list[str]] = field(default_factory=dict)
    """Contract key or name -> blueprint names it can award."""
    resources: list[ResourceLocation] = field(default_factory=list)
    source_file: str | None = None

    @property
    def owned_blueprints(self) -> set[str]:
        return {b.name for b in self.blueprints if b.owned}

    @property
    def all_blueprints(self) -> set[str]:
        return {b.name for b in self.blueprints}

    def summary(self) -> str:
        parts = [f"{self.kind.value}"]
        if self.blueprints:
            parts.append(f"{len(self.blueprints):,} blueprints ({len(self.owned_blueprints):,} owned)")
        if self.pools:
            parts.append(f"{len(self.pools):,} contract pools")
        if self.resources:
            parts.append(f"{len(self.resources):,} locations")
        return ", ".join(parts)


# --- loading -----------------------------------------------------------------


def load(path: Path) -> ScmdbExport:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScmdbError(f"{path.name} is not valid JSON: {exc}") from exc

    export = parse(data)
    export.source_file = path.name
    return export


def load_latest(directory: Path, pattern: str = "scmdb*.json") -> ScmdbExport:
    """Most recently modified matching export in a directory.

    SCMDB downloads are timestamped, so this picks up the newest without the
    user renaming anything.
    """
    matches = sorted(
        Path(directory).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not matches:
        raise ScmdbError(f"no {pattern} found in {directory}")
    return load(matches[0])


def parse(data: Any) -> ScmdbExport:
    kind = detect(data)

    if kind is ExportKind.BLUEPRINT_TRACKING:
        return _parse_blueprints(data)
    if kind is ExportKind.CONTRACT_POOLS:
        return _parse_pools(data)
    if kind is ExportKind.RESOURCES:
        return _parse_resources(data)

    raise UnrecognisedExportError(_describe(data))


def detect(data: Any) -> ExportKind:
    """Identify an export by structure rather than filename."""
    if not isinstance(data, dict):
        return ExportKind.UNKNOWN

    if _is_blueprint_list(data.get("blueprints")):
        return ExportKind.BLUEPRINT_TRACKING

    for key in ("contracts", "missions", "pools"):
        if isinstance(data.get(key), (list, dict)):
            return ExportKind.CONTRACT_POOLS

    for key in ("resources", "locations", "mining"):
        if isinstance(data.get(key), (list, dict)):
            return ExportKind.RESOURCES

    return ExportKind.UNKNOWN


def _is_blueprint_list(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    first = value[0]
    return isinstance(first, dict) and "name" in first


# --- shape readers -----------------------------------------------------------


def _parse_blueprints(data: dict) -> ScmdbExport:
    """The known shape: `{"blueprints": [{"name": ..., "completed": bool}]}`."""
    blueprints = []
    for entry in data.get("blueprints", ()):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name:
            blueprints.append(Blueprint(name=name, owned=bool(entry.get("completed"))))

    return ScmdbExport(kind=ExportKind.BLUEPRINT_TRACKING, blueprints=blueprints)


def _parse_pools(data: dict) -> ScmdbExport:
    pools: dict[str, list[str]] = {}
    raw = _first_present(data, ("contracts", "missions", "pools"))

    entries = raw.items() if isinstance(raw, dict) else enumerate(raw)
    for key, entry in entries:
        if not isinstance(entry, dict):
            continue

        name = str(entry.get("key") or entry.get("name") or key).strip()
        items = _blueprint_names(
            _first_present(entry, ("blueprints", "rewards", "pool", "items"))
        )
        if name and items:
            pools[name] = items

    return ScmdbExport(kind=ExportKind.CONTRACT_POOLS, pools=pools)


def _parse_resources(data: dict) -> ScmdbExport:
    locations: list[ResourceLocation] = []
    raw = _first_present(data, ("resources", "locations", "mining"))

    entries = raw.items() if isinstance(raw, dict) else enumerate(raw)
    for key, entry in entries:
        if isinstance(entry, dict):
            name = str(entry.get("location") or entry.get("name") or key).strip()
            found = _blueprint_names(
                _first_present(entry, ("resources", "ores", "materials", "items"))
            )
        elif isinstance(entry, list):
            name, found = str(key), _blueprint_names(entry)
        else:
            continue

        if name and found:
            locations.append(ResourceLocation(location=name, resources=found))

    return ScmdbExport(kind=ExportKind.RESOURCES, resources=locations)


def _first_present(data: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return []


def _blueprint_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []

    names = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
            if name:
                names.append(name)
    return names


def _describe(data: Any) -> str:
    if isinstance(data, dict):
        keys = sorted(data)[:8]
        return f"object with keys {keys}" + (" …" if len(data) > 8 else "")
    if isinstance(data, list):
        return f"array of {len(data)} items"
    return type(data).__name__
