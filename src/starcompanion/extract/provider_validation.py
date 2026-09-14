"""Read-only, redacted aggregate validation for local game builds."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..install import GameInstall
from . import datacore
from .dataforge import DataForgeIndex, RecordSource
from .entities import LocalEntityProvider, entity_providers
from .mission_tactical import MissionTacticalProvider, mission_tactical_providers
from .p4k import P4KArchive

SNAPSHOT_VERSION = 1
VALIDATION_CHANNELS = frozenset({"LIVE", "HOTFIX"})
_DCB_ENTRY = "Data/Game2.dcb"
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_LOCAL_PATH = re.compile(r"(?:[a-z]:\\|/libs/foundry/|/users/)", re.IGNORECASE)


def _counts(values) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _schema_fingerprint(source: RecordSource) -> str:
    structs = Counter()
    properties = Counter()
    for record in source.records:
        if 0 <= record.struct_index < len(source.structs):
            structs[source.structs[record.struct_index].name] += 1
    for prop in getattr(source, "properties", ()):
        properties[(prop.name, int(prop.data_type), int(prop.conversion_type))] += 1
    shape = {
        "version": source.version,
        "records": len(source.records),
        "structs": sorted(structs.items()),
        "properties": sorted(((*key, count) for key, count in properties.items())),
    }
    payload = json.dumps(shape, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _entity_snapshot(provider: LocalEntityProvider, index: DataForgeIndex, build: str) -> dict[str, Any]:
    try:
        result = provider.extract(index, build_version=build)
    except Exception:
        return {
            "version": provider.spec.version,
            "status": "error",
            "records_examined": 0,
            "facts_emitted": 0,
            "value_counts": {},
            "relationship_counts": {},
            "evidence_links": 0,
            "diagnostic_counts": {"provider-exception": 1},
            "corrections_applied": 0,
        }
    return {
        "version": provider.spec.version,
        "status": result.capability.status.value,
        "records_examined": result.capability.records_examined,
        "facts_emitted": result.capability.facts_emitted,
        "value_counts": _counts(value.name for fact in result.facts for value in fact.values),
        "relationship_counts": _counts(
            link.name for fact in result.facts for link in fact.relationships
        ),
        "evidence_links": sum(
            len(fact.values) + sum(len(link.evidence) for link in fact.relationships)
            for fact in result.facts
        ),
        "diagnostic_counts": _counts(item.code for item in result.capability.diagnostics),
        "corrections_applied": len(result.corrections_applied),
    }


def _tactical_snapshot(
    provider: MissionTacticalProvider,
    index: DataForgeIndex,
    build: str,
) -> dict[str, Any]:
    try:
        result = provider.extract(index, build_version=build)
    except Exception:
        return {
            "version": provider.spec.version,
            "status": "error",
            "records_examined": 0,
            "facts_emitted": 0,
            "value_counts": {},
            "evidence_links": 0,
            "diagnostic_counts": {"provider-exception": 1},
        }
    return {
        "version": provider.spec.version,
        "status": result.capability.status.value,
        "records_examined": result.capability.records_examined,
        "facts_emitted": result.capability.facts_emitted,
        "value_counts": _counts(value.name for fact in result.facts for value in fact.values),
        "evidence_links": sum(
            len(value.evidence) for fact in result.facts for value in fact.values
        ),
        "diagnostic_counts": _counts(item.code for item in result.capability.diagnostics),
    }


def aggregate_provider_validation(
    source: RecordSource,
    *,
    channel: str,
    build_version: str,
) -> dict[str, Any]:
    """Run every provider and return counts/hashes without record content."""

    normalized_channel = channel.strip().upper()
    if normalized_channel not in VALIDATION_CHANNELS:
        raise ValueError("provider archive validation supports LIVE and HOTFIX only")
    index = DataForgeIndex(source)
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "redaction": "aggregate-only",
        "channel": normalized_channel,
        "game_version": str(build_version),
        "datacore_version": source.version,
        "record_count": len(source.records),
        "schema_sha256": _schema_fingerprint(source),
        "entities": {
            provider.spec.provider: _entity_snapshot(provider, index, str(build_version))
            for provider in entity_providers()
        },
        "mission_tactical": {
            provider.spec.provider: _tactical_snapshot(provider, index, str(build_version))
            for provider in mission_tactical_providers()
        },
        "index_diagnostic_counts": _counts(item.code for item in index.diagnostics),
    }
    assert_redacted_snapshot(snapshot)
    return snapshot


def validate_install(install: GameInstall) -> dict[str, Any]:
    """Validate one local archive while owning and deleting its temporary DCB."""

    channel = install.channel.strip().upper()
    if channel not in VALIDATION_CHANNELS:
        raise ValueError("provider archive validation supports LIVE and HOTFIX only")
    if not install.archive.is_file() or install.archive.is_symlink():
        raise ValueError("install archive must be a regular local file")
    with tempfile.TemporaryDirectory(prefix="starcompanion-provider-validation-") as directory:
        extracted = Path(directory) / "Game2.dcb"
        with P4KArchive(
            install.archive,
            exact_entries=(_DCB_ENTRY,),
        ) as archive:
            if _DCB_ENTRY not in archive:
                raise ValueError("Data/Game2.dcb is not present in the selected archive")
            archive.extract(_DCB_ENTRY, extracted)
        source = datacore.load(extracted)
        return aggregate_provider_validation(
            source,
            channel=channel,
            build_version=install.version or f"datacore-{source.version}",
        )


def assert_redacted_snapshot(snapshot: dict[str, Any]) -> None:
    """Reject accidental record identities, paths, or localization content."""

    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=True)
    if _UUID.search(encoded) or _LOCAL_PATH.search(encoded) or "@" in encoded:
        raise ValueError("provider validation snapshot contains non-aggregate data")


def snapshot_json(snapshot: dict[str, Any]) -> str:
    assert_redacted_snapshot(snapshot)
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
