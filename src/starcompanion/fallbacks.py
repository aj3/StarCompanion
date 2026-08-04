"""Explicit user-authored localization for CIG keys missing from a build.

This source never guesses text or aliases.  A document is generated from
structured provider gaps, and import accepts only complete, reward-bearing
DataForge key groups that are still absent from the selected stock table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .extract.dataforge import MissionExtractionResult
from .ini import LocalizationFile
from .model import (
    ContractSet,
    Evidence,
    ProviderCapability,
    ProviderStatus,
    UnresolvedLocalization,
)
from .validate import Severity, validate_value

SCHEMA_VERSION = 1
PROVIDER = "user-localization-fallbacks"
PROVIDER_VERSION = "1"
PLACEHOLDER_KEYS = frozenset({"loc_uninitialized", "loc_placeholder"})


class FallbackError(ValueError):
    pass


@dataclass(frozen=True)
class FallbackDocument:
    game_version: str | None
    language: str
    unresolved: tuple[UnresolvedLocalization, ...]
    values: dict[str, str]
    source_path: Path | None = None

    @property
    def authored_values(self) -> dict[str, str]:
        return {key: value for key, value in self.values.items() if value.strip()}

    def validate_context(self, *, game_version: str | None, language: str) -> None:
        if self.language.casefold() != language.casefold():
            raise FallbackError(
                f"fallback language {self.language!r} does not match {language!r}"
            )
        if (
            self.game_version
            and game_version
            and self.game_version != game_version
        ):
            raise FallbackError(
                f"fallback build {self.game_version!r} does not match {game_version!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "game_version": self.game_version,
            "language": self.language,
            "instructions": (
                "Fill only the values you personally author. Empty values are ignored. "
                "Do not rename keys or unresolved entries."
            ),
            "unresolved": [
                {
                    "source_id": item.source_id,
                    "reason": item.reason,
                    "keys": list(item.keys),
                }
                for item in self.unresolved
            ],
            "values": self.values,
        }

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> FallbackDocument:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FallbackError(f"invalid fallback JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise FallbackError("fallback document must be a JSON object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise FallbackError(
                f"unsupported fallback schema {raw.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        language = raw.get("language")
        if not isinstance(language, str) or not language.strip():
            raise FallbackError("fallback language must be a non-empty string")
        game_version = raw.get("game_version")
        if game_version is not None and not isinstance(game_version, str):
            raise FallbackError("fallback game_version must be a string or null")

        unresolved_raw = raw.get("unresolved")
        if not isinstance(unresolved_raw, list):
            raise FallbackError("fallback unresolved must be an array")
        unresolved: list[UnresolvedLocalization] = []
        allowed_keys: set[str] = set()
        for number, item in enumerate(unresolved_raw, 1):
            if not isinstance(item, dict):
                raise FallbackError(f"unresolved entry {number} must be an object")
            source_id = item.get("source_id")
            reason = item.get("reason")
            keys = item.get("keys")
            if not isinstance(source_id, str) or not source_id:
                raise FallbackError(f"unresolved entry {number} has no source_id")
            if reason != "localization-missing":
                raise FallbackError(
                    f"unresolved entry {number} is not a localization-missing fact"
                )
            if not isinstance(keys, list) or not keys or not all(
                isinstance(key, str) and key for key in keys
            ):
                raise FallbackError(f"unresolved entry {number} has invalid keys")
            typed_keys = tuple(keys)
            if any(key.casefold() in PLACEHOLDER_KEYS for key in typed_keys):
                raise FallbackError("shared placeholder keys cannot receive fallbacks")
            unresolved.append(UnresolvedLocalization(source_id, reason, typed_keys))
            allowed_keys.update(typed_keys)

        values = raw.get("values")
        if not isinstance(values, dict):
            raise FallbackError("fallback values must be an object of key to text")
        clean_values: dict[str, str] = {}
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\n" in key
                or "\r" in key
            ):
                raise FallbackError(f"invalid fallback key {key!r}")
            if key not in allowed_keys:
                raise FallbackError(f"fallback value {key!r} is not in unresolved metadata")
            if key.casefold() in PLACEHOLDER_KEYS:
                raise FallbackError(f"shared placeholder key {key!r} cannot be overridden")
            if not isinstance(value, str):
                raise FallbackError(f"fallback value for {key!r} must be text")
            errors = [
                issue
                for issue in validate_value(value)
                if issue.severity is Severity.ERROR
            ]
            if errors:
                raise FallbackError(f"invalid fallback value for {key!r}: {errors[0]}")
            clean_values[key] = value
        return cls(
            game_version=game_version,
            language=language,
            unresolved=tuple(unresolved),
            values=clean_values,
            source_path=path,
        )


@dataclass(frozen=True)
class AppliedFallbacks:
    keys: tuple[str, ...]
    source_ids: tuple[str, ...]
    source: str


def template_from_contracts(
    contracts: ContractSet,
    *,
    game_version: str | None,
    language: str,
) -> FallbackDocument:
    unresolved = tuple(
        item
        for capability in contracts.capabilities
        for item in capability.unresolved_localizations
        if item.reason == "localization-missing"
    )
    unique = {
        (item.source_id, item.keys): item
        for item in unresolved
    }
    ordered = tuple(
        unique[key]
        for key in sorted(unique, key=lambda value: (value[0], value[1]))
    )
    values = {
        key: ""
        for item in ordered
        for key in item.keys
        if key.casefold() not in PLACEHOLDER_KEYS
    }
    return FallbackDocument(game_version, language, ordered, values)


def apply_to_localization(
    strings: LocalizationFile,
    source: MissionExtractionResult,
    document: FallbackDocument,
) -> AppliedFallbacks:
    """Validate authored values against this exact DataForge/localization pair."""

    authored = document.authored_values
    source_label = (
        document.source_path.name
        if document.source_path is not None
        else "user-authored-fallbacks.json"
    )
    if not authored:
        return AppliedFallbacks((), (), source_label)

    missing_groups: dict[str, tuple[str, ...]] = {}
    eligible: set[str] = set()
    for fact in source.facts:
        if not (fact.reputation or fact.blueprint_pools or fact.item_rewards):
            continue
        keys = tuple(dict.fromkeys((*fact.title_keys, *fact.description_keys)))
        missing = tuple(
            key
            for key in keys
            if key.casefold() not in PLACEHOLDER_KEYS and strings.get(key) is None
        )
        if missing:
            missing_groups[fact.mission_id] = missing
            eligible.update(missing)

    for key in authored:
        if strings.get(key) is not None:
            raise FallbackError(
                f"fallback key {key!r} already exists in stock localization"
            )
        if key not in eligible:
            raise FallbackError(
                f"fallback key {key!r} is not an unresolved reward fact in this build"
            )

    selected_sources: list[str] = []
    authored_keys = set(authored)
    for source_id, missing in missing_groups.items():
        selected = authored_keys.intersection(missing)
        if selected and not set(missing) <= authored_keys:
            absent = ", ".join(key for key in missing if key not in authored_keys)
            raise FallbackError(
                f"fallback mission {source_id!r} is incomplete; also author: {absent}"
            )
        if selected:
            selected_sources.append(source_id)

    for key, value in authored.items():
        if not strings.add(key, value):  # defensive; existence was checked above
            raise FallbackError(f"fallback key {key!r} could not be added")
    return AppliedFallbacks(
        keys=tuple(authored),
        source_ids=tuple(selected_sources),
        source=source_label,
    )


def record_usage(
    contracts: ContractSet,
    applied: AppliedFallbacks,
    *,
    game_version: str,
) -> None:
    if not applied.keys:
        return
    key_set = set(applied.keys)
    touched: set[int] = set()
    evidence_links = 0
    for contract in contracts.contracts:
        for key in contract.all_keys():
            if key not in key_set:
                continue
            evidence = Evidence(
                provider=PROVIDER,
                record_id=key,
                record_path=applied.source,
                field_path=f"$.values[{json.dumps(key)}]",
                value=None,
            )
            if evidence not in contract.evidence:
                contract.evidence.append(evidence)
                evidence_links += 1
            touched.add(id(contract))
    contracts.capabilities.append(
        ProviderCapability(
            provider=PROVIDER,
            version=PROVIDER_VERSION,
            status=ProviderStatus.AVAILABLE,
            build_version=game_version,
            facts_seen=len(applied.keys),
            contracts_enhanced=len(touched),
            evidence_links=evidence_links,
            reward_facts=len(applied.source_ids),
            matched_facts=len(applied.source_ids),
        )
    )


__all__ = [
    "AppliedFallbacks",
    "FallbackDocument",
    "FallbackError",
    "apply_to_localization",
    "record_usage",
    "template_from_contracts",
]
