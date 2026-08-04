"""Deterministic localization source precedence and conflict provenance.

The graph is deliberately a value model, not a file writer.  It can therefore
drive the CLI and the future Qt item model without either surface inventing its
own merge rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class SourceKind(Enum):
    STOCK = "stock"
    LANGUAGE_OVERLAY = "language-overlay"
    IMPORT = "import"
    GENERATED = "generated"
    USER = "user"


PRECEDENCE: tuple[SourceKind, ...] = (
    SourceKind.STOCK,
    SourceKind.LANGUAGE_OVERLAY,
    SourceKind.IMPORT,
    SourceKind.GENERATED,
    SourceKind.USER,
)
_PRIORITY = {kind: number for number, kind in enumerate(PRECEDENCE)}


@dataclass(frozen=True)
class SourceLayer:
    """One named contribution at a fixed place in the precedence graph.

    ``order`` is meaningful within a kind.  In particular, configured imports
    are applied in command/configuration order and a later import wins.
    """

    source_id: str
    kind: SourceKind
    values: Mapping[str, str]
    order: int = 0
    provenance: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id cannot be empty")
        values: dict[str, str] = {}
        for key, value in self.values.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{self.source_id!r} has an invalid key")
            if not isinstance(value, str):
                raise ValueError(f"{self.source_id!r} value for {key!r} is not text")
            values[key] = value
        provenance = {key: tuple(items) for key, items in self.provenance.items()}
        if set(provenance) - set(values):
            raise ValueError(f"{self.source_id!r} has provenance for an absent key")
        if any(
            not isinstance(item, str)
            for items in provenance.values()
            for item in items
        ):
            raise ValueError(f"{self.source_id!r} provenance must be text")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "provenance", MappingProxyType(provenance))


@dataclass(frozen=True)
class Contribution:
    source_id: str
    kind: SourceKind
    value: str
    order: int
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedEntry:
    key: str
    value: str
    winner: Contribution
    contributions: tuple[Contribution, ...]

    @property
    def conflicted(self) -> bool:
        return len({item.value for item in self.contributions}) > 1

    @property
    def shadowed(self) -> tuple[Contribution, ...]:
        return tuple(
            item for item in self.contributions[:-1] if item.value != self.value
        )


@dataclass(frozen=True)
class MergeResult:
    entries: Mapping[str, ResolvedEntry]
    layers: tuple[SourceLayer, ...]

    @property
    def values(self) -> dict[str, str]:
        return {key: entry.value for key, entry in self.entries.items()}

    @property
    def conflicts(self) -> dict[str, ResolvedEntry]:
        return {key: entry for key, entry in self.entries.items() if entry.conflicted}

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries.values():
            counts[entry.winner.source_id] = counts.get(entry.winner.source_id, 0) + 1
        return counts


class SourceGraph:
    """Resolve values using the one public, stable precedence definition."""

    def __init__(self, layers: Iterable[SourceLayer] = ()):
        self._layers: list[SourceLayer] = []
        for layer in layers:
            self.add(layer)

    @property
    def layers(self) -> tuple[SourceLayer, ...]:
        return tuple(self._layers)

    def add(self, layer: SourceLayer) -> None:
        if any(existing.source_id == layer.source_id for existing in self._layers):
            raise ValueError(f"duplicate source_id {layer.source_id!r}")
        self._layers.append(layer)

    def resolve(self) -> MergeResult:
        ordered = tuple(
            sorted(
                self._layers,
                key=lambda layer: (
                    _PRIORITY[layer.kind],
                    layer.order,
                    layer.source_id.casefold(),
                ),
            )
        )
        gathered: dict[str, list[Contribution]] = {}
        for layer in ordered:
            for key in sorted(layer.values):
                gathered.setdefault(key, []).append(
                    Contribution(
                        source_id=layer.source_id,
                        kind=layer.kind,
                        value=layer.values[key],
                        order=layer.order,
                        provenance=tuple(layer.provenance.get(key, ())),
                    )
                )

        entries = {
            key: ResolvedEntry(
                key=key,
                value=contributions[-1].value,
                winner=contributions[-1],
                contributions=tuple(contributions),
            )
            for key, contributions in sorted(gathered.items())
        }
        return MergeResult(entries=MappingProxyType(entries), layers=ordered)


def report(result: MergeResult) -> dict[str, object]:
    """JSON-ready per-key precedence, provenance, and conflict report."""

    return {
        "precedence": [kind.value for kind in PRECEDENCE],
        "source_counts": result.source_counts(),
        "conflict_count": len(result.conflicts),
        "entries": {
            key: {
                "winner": entry.winner.source_id,
                "winner_kind": entry.winner.kind.value,
                "conflicted": entry.conflicted,
                "contributions": [
                    {
                        "source": item.source_id,
                        "kind": item.kind.value,
                        "value": item.value,
                        "provenance": list(item.provenance),
                    }
                    for item in entry.contributions
                ],
            }
            for key, entry in result.entries.items()
        },
    }


__all__ = [
    "PRECEDENCE",
    "Contribution",
    "MergeResult",
    "ResolvedEntry",
    "SourceGraph",
    "SourceKind",
    "SourceLayer",
    "report",
]
