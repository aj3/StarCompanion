"""Bounded, offline sharing of explicitly authored localization deltas."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Profile
from .ini import LocalizationFile
from .install import normalize_channel, normalize_language
from .user_edits import (
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    UserEditError,
    UserEditStore,
    _digest,
    _serialize,
    _validate_values,
)

DELTA_PACK_SCHEMA = 1
MAX_PACK_BYTES = 24 * 1024 * 1024
MAX_PACK_ENTRIES = 8
MAX_COMPRESSION_RATIO = 200
_MEMBERS = frozenset({"manifest.json", "user-deltas.ini", "profile.json", "README.txt"})


class DeltaPackError(ValueError):
    pass


@dataclass(frozen=True)
class DeltaPackExportPlan:
    store: UserEditStore
    user_digest: str
    origins_digest: str
    authored_values: dict[str, str]
    excluded_values: int
    profile_payload: bytes
    channel: str
    language: str


@dataclass(frozen=True)
class DeltaPackDocument:
    archive: Path
    archive_sha256: str
    source_channel: str
    language: str
    values: dict[str, str]
    profile: Profile


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def plan_delta_pack(store: UserEditStore, profile: Profile) -> DeltaPackExportPlan:
    """Select only values whose digest-bound origin is explicitly authored."""

    values = store.load()
    origins = store.load_origins(values)
    authored = {
        key: value for key, value in values.items() if origins.get(key) == "authored"
    }
    if not authored:
        raise DeltaPackError(
            "no values have verified user-authored provenance; imported, derived, and "
            "legacy values are excluded"
        )
    _validate_values(authored)
    if len(authored) > MAX_ENTRIES:
        raise DeltaPackError("authored delta count exceeds the safety limit")
    origins_digest = _sha256(
        _canonical_json({key: origins.get(key, "unknown") for key in sorted(values)})
    )
    return DeltaPackExportPlan(
        store,
        _digest(values),
        origins_digest,
        dict(authored),
        len(values) - len(authored),
        profile.dumps().encode("utf-8"),
        normalize_channel(store.channel),
        normalize_language(store.language),
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _readme(language: str) -> bytes:
    return (
        "StarCompanion user-authored delta pack\n\n"
        "This archive contains only explicitly authored user.ini deltas and a profile.\n"
        "It does not contain CIG stock localization or game data. Import it in "
        f"StarCompanion, review every conflict for {language}, then rebuild from your own "
        "installed Data.p4k. Applying still requires the normal preview and confirmation.\n"
    ).encode("utf-8")


def write_delta_pack(
    plan: DeltaPackExportPlan,
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    destination = Path(destination)
    if destination.suffix.casefold() != ".zip":
        raise DeltaPackError("delta-pack destination must be a .zip file")
    if destination.exists() and not overwrite:
        raise DeltaPackError(f"refusing to overwrite {destination}")
    current = plan.store.load()
    current_origins = plan.store.load_origins(current)
    current_origins_digest = _sha256(
        _canonical_json(
            {key: current_origins.get(key, "unknown") for key in sorted(current)}
        )
    )
    if _digest(current) != plan.user_digest or current_origins_digest != plan.origins_digest:
        raise DeltaPackError("user edits or authorship metadata changed after preview")
    current_authored = {
        key: value
        for key, value in current.items()
        if current_origins.get(key) == "authored"
    }
    if current_authored != plan.authored_values:
        raise DeltaPackError("delta-pack selection changed after preview")

    members = {
        "user-deltas.ini": _serialize(plan.authored_values).encode("utf-8"),
        "profile.json": plan.profile_payload,
        "README.txt": _readme(plan.language),
    }
    files = [
        {"path": name, "size": len(payload), "sha256": _sha256(payload)}
        for name, payload in sorted(members.items())
    ]
    manifest = _canonical_json(
        {
            "schema": DELTA_PACK_SCHEMA,
            "application": "StarCompanion",
            "kind": "user-authored-deltas",
            "source_channel": plan.channel,
            "language": plan.language,
            "files": files,
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temp = Path(temporary)
    try:
        with zipfile.ZipFile(temp, "w", allowZip64=False) as archive:
            archive.writestr(_zip_info("manifest.json"), manifest)
            for name, payload in members.items():
                archive.writestr(_zip_info(name), payload)
        if temp.stat().st_size > MAX_PACK_BYTES:
            raise DeltaPackError("delta pack exceeds the size limit")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or len(path.parts) != 1
        or any(part in {"", ".", ".."} for part in path.parts)
        or name not in _MEMBERS
    ):
        raise DeltaPackError(f"unsafe or undeclared delta-pack path {name!r}")
    if info.flag_bits & 0x1:
        raise DeltaPackError("encrypted delta packs are not supported")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise DeltaPackError(f"unsupported compression for {name!r}")
    unix_mode = info.external_attr >> 16
    if unix_mode and (unix_mode & 0o170000) not in {0, 0o100000}:
        raise DeltaPackError(f"delta-pack member {name!r} is not a regular file")
    if info.file_size > MAX_FILE_BYTES:
        raise DeltaPackError(f"delta-pack member {name!r} exceeds its size limit")
    if info.file_size and not info.compress_size:
        raise DeltaPackError(f"delta-pack member {name!r} has invalid compressed size")
    if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
        raise DeltaPackError(f"delta-pack member {name!r} exceeds compression-ratio limit")


def load_delta_pack(path: Path) -> DeltaPackDocument:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_PACK_BYTES:
        raise DeltaPackError("delta pack is missing or exceeds the size limit")
    raw = path.read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PACK_ENTRIES:
                raise DeltaPackError("delta pack contains too many entries")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != _MEMBERS:
                raise DeltaPackError("delta pack must contain exactly the declared files")
            for info in infos:
                _safe_member(info)
            if sum(info.file_size for info in infos) > MAX_PACK_BYTES:
                raise DeltaPackError("delta pack exceeds the uncompressed size limit")
            manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_strict_object)
            if not isinstance(manifest, dict) or set(manifest) != {
                "schema",
                "application",
                "kind",
                "source_channel",
                "language",
                "files",
            }:
                raise DeltaPackError("delta-pack manifest has an invalid shape")
            if (
                type(manifest["schema"]) is not int
                or manifest["schema"] != DELTA_PACK_SCHEMA
                or manifest["application"] != "StarCompanion"
                or manifest["kind"] != "user-authored-deltas"
                or not isinstance(manifest["source_channel"], str)
                or not isinstance(manifest["language"], str)
                or not isinstance(manifest["files"], list)
            ):
                raise DeltaPackError("unsupported delta-pack manifest")
            try:
                source_channel = normalize_channel(manifest["source_channel"])
                language = normalize_language(manifest["language"])
            except ValueError as exc:
                raise DeltaPackError(str(exc)) from exc
            if (
                source_channel != manifest["source_channel"]
                or language != manifest["language"]
            ):
                raise DeltaPackError("delta-pack scope is not canonical")
            declared = {}
            for item in manifest["files"]:
                if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                    raise DeltaPackError("invalid delta-pack file record")
                name = item["path"]
                if (
                    not isinstance(name, str)
                    or name in declared
                    or name not in _MEMBERS - {"manifest.json"}
                ):
                    raise DeltaPackError("invalid or duplicate delta-pack file record")
                if (
                    not isinstance(item["size"], int)
                    or isinstance(item["size"], bool)
                    or item["size"] < 0
                    or not isinstance(item["sha256"], str)
                    or len(item["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in item["sha256"])
                ):
                    raise DeltaPackError("invalid delta-pack file metadata")
                payload = archive.read(name)
                if item["size"] != len(payload) or item["sha256"] != _sha256(payload):
                    raise DeltaPackError(f"delta-pack member {name!r} failed verification")
                declared[name] = payload
            if set(declared) != _MEMBERS - {"manifest.json"}:
                raise DeltaPackError("delta-pack manifest is incomplete")
            values = _parse_values(declared["user-deltas.ini"])
            profile = Profile.loads(declared["profile.json"].decode("utf-8"))
    except DeltaPackError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        KeyError,
        RuntimeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise DeltaPackError(f"invalid delta pack: {exc}") from exc
    return DeltaPackDocument(
        path,
        _sha256(raw),
        source_channel,
        language,
        values,
        profile,
    )


def _parse_values(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_FILE_BYTES:
        raise DeltaPackError("user deltas exceed the size limit")
    text = payload.decode("utf-8")
    if not text.startswith("\ufeff") or "\r" in text or not text.endswith("\n"):
        raise DeltaPackError("user deltas are not canonical UTF-8 BOM/LF INI")
    body = text[1:-1]
    if not body or any(not line or "=" not in line for line in body.split("\n")):
        raise DeltaPackError("user deltas contain a malformed line")
    parsed = LocalizationFile.loads(text)
    values: dict[str, str] = {}
    for entry in parsed.entries():
        if entry.key in values:
            raise DeltaPackError(f"duplicate delta key {entry.key!r}")
        values[entry.key] = entry.value
    try:
        _validate_values(values)
    except UserEditError as exc:
        raise DeltaPackError(str(exc)) from exc
    if _serialize(values).encode("utf-8") != payload:
        raise DeltaPackError("user deltas are not in canonical key order")
    return values


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeltaPackError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


__all__ = [
    "DeltaPackDocument",
    "DeltaPackError",
    "DeltaPackExportPlan",
    "load_delta_pack",
    "plan_delta_pack",
    "write_delta_pack",
]
