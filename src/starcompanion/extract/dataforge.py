"""Typed, evidence-carrying views over DataCore's DataForge records.

The binary reader deliberately decodes only one record at a time.  This module
adds the relationships needed by mission extraction without turning the whole
database into an in-memory object tree.  Every derived value retains its source
record and field path; unresolved or ambiguous references are diagnostics, not
guesses.
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict, defaultdict, deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID

from .datacore import Record, StructDefinition

NULL_UUID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_LOCALE_KEYS = frozenset(
    {"loc_uninitialized", "loc_placeholder", "loc_empty"}
)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class RecordSource(Protocol):
    """The small DataCore surface consumed by :class:`DataForgeIndex`."""

    version: int
    structs: list[StructDefinition]
    records: list[Record]

    def read_record(self, record: Record, *, max_depth: int = 0) -> dict[str, Any]: ...


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCategory(StrEnum):
    """Operational meaning of an extractor diagnostic."""

    OPTIONAL_REFERENCE = "optional-reference"
    DATA_GAP = "data-gap"
    SCHEMA_DRIFT = "schema-drift"
    INTEGRITY = "integrity"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ScalarKind(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    UUID = "uuid"
    LOCALE_KEY = "locale-key"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: Severity = Severity.WARNING
    record_id: str | None = None
    record_path: str | None = None
    field_path: str | None = None
    breadcrumbs: tuple[str, ...] = ()

    @property
    def category(self) -> DiagnosticCategory:
        if self.code in {
            "missing-reference-target",
            "invalid-reference",
            "pointer-cycle",
            "missing-pointer-target",
            "reference-hop-limit",
            "reference-cycle",
        }:
            return DiagnosticCategory.OPTIONAL_REFERENCE
        if self.code in {
            "mission-title-missing",
            "blueprint-name-fallback",
            "item-name-fallback",
        }:
            return DiagnosticCategory.DATA_GAP
        if "schema-drift" in self.code or self.code.endswith("provider-missing"):
            return DiagnosticCategory.SCHEMA_DRIFT
        if self.code in {
            "invalid-struct-index",
            "duplicate-guid",
            "ambiguous-reference",
            "record-walk-limit",
            "record-depth-limit",
        }:
            return DiagnosticCategory.INTEGRITY
        return DiagnosticCategory.DATA_GAP

    @property
    def degrades_capability(self) -> bool:
        return self.category in {
            DiagnosticCategory.SCHEMA_DRIFT,
            DiagnosticCategory.INTEGRITY,
        }


@dataclass(frozen=True)
class Evidence:
    """The exact record field supporting one emitted fact."""

    record_id: str
    record_path: str
    field_path: str
    value: str | int | float | bool


@dataclass(frozen=True)
class RecordNode:
    record: Record
    struct_name: str
    normalized_path: str

    @property
    def id(self) -> str:
        return self.record.guid


@dataclass(frozen=True)
class ReferenceEdge:
    source_id: str
    source_path: str
    field_path: str
    target_id: str
    target: RecordNode | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class Resolution:
    reference: str
    target: RecordNode | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class FieldValue:
    path: str
    value: Any


@dataclass(frozen=True)
class ScalarConversion:
    value: str | int | float | bool | None
    diagnostic: Diagnostic | None = None


@dataclass(frozen=True)
class BlueprintPoolFacts:
    pool_id: str
    items: tuple[str, ...]
    chance: float | None = None
    evidence: tuple[Evidence, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    item_ids: tuple[str, ...] = ()
    item_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissionFacts:
    mission_id: str
    title_keys: tuple[str, ...] = ()
    description_keys: tuple[str, ...] = ()
    reputation: tuple[int, ...] = ()
    blueprint_pools: tuple[BlueprintPoolFacts, ...] = ()
    item_rewards: tuple[str, ...] = ()
    source_record_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    confidence: Confidence = Confidence.NONE
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class CapabilityReport:
    provider: str
    status: CapabilityStatus
    build_version: int | str
    records_examined: int
    facts_emitted: int
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class MissionExtractionResult:
    facts: tuple[MissionFacts, ...]
    capability: CapabilityReport


def normalize_record_path(path: str) -> str:
    """Return one stable, case-insensitive path form for DataForge indexes."""

    cleaned = path.replace("\\", "/").strip()
    parts: list[str] = []
    for part in cleaned.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part.casefold())
    return str(PurePosixPath(*parts)) if parts else ""


def normalize_uuid(value: object) -> str | None:
    if not isinstance(value, str) or not _UUID_PATTERN.fullmatch(value.strip()):
        return None
    try:
        normalized = str(UUID(value.strip()))
    except ValueError:
        return None
    return None if normalized == NULL_UUID else normalized


def convert_scalar(
    found: FieldValue,
    kind: ScalarKind,
    *,
    node: RecordNode | None = None,
) -> ScalarConversion:
    """Convert a decoded field without Python's lossy implicit coercions."""

    value = found.value
    converted: str | int | float | bool | None = None
    try:
        if kind is ScalarKind.STRING:
            converted = value if isinstance(value, str) else None
        elif kind is ScalarKind.UUID:
            converted = normalize_uuid(value)
        elif kind is ScalarKind.LOCALE_KEY:
            converted = _locale_key(value)
        elif kind is ScalarKind.BOOLEAN:
            if isinstance(value, bool):
                converted = value
            elif isinstance(value, str) and value.strip().casefold() in {"true", "false", "1", "0"}:
                converted = value.strip().casefold() in {"true", "1"}
            elif isinstance(value, int) and value in (0, 1):
                converted = bool(value)
        elif kind is ScalarKind.INTEGER:
            if isinstance(value, bool):
                converted = None
            elif isinstance(value, int):
                converted = value
            elif isinstance(value, (float, str)):
                number = float(value)
                if math.isfinite(number) and number.is_integer():
                    converted = int(number)
        elif kind is ScalarKind.FLOAT:
            if not isinstance(value, bool) and isinstance(value, (int, float, str)):
                number = float(value)
                converted = number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        converted = None
    if converted is not None:
        return ScalarConversion(converted)
    return ScalarConversion(
        None,
        Diagnostic(
            "scalar-conversion-failed",
            f"Cannot convert {value!r} to {kind.value}",
            Severity.WARNING,
            node.id if node else None,
            node.normalized_path if node else None,
            found.path,
        ),
    )


class DataForgeIndex:
    """Lazy record graph with bounded payload caching and traversal.

    Duplicate GUIDs and paths remain represented as multiple nodes.  Callers
    therefore cannot accidentally inherit DataCore's legacy "first wins"
    behavior when a build is corrupt or its identity contract changes.
    """

    def __init__(
        self,
        source: RecordSource,
        *,
        payload_cache_size: int = 128,
        max_walk_nodes: int = 100_000,
        max_walk_depth: int = 64,
    ) -> None:
        if payload_cache_size < 0:
            raise ValueError("payload_cache_size must not be negative")
        self.source = source
        self.payload_cache_size = payload_cache_size
        self.max_walk_nodes = max_walk_nodes
        self.max_walk_depth = max_walk_depth
        self._payloads: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._instances: OrderedDict[tuple[int, int], dict[str, Any]] = OrderedDict()
        self.by_guid: dict[str, list[RecordNode]] = defaultdict(list)
        self.by_path: dict[str, list[RecordNode]] = defaultdict(list)
        self.by_filename: dict[str, list[RecordNode]] = defaultdict(list)
        self.by_struct: dict[str, list[RecordNode]] = defaultdict(list)
        self.nodes: list[RecordNode] = []
        self.diagnostics: list[Diagnostic] = []
        self._build()

    def _build(self) -> None:
        for record in self.source.records:
            path = normalize_record_path(record.file_name)
            if 0 <= record.struct_index < len(self.source.structs):
                struct_name = self.source.structs[record.struct_index].name
            else:
                struct_name = f"<invalid:{record.struct_index}>"
                self.diagnostics.append(
                    Diagnostic(
                        "invalid-struct-index",
                        f"Record uses missing struct index {record.struct_index}",
                        Severity.ERROR,
                        record.guid,
                        path,
                    )
                )
            node = RecordNode(record, struct_name, path)
            self.nodes.append(node)
            guid = normalize_uuid(record.guid)
            if guid:
                self.by_guid[guid].append(node)
            self.by_path[path].append(node)
            self.by_filename[PurePosixPath(path).name].append(node)
            self.by_struct[struct_name.casefold()].append(node)

        for guid, nodes in self.by_guid.items():
            if len(nodes) > 1:
                self.diagnostics.append(
                    Diagnostic(
                        "duplicate-guid",
                        f"GUID {guid} identifies {len(nodes)} records",
                        Severity.ERROR,
                        guid,
                        breadcrumbs=tuple(n.normalized_path for n in nodes),
                    )
                )

    def records_under(self, fragment: str) -> tuple[RecordNode, ...]:
        needle = normalize_record_path(fragment)
        return tuple(node for node in self.nodes if needle in node.normalized_path)

    def records_of(self, struct_name: str) -> tuple[RecordNode, ...]:
        return tuple(self.by_struct.get(struct_name.casefold(), ()))

    def payload(self, node: RecordNode) -> dict[str, Any]:
        """Decode one record and retain at most ``payload_cache_size`` trees."""

        key = id(node.record)
        cached = self._payloads.pop(key, None)
        if cached is not None:
            self._payloads[key] = cached
            return cached
        payload = self.source.read_record(node.record, max_depth=0)
        if self.payload_cache_size:
            self._payloads[key] = payload
            self._trim_payload_cache()
        return payload

    @property
    def cached_payload_count(self) -> int:
        return len(self._payloads) + len(self._instances)

    def _instance_payload(self, struct_index: int, instance_index: int) -> dict[str, Any] | None:
        reader = getattr(self.source, "read_instance", None)
        if not callable(reader):
            return None
        key = (struct_index, instance_index)
        cached = self._instances.pop(key, None)
        if cached is not None:
            self._instances[key] = cached
            return cached
        payload = reader(struct_index, instance_index, max_depth=0)
        if not isinstance(payload, dict):
            return None
        payload = dict(payload)
        if 0 <= struct_index < len(self.source.structs):
            payload.setdefault("$type", self.source.structs[struct_index].name)
        if self.payload_cache_size:
            self._instances[key] = payload
            self._trim_payload_cache()
        return payload

    def _trim_payload_cache(self) -> None:
        while len(self._payloads) + len(self._instances) > self.payload_cache_size:
            # Anonymous instances fan out much faster than root records, so
            # evict those first while retaining the caller's current root.
            if self._instances:
                self._instances.popitem(last=False)
            elif self._payloads:
                self._payloads.popitem(last=False)

    def resolve(
        self,
        reference: object,
        *,
        source: RecordNode | None = None,
        field_path: str | None = None,
        breadcrumbs: Sequence[str] = (),
    ) -> Resolution:
        guid = normalize_uuid(reference)
        location = source.normalized_path if source else None
        source_id = source.id if source else None
        if guid is None:
            diagnostic = Diagnostic(
                "invalid-reference",
                f"Reference is not a non-null UUID: {reference!r}",
                Severity.WARNING,
                source_id,
                location,
                field_path,
                tuple(breadcrumbs),
            )
            return Resolution(str(reference), None, (diagnostic,))
        matches = self.by_guid.get(guid, ())
        if not matches:
            diagnostic = Diagnostic(
                "missing-reference-target",
                f"No record has GUID {guid}",
                Severity.WARNING,
                source_id,
                location,
                field_path,
                tuple((*breadcrumbs, guid)),
            )
            return Resolution(guid, None, (diagnostic,))
        if len(matches) > 1:
            diagnostic = Diagnostic(
                "ambiguous-reference",
                f"GUID {guid} resolves to {len(matches)} records",
                Severity.ERROR,
                source_id,
                location,
                field_path,
                tuple((*breadcrumbs, *(n.normalized_path for n in matches))),
            )
            return Resolution(guid, None, (diagnostic,))
        return Resolution(guid, matches[0])

    def iter_fields(
        self,
        node: RecordNode,
        *,
        key: str | None = None,
    ) -> Iterator[FieldValue]:
        """Stream nested fields with deterministic bounds and field paths."""

        yield from self.iter_value_fields(self.payload(node), key=key, diagnostic_node=node)

    def iter_value_fields(
        self,
        value: Any,
        *,
        key: str | None = None,
        root_path: str = "$",
        diagnostic_node: RecordNode | None = None,
    ) -> Iterator[FieldValue]:
        """Stream a subtree, preserving its path within the source record."""

        wanted = key.casefold() if key else None
        stack: list[tuple[Any, str, int, frozenset[tuple[int, int]]]] = [
            (value, root_path, 0, frozenset())
        ]
        visited = 0
        while stack:
            value, path, depth, pointer_chain = stack.pop()
            visited += 1
            if visited > self.max_walk_nodes:
                self.diagnostics.append(
                    Diagnostic(
                        "record-walk-limit",
                        f"Stopped after {self.max_walk_nodes:,} nested values",
                        Severity.ERROR,
                        diagnostic_node.id if diagnostic_node else None,
                        diagnostic_node.normalized_path if diagnostic_node else None,
                        path,
                    )
                )
                return
            if depth > self.max_walk_depth:
                self.diagnostics.append(
                    Diagnostic(
                        "record-depth-limit",
                        f"Stopped below depth {self.max_walk_depth}",
                        Severity.ERROR,
                        diagnostic_node.id if diagnostic_node else None,
                        diagnostic_node.normalized_path if diagnostic_node else None,
                        path,
                    )
                )
                continue
            if isinstance(value, Mapping):
                struct_index = value.get("$struct")
                instance_index = value.get("$instance")
                if (
                    isinstance(struct_index, int)
                    and not isinstance(struct_index, bool)
                    and isinstance(instance_index, int)
                    and not isinstance(instance_index, bool)
                ):
                    pointer = (struct_index, instance_index)
                    pointer_path = f"{path}->$[{struct_index}:{instance_index}]"
                    if pointer in pointer_chain:
                        self.diagnostics.append(
                            Diagnostic(
                                "pointer-cycle",
                                f"DataCore pointer cycle reaches struct {struct_index}, instance {instance_index}",
                                Severity.WARNING,
                                diagnostic_node.id if diagnostic_node else None,
                                diagnostic_node.normalized_path if diagnostic_node else None,
                                pointer_path,
                            )
                        )
                        continue
                    pointed = self._instance_payload(struct_index, instance_index)
                    if pointed is None:
                        self.diagnostics.append(
                            Diagnostic(
                                "missing-pointer-target",
                                f"Cannot read struct {struct_index}, instance {instance_index}",
                                Severity.WARNING,
                                diagnostic_node.id if diagnostic_node else None,
                                diagnostic_node.normalized_path if diagnostic_node else None,
                                pointer_path,
                            )
                        )
                        continue
                    if wanted is None:
                        yield FieldValue(pointer_path, pointed)
                    stack.append((pointed, pointer_path, depth + 1, pointer_chain | {pointer}))
                    continue
                items = list(value.items())
                for child_key, child in reversed(items):
                    child_path = f"{path}.{child_key}"
                    if wanted is None or str(child_key).casefold() == wanted:
                        yield FieldValue(child_path, child)
                    stack.append((child, child_path, depth + 1, pointer_chain))
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                if wanted is None:
                    for index, child in enumerate(value):
                        yield FieldValue(f"{path}[{index}]", child)
                for index in range(len(value) - 1, -1, -1):
                    child_path = f"{path}[{index}]"
                    child = value[index]
                    stack.append((child, child_path, depth + 1, pointer_chain))

    def reference_edges(self, node: RecordNode) -> Iterator[ReferenceEdge]:
        for found in self.iter_fields(node):
            guid = normalize_uuid(found.value)
            if not guid:
                continue
            resolved = self.resolve(found.value, source=node, field_path=found.path)
            yield ReferenceEdge(
                node.id,
                node.normalized_path,
                found.path,
                guid,
                resolved.target,
                resolved.diagnostics,
            )

    def walk_references(
        self, start: RecordNode, *, max_hops: int = 32
    ) -> tuple[tuple[RecordNode, ...], tuple[Diagnostic, ...]]:
        """Breadth-first graph traversal with missing, duplicate and cycle evidence."""

        found: list[RecordNode] = []
        diagnostics: list[Diagnostic] = []
        start_identity = normalize_uuid(start.id) or f"path:{start.normalized_path}"
        queue: deque[tuple[RecordNode, tuple[str, ...], frozenset[str], int]] = deque(
            [(start, (start.normalized_path,), frozenset((start_identity,)), 0)]
        )
        visited: set[str] = set()
        while queue:
            node, crumbs, ancestry, depth = queue.popleft()
            identity = normalize_uuid(node.id) or f"path:{node.normalized_path}"
            if identity in visited:
                continue
            visited.add(identity)
            found.append(node)
            if depth >= max_hops:
                diagnostics.append(
                    Diagnostic(
                        "reference-hop-limit",
                        f"Reference traversal stopped at {max_hops} hops",
                        Severity.WARNING,
                        node.id,
                        node.normalized_path,
                        breadcrumbs=crumbs,
                    )
                )
                continue
            for edge in self.reference_edges(node):
                diagnostics.extend(edge.diagnostics)
                if edge.target:
                    target_identity = normalize_uuid(edge.target.id) or f"path:{edge.target.normalized_path}"
                    next_crumbs = (*crumbs, f"{edge.field_path} -> {edge.target.normalized_path}")
                    if target_identity in ancestry:
                        diagnostics.append(
                            Diagnostic(
                                "reference-cycle",
                                f"Reference cycle reaches {edge.target.normalized_path}",
                                Severity.WARNING,
                                edge.target.id,
                                edge.target.normalized_path,
                                edge.field_path,
                                next_crumbs,
                            )
                        )
                        continue
                    queue.append(
                        (
                            edge.target,
                            next_crumbs,
                            ancestry | {target_identity},
                            depth + 1,
                        )
                    )
        return tuple(found), tuple(diagnostics)


def _evidence(node: RecordNode, found: FieldValue, value: Any | None = None) -> Evidence:
    actual = found.value if value is None else value
    if not isinstance(actual, (str, int, float, bool)):
        actual = repr(actual)
    return Evidence(node.id, node.normalized_path, found.path, actual)


def _locale_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    return value or None


def _first_scalar(mapping: Mapping[str, Any], names: Sequence[str]) -> tuple[str, Any] | None:
    wanted = {name.casefold() for name in names}
    return next(
        ((str(key), value) for key, value in mapping.items() if str(key).casefold() in wanted),
        None,
    )


def _iter_named_objects(
    index: DataForgeIndex,
    node: RecordNode,
    name: str,
    scope: _RecordScope | None = None,
) -> Iterator[FieldValue]:
    seen: set[str] = set()
    candidates = (
        iter(scope.fields)
        if scope
        else index.iter_fields(node)
    )
    for found in candidates:
        # Unforge's XML element name is often the anonymous instance's struct
        # type, not a property name retained in the binary tree. Match either
        # representation in one traversal.
        last_segment = found.path.rsplit(".", 1)[-1].split("[", 1)[0]
        property_match = last_segment.casefold() == name.casefold()
        type_match = (
            isinstance(found.value, Mapping)
            and str(found.value.get("$type", "")).casefold() == name.casefold()
        )
        if not property_match and not type_match:
            continue
        values = found.value if isinstance(found.value, list) else [found.value]
        for position, value in enumerate(values):
            if not isinstance(value, Mapping):
                continue
            suffix = f"[{position}]" if isinstance(found.value, list) else ""
            path = found.path + suffix
            if path not in seen:
                seen.add(path)
                yield FieldValue(path, value)


def _build_reputation_lookup(
    index: DataForgeIndex,
) -> tuple[dict[str, tuple[int, Evidence]], list[Diagnostic]]:
    lookup: dict[str, tuple[int, Evidence]] = {}
    diagnostics: list[Diagnostic] = []
    nodes = index.records_under("records/reputation/rewards/missionrewards_reputation")
    for node in nodes:
        amount_fields = list(index.iter_fields(node, key="reputationAmount"))
        if not amount_fields:
            diagnostics.append(
                Diagnostic(
                    "reputation-schema-drift",
                    "Reputation reward record has no reputationAmount field",
                    Severity.WARNING,
                    node.id,
                    node.normalized_path,
                )
            )
            continue
        value = amount_fields[0].value
        converted = convert_scalar(amount_fields[0], ScalarKind.INTEGER, node=node)
        if converted.value is None:
            diagnostics.append(
                Diagnostic(
                    "invalid-reputation-amount",
                    f"reputationAmount is not numeric: {value!r}",
                    Severity.WARNING,
                    node.id,
                    node.normalized_path,
                    amount_fields[0].path,
                )
            )
            continue
        amount = int(converted.value)
        guid = normalize_uuid(node.id)
        if guid and len(index.by_guid.get(guid, ())) == 1:
            lookup[guid] = (amount, _evidence(node, amount_fields[0], amount))
    return lookup, diagnostics


def _display_name(index: DataForgeIndex, node: RecordNode) -> tuple[str, Evidence, bool]:
    candidates: list[tuple[int, int, str, FieldValue]] = []
    for found in index.iter_fields(node):
        leaf = found.path.rsplit(".", 1)[-1].casefold()
        if leaf not in {"displayname", "name", "title"}:
            continue
        # Entity records contain hundreds of nested interaction names such as
        # Carry/Drop. Item display names are localization references; plain
        # nested labels are never safe blueprint identities.
        if not isinstance(found.value, str) or not found.value.strip().startswith("@"):
            continue
        value = _locale_key(found.value)
        if not value:
            continue
        lowered = value.casefold()
        if lowered in PLACEHOLDER_LOCALE_KEYS or lowered.startswith("interaction_"):
            continue
        path = found.path.casefold()
        if ".attachdef.localization.name" in path:
            semantic_rank = 0
        elif lowered.startswith(("item_name_", "vehicle_name_")):
            semantic_rank = 1
        elif path.endswith(".displayname"):
            semantic_rank = 2
        else:
            semantic_rank = 3
        depth = path.count(".") + path.count("->")
        candidates.append((semantic_rank, depth, path, found))
    if candidates:
        _rank, _depth, _path, found = min(candidates)
        value = _locale_key(found.value)
        assert value is not None
        return value, _evidence(node, found, value), False
    stem = PurePosixPath(node.normalized_path).stem
    for prefix in ("bp_craft_", "bp_rewards_", "bp_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    name = stem.replace("_", " ").strip()
    return (
        name,
        Evidence(node.id, node.normalized_path, "$record.file_name", node.record.file_name),
        True,
    )


@dataclass(frozen=True)
class _ExpandedPool:
    items: tuple[str, ...]
    item_ids: tuple[str, ...]
    item_categories: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class _BrokerReputation:
    broker_id: str
    values: tuple[int, ...]
    evidence: tuple[Evidence, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class _RecordScope:
    path: str
    value: Mapping[str, Any]
    fields: tuple[FieldValue, ...]


def _make_scope(
    index: DataForgeIndex,
    node: RecordNode,
    path: str,
    value: Mapping[str, Any],
) -> _RecordScope:
    return _RecordScope(
        path,
        value,
        tuple(
            index.iter_value_fields(
                value,
                root_path=path,
                diagnostic_node=node,
            )
        ),
    )


def _field_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[", 1)[0]


def _scope_fields(
    index: DataForgeIndex,
    node: RecordNode,
    scope: _RecordScope,
    key: str,
) -> Iterator[FieldValue]:
    wanted = key.casefold()
    yield from (found for found in scope.fields if _field_name(found.path).casefold() == wanted)


def _contract_scopes(index: DataForgeIndex, node: RecordNode) -> tuple[_RecordScope, ...]:
    """Split generator files into their nested Career/List contract variants."""

    root_value = index.payload(node)
    root_fields = tuple(index.iter_value_fields(root_value, diagnostic_node=node))
    scopes: list[_RecordScope] = []
    for found in root_fields:
        if not isinstance(found.value, Mapping):
            continue
        property_name = _field_name(found.path)
        type_name = str(found.value.get("$type", ""))
        if property_name in {"CareerContract", "Contract"} or type_name in {
            "CareerContract",
            "Contract",
        }:
            scopes.append(_make_scope(index, node, found.path, found.value))
    if scopes:
        # Path de-dup protects against schema aliases without merging variants.
        return tuple({scope.path: scope for scope in scopes}.values())
    return (_RecordScope("$", root_value, root_fields),)


def _build_blueprint_pools(
    index: DataForgeIndex,
) -> tuple[dict[str, _ExpandedPool], list[Diagnostic]]:
    pools: dict[str, _ExpandedPool] = {}
    diagnostics: list[Diagnostic] = []
    for pool in index.records_under("records/crafting/blueprintrewards"):
        items: list[str] = []
        item_ids: list[str] = []
        item_categories: list[str] = []
        pool_evidence: list[Evidence] = []
        pool_diags: list[Diagnostic] = []
        refs = list(index.iter_fields(pool, key="blueprintRecord"))
        if not refs:
            pool_diags.append(
                Diagnostic(
                    "blueprint-pool-schema-drift",
                    "Blueprint pool has no blueprintRecord fields",
                    Severity.WARNING,
                    pool.id,
                    pool.normalized_path,
                )
            )
        for found in refs:
            if found.value is None or (
                isinstance(found.value, str) and found.value.casefold() == NULL_UUID
            ):
                continue
            resolution = index.resolve(
                found.value,
                source=pool,
                field_path=found.path,
                breadcrumbs=(pool.normalized_path,),
            )
            pool_diags.extend(resolution.diagnostics)
            if not resolution.target:
                continue
            target = resolution.target
            if "records/crafting/blueprints/crafting" not in target.normalized_path:
                pool_diags.append(
                    Diagnostic(
                        "unexpected-blueprint-target",
                        "blueprintRecord resolves outside the crafting blueprint tree",
                        Severity.WARNING,
                        target.id,
                        target.normalized_path,
                        breadcrumbs=(pool.normalized_path, found.path),
                    )
                )
            pool_evidence.append(_evidence(pool, found, resolution.reference))

            entity_fields = list(index.iter_fields(target, key="entityClass"))
            named_node = target
            if entity_fields:
                entity = index.resolve(
                    entity_fields[0].value,
                    source=target,
                    field_path=entity_fields[0].path,
                    breadcrumbs=(pool.normalized_path, target.normalized_path),
                )
                pool_diags.extend(entity.diagnostics)
                if entity.target:
                    named_node = entity.target
                    pool_evidence.append(_evidence(target, entity_fields[0], entity.reference))
            name, name_evidence, used_fallback = _display_name(index, named_node)
            if name and name not in items:
                items.append(name)
                item_ids.append(
                    normalize_uuid(named_node.id) or f"path:{named_node.normalized_path}"
                )
                parts = named_node.normalized_path.split("/")
                try:
                    entity_index = parts.index("entities")
                    category = parts[entity_index + 2] if len(parts) > entity_index + 2 else "unknown"
                except ValueError:
                    category = "unknown"
                item_categories.append(category or "unknown")
                pool_evidence.append(name_evidence)
                if used_fallback:
                    pool_diags.append(
                        Diagnostic(
                            "blueprint-name-fallback",
                            f"Used filename-derived blueprint name {name!r}",
                            Severity.WARNING,
                            named_node.id,
                            named_node.normalized_path,
                        )
                    )

        guid = normalize_uuid(pool.id)
        if guid and len(index.by_guid.get(guid, ())) == 1:
            expanded = _ExpandedPool(
                tuple(items),
                tuple(item_ids),
                tuple(item_categories),
                tuple(pool_evidence),
                tuple(pool_diags),
            )
            pools[guid] = expanded
            diagnostics.extend(pool_diags)
    return pools, diagnostics


def _contract_localization(
    index: DataForgeIndex, node: RecordNode, scope: _RecordScope
) -> tuple[list[str], list[str], list[Evidence]]:
    titles: list[str] = []
    descriptions: list[str] = []
    evidence: list[Evidence] = []

    for field_name, bucket in (
        ("title", titles),
        ("titleKey", titles),
        ("description", descriptions),
        ("descriptionKey", descriptions),
    ):
        for found in _scope_fields(index, node, scope, field_name):
            key = _locale_key(found.value)
            if key and key not in bucket:
                bucket.append(key)
                evidence.append(_evidence(node, found, key))

    # DataForge contract generators commonly encode localization as a list of
    # ContractStringParam objects: {param: Title|Description, value: @key}.
    # The binary schema does not always retain the XML element name, so accept
    # the typed shape itself as well as an explicit ContractStringParam wrapper.
    candidates = list(_iter_named_objects(index, node, "ContractStringParam", scope))
    candidates.extend(
        found
        for found in scope.fields
        if isinstance(found.value, Mapping)
    )
    seen_candidates: set[str] = set()
    for found in candidates:
        if found.path in seen_candidates:
            continue
        seen_candidates.add(found.path)
        param = _first_scalar(found.value, ("param", "name"))
        value = _first_scalar(found.value, ("value", "string", "text"))
        if not param or not value:
            continue
        key = _locale_key(value[1])
        if not key:
            continue
        bucket = titles if str(param[1]).casefold() == "title" else descriptions
        if str(param[1]).casefold() not in {"title", "description"}:
            continue
        if key not in bucket:
            bucket.append(key)
            evidence.append(
                Evidence(node.id, node.normalized_path, f"{found.path}.{value[0]}", key)
            )
    return titles, descriptions, evidence


def _mission_reputation(
    index: DataForgeIndex,
    node: RecordNode,
    scope: _RecordScope,
    lookup: Mapping[str, tuple[int, Evidence]],
) -> tuple[list[int], list[Evidence], list[Diagnostic]]:
    evidence: list[Evidence] = []
    diagnostics: list[Diagnostic] = []
    outcomes = list(_iter_named_objects(index, node, "SReputationAmountListParams", scope))
    if not outcomes:
        return [], evidence, diagnostics

    first = outcomes[0]
    first_scope = _make_scope(index, node, first.path, first.value)
    amount_objects = list(
        _iter_named_objects(index, node, "SReputationAmountParams", first_scope)
    )
    rewards: list[tuple[str | None, FieldValue]] = []
    for amount_object in amount_objects:
        reward = _first_scalar(amount_object.value, ("reward",))
        scope_value = _first_scalar(amount_object.value, ("reputationScope",))
        if reward and reward[1] is not None and (
            not isinstance(reward[1], str) or reward[1].casefold() != NULL_UUID
        ):
            rewards.append(
                (
                    str(scope_value[1]) if scope_value else None,
                    FieldValue(f"{amount_object.path}.{reward[0]}", reward[1]),
                )
            )

    if not rewards:
        populated = any(
            bool(found.value)
            for found in _scope_fields(
                index, node, first_scope, "reputationAmounts"
            )
        )
        if populated:
            diagnostics.append(
                Diagnostic(
                    "reputation-outcome-schema-drift",
                    "Populated success reputation list contains no SReputationAmountParams rewards",
                    Severity.WARNING,
                    node.id,
                    node.normalized_path,
                    first.path,
                )
            )
        return [], evidence, diagnostics

    primary_scope = rewards[0][0]
    total = 0
    resolved_count = 0
    for scope, found in rewards:
        if scope != primary_scope:
            continue
        guid = normalize_uuid(found.value)
        resolution = index.resolve(
            found.value,
            source=node,
            field_path=found.path,
            breadcrumbs=(node.normalized_path, first.path),
        )
        diagnostics.extend(resolution.diagnostics)
        if not resolution.target or not guid or guid not in lookup:
            continue
        amount, amount_evidence = lookup[guid]
        total += amount
        resolved_count += 1
        evidence.extend((_evidence(node, found, guid), amount_evidence))
    return ([total] if resolved_count else []), evidence, diagnostics


def _build_broker_reputation(
    index: DataForgeIndex,
    lookup: Mapping[str, tuple[int, Evidence]],
) -> tuple[dict[str, tuple[_BrokerReputation, ...]], list[Diagnostic]]:
    """Join broker outcomes to generator variants by stock localization key."""

    by_key: dict[str, list[_BrokerReputation]] = defaultdict(list)
    diagnostics: list[Diagnostic] = []
    for node in index.records_of("MissionBrokerEntry"):
        payload = index.payload(node)
        keys: list[str] = []
        key_evidence: list[Evidence] = []
        for field_name in ("title", "titleHUD", "description"):
            found = FieldValue(f"$.{field_name}", payload.get(field_name))
            key = _locale_key(found.value)
            if key and key not in {"LOC_UNINITIALIZED", "LOC_PLACEHOLDER"} and key not in keys:
                keys.append(key)
                key_evidence.append(_evidence(node, found, key))
        if not keys:
            continue
        scope = _make_scope(index, node, "$", payload)
        values, reward_evidence, reward_diagnostics = _mission_reputation(
            index, node, scope, lookup
        )
        diagnostics.extend(reward_diagnostics)
        if not values:
            continue
        linked = _BrokerReputation(
            node.id,
            tuple(values),
            tuple((*key_evidence, *reward_evidence)),
            tuple(reward_diagnostics),
        )
        for key in keys:
            by_key[key].append(linked)
    return {key: tuple(values) for key, values in by_key.items()}, diagnostics


def _mission_blueprints(
    index: DataForgeIndex,
    node: RecordNode,
    scope: _RecordScope,
    pools: Mapping[str, _ExpandedPool],
) -> tuple[list[BlueprintPoolFacts], list[Evidence], list[Diagnostic]]:
    facts: list[BlueprintPoolFacts] = []
    evidence: list[Evidence] = []
    diagnostics: list[Diagnostic] = []
    for found in _scope_fields(index, node, scope, "blueprintPool"):
        guid = normalize_uuid(found.value)
        resolution = index.resolve(
            found.value,
            source=node,
            field_path=found.path,
            breadcrumbs=(node.normalized_path,),
        )
        diagnostics.extend(resolution.diagnostics)
        if not guid or guid not in pools:
            if resolution.target and guid not in pools:
                diagnostics.append(
                    Diagnostic(
                        "unindexed-blueprint-pool",
                        "Referenced record is not a recognized blueprint pool",
                        Severity.WARNING,
                        node.id,
                        node.normalized_path,
                        found.path,
                    )
                )
            continue
        expanded = pools[guid]
        chance: float | None = None
        chance_evidence: Evidence | None = None
        parent_path = found.path.rsplit(".", 1)[0]
        for chance_field in _scope_fields(index, node, scope, "chance"):
            if chance_field.path.rsplit(".", 1)[0] == parent_path:
                try:
                    converted = convert_scalar(chance_field, ScalarKind.FLOAT, node=node)
                    chance = float(converted.value) if converted.value is not None else None
                    if chance is not None:
                        chance_evidence = _evidence(node, chance_field, chance)
                except (TypeError, ValueError, OverflowError):
                    chance = None
                if chance is None:
                    diagnostics.append(
                        Diagnostic(
                            "invalid-blueprint-chance",
                            f"Blueprint chance is not numeric: {chance_field.value!r}",
                            Severity.WARNING,
                            node.id,
                            node.normalized_path,
                            chance_field.path,
                        )
                    )
                break
        direct = _evidence(node, found, guid)
        pool_evidence = (
            direct,
            *((chance_evidence,) if chance_evidence else ()),
            *expanded.evidence,
        )
        evidence.extend(pool_evidence)
        diagnostics.extend(expanded.diagnostics)
        facts.append(
            BlueprintPoolFacts(
                pool_id=guid,
                items=expanded.items,
                item_ids=expanded.item_ids,
                item_categories=expanded.item_categories,
                chance=chance,
                evidence=tuple(pool_evidence),
                diagnostics=expanded.diagnostics,
            )
        )
    return facts, evidence, diagnostics


def _mission_items(
    index: DataForgeIndex, node: RecordNode, scope: _RecordScope
) -> tuple[list[str], list[Evidence], list[Diagnostic]]:
    items: list[str] = []
    evidence: list[Evidence] = []
    diagnostics: list[Diagnostic] = []
    candidates: list[FieldValue] = []
    reward_paths: list[str] = []
    for type_name in ("ContractResult_Item", "ItemAwardEntityClass"):
        for reward_object in _iter_named_objects(index, node, type_name, scope):
            reward_paths.append(reward_object.path)
    for found in _scope_fields(index, node, scope, "entityClass"):
        lowered = found.path.casefold()
        if "itemrewards" in lowered or any(
            found.path.startswith(f"{path}.") or found.path.startswith(f"{path}->")
            for path in reward_paths
        ):
            candidates.append(found)
    for found in {candidate.path: candidate for candidate in candidates}.values():
        resolved = index.resolve(
            found.value,
            source=node,
            field_path=found.path,
            breadcrumbs=(node.normalized_path,),
        )
        diagnostics.extend(resolved.diagnostics)
        if not resolved.target:
            continue
        name, name_evidence, used_fallback = _display_name(index, resolved.target)
        if name and name not in items:
            items.append(name)
            evidence.append(_evidence(node, found, resolved.reference))
            evidence.append(name_evidence)
            if used_fallback:
                diagnostics.append(
                    Diagnostic(
                        "item-name-fallback",
                        f"Used filename-derived item name {name!r}",
                        Severity.WARNING,
                        resolved.target.id,
                        resolved.target.normalized_path,
                    )
                )
    return items, evidence, diagnostics


def extract_mission_facts(source: RecordSource) -> MissionExtractionResult:
    """Extract contract-generator rewards while isolating schema drift.

    A failed optional provider degrades this report only.  It never prevents
    callers from using the existing MissionBrokerEntry/string extraction path.
    """

    index = DataForgeIndex(source)
    diagnostics = list(index.diagnostics)
    contract_nodes = index.records_under("records/contracts/contractgenerator")
    if not contract_nodes:
        diagnostics.append(
            Diagnostic(
                "contract-generator-provider-missing",
                "No records/contracts/contractgenerator records were found",
                Severity.ERROR,
            )
        )
        report = CapabilityReport(
            "dataforge-mission-facts",
            CapabilityStatus.UNAVAILABLE,
            source.version,
            0,
            0,
            tuple(diagnostics),
        )
        return MissionExtractionResult((), report)

    reputation, rep_diagnostics = _build_reputation_lookup(index)
    broker_reputation, broker_diagnostics = _build_broker_reputation(index, reputation)
    pools, pool_diagnostics = _build_blueprint_pools(index)
    diagnostics.extend(rep_diagnostics)
    diagnostics.extend(broker_diagnostics)
    diagnostics.extend(pool_diagnostics)
    if not reputation:
        diagnostics.append(
            Diagnostic(
                "reputation-provider-missing",
                "No usable mission reputation reward definitions were found",
                Severity.WARNING,
            )
        )
    if not pools:
        diagnostics.append(
            Diagnostic(
                "blueprint-provider-missing",
                "No usable crafting blueprint pools were found",
                Severity.WARNING,
            )
        )

    facts: list[MissionFacts] = []
    structurally_recognized = 0
    for node in contract_nodes:
        for scope in _contract_scopes(index, node):
            titles, descriptions, loc_evidence = _contract_localization(index, node, scope)
            rep, rep_evidence, rep_diags = _mission_reputation(index, node, scope, reputation)
            if not rep:
                broker_matches: dict[str, _BrokerReputation] = {}
                for key in (*titles, *descriptions):
                    for linked in broker_reputation.get(key, ()):
                        broker_matches.setdefault(linked.broker_id, linked)
                for linked in broker_matches.values():
                    for value in linked.values:
                        if value not in rep:
                            rep.append(value)
                    rep_evidence.extend(linked.evidence)
                    rep_diags.extend(linked.diagnostics)
            blueprints, bp_evidence, bp_diags = _mission_blueprints(index, node, scope, pools)
            items, item_evidence, item_diags = _mission_items(index, node, scope)
            fact_diags = [*rep_diags, *bp_diags, *item_diags]
            evidence = [*loc_evidence, *rep_evidence, *bp_evidence, *item_evidence]
            if titles or descriptions or rep or blueprints or items:
                structurally_recognized += 1
            if not titles:
                fact_diags.append(
                    Diagnostic(
                        "mission-title-missing",
                        "Contract generator variant has no recognized title localization key",
                        Severity.WARNING,
                        node.id,
                        node.normalized_path,
                        scope.path,
                    )
                )
            confidence = Confidence.HIGH
            if not titles or any(d.severity == Severity.ERROR for d in fact_diags):
                confidence = Confidence.LOW
            elif fact_diags or not evidence:
                confidence = Confidence.MEDIUM
            source_ids = [node.id]
            source_ids.extend(ev.record_id for ev in evidence if ev.record_id not in source_ids)
            mission_id = node.id if scope.path == "$" else f"{node.id}:{scope.path}"
            facts.append(
                MissionFacts(
                    mission_id,
                    tuple(titles),
                    tuple(descriptions),
                    tuple(rep),
                    tuple(blueprints),
                    tuple(items),
                    tuple(source_ids),
                    tuple(evidence),
                    confidence,
                    tuple(fact_diags),
                )
            )
            diagnostics.extend(fact_diags)

    if structurally_recognized == 0:
        diagnostics.append(
            Diagnostic(
                "contract-generator-schema-drift",
                "Contract records exist but none match the supported mission structure",
                Severity.ERROR,
            )
        )
        status = CapabilityStatus.UNAVAILABLE
    elif any(d.degrades_capability for d in diagnostics):
        status = CapabilityStatus.DEGRADED
    else:
        status = CapabilityStatus.AVAILABLE

    report = CapabilityReport(
        "dataforge-mission-facts",
        status,
        source.version,
        len(contract_nodes),
        len(facts),
        tuple(dict.fromkeys(diagnostics)),
    )
    return MissionExtractionResult(tuple(facts), report)
