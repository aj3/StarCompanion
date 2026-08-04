"""Channel-scoped blueprint ownership, incremental log scanning, and transfer.

Only authoritative ``SHUDEvent_OnNotification`` acquisition lines are
accepted.  The state retains a timestamp, byte offset, hashes, and a basename
for explanation; it never copies complete player log lines or absolute paths.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .blueprints import BlueprintCatalog, normalize_blueprint_name
from .install import normalize_channel
from .user_edits import data_dir

OWNERSHIP_SCHEMA = 1
MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_ENTRIES = 100_000
MAX_NAME_LENGTH = 512
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_ACQUISITIONS = 500_000
CHUNK_SIZE = 256 * 1024
MAX_LOG_LINE_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64
LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 30.0
BLUEPRINT_EPOCH = datetime(2026, 3, 1, tzinfo=timezone.utc)
_EVENT = re.compile(
    r'^<(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)>.*?'
    r'<SHUDEvent_OnNotification> Added notification '
    r'"Received Blueprint: (?P<name>.*?): " \['
)


class OwnershipError(ValueError):
    pass


class ScanCancelled(OwnershipError):
    pass


class OwnershipConflictError(OwnershipError):
    pass


class OwnershipRecoveryAvailable(OwnershipError):
    pass


def _check_json_depth(text: str, *, label: str) -> None:
    """Reject deeply nested JSON before the decoder allocates its object tree."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise OwnershipError(f"{label}: JSON nesting limit exceeded")
        elif char in "]}":
            depth = max(0, depth - 1)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_unknown_fields(item: dict, allowed: set[str], label: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise OwnershipError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def ownership_scope(channel: str, *, link_live_hotfix: bool = False) -> str:
    try:
        normalized = normalize_channel(channel)
    except ValueError as exc:
        raise OwnershipError(str(exc)) from exc
    if link_live_hotfix and normalized in {"LIVE", "HOTFIX"}:
        return "LIVE-HOTFIX"
    return normalized


def ownership_path(
    channel: str,
    *,
    root: Path | None = None,
    link_live_hotfix: bool = False,
) -> Path:
    base = (root or data_dir()).resolve()
    path = base / "channels" / ownership_scope(
        channel, link_live_hotfix=link_live_hotfix
    ) / "ownership.json"
    if base not in path.resolve().parents:
        raise OwnershipError("ownership path escapes the selected data root")
    return path


@dataclass(frozen=True)
class Acquisition:
    acquisition_id: str
    source: str
    acquired_at: str | None
    source_name: str
    source_fingerprint: str
    byte_offset: int | None = None
    line_sha256: str | None = None


@dataclass
class OwnershipRecord:
    blueprint_id: str
    name: str
    acquisitions: list[Acquisition] = field(default_factory=list)


@dataclass(frozen=True)
class UnresolvedAcquisition:
    name: str
    reason: str
    acquisition: Acquisition


@dataclass
class FileCursor:
    identity: str
    source_name: str
    offset: int = 0
    size: int = 0
    prefix_length: int = 0
    prefix_sha256: str = ""


@dataclass
class OwnershipState:
    scope: str
    records: dict[str, OwnershipRecord] = field(default_factory=dict)
    cursors: dict[str, FileCursor] = field(default_factory=dict)
    unresolved: list[UnresolvedAcquisition] = field(default_factory=list)
    revision: int = 0

    def add(self, blueprint_id: str, name: str, acquisition: Acquisition) -> bool:
        count = sum(len(record.acquisitions) for record in self.records.values()) + len(self.unresolved)
        if count >= MAX_ACQUISITIONS:
            raise OwnershipError("ownership acquisition limit exceeded")
        record = self.records.setdefault(blueprint_id, OwnershipRecord(blueprint_id, name))
        if any(item.acquisition_id == acquisition.acquisition_id for item in record.acquisitions):
            return False
        record.acquisitions.append(acquisition)
        record.acquisitions.sort(key=lambda item: (item.acquired_at or "", item.acquisition_id))
        return True

    def add_unresolved(self, item: UnresolvedAcquisition) -> bool:
        if any(
            current.acquisition.acquisition_id == item.acquisition.acquisition_id
            for current in self.unresolved
        ):
            return False
        if sum(len(record.acquisitions) for record in self.records.values()) + len(self.unresolved) >= MAX_ACQUISITIONS:
            raise OwnershipError("ownership acquisition limit exceeded")
        self.unresolved.append(item)
        self.unresolved.sort(
            key=lambda current: (
                current.acquisition.acquired_at or "",
                current.acquisition.acquisition_id,
            )
        )
        return True


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Unlike POSIX, ``os.kill(pid, 0)`` is not a harmless existence probe
        # on Windows: it can terminate the target process with exit code 0.
        # Querying a limited-information process handle is read-only. Access
        # denied still means the process exists (for example an elevated one).
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        open_process.restype = ctypes.c_void_p
        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            exit_code = ctypes.c_ulong()
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            get_exit_code.restype = ctypes.c_int
            queried = bool(get_exit_code(handle, ctypes.byref(exit_code)))
            kernel32.CloseHandle(handle)
            return queried and exit_code.value == 259  # STILL_ACTIVE
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def _store_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.time_ns()}"
    payload = json.dumps({"pid": os.getpid(), "token": token}).encode("ascii")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            stale = False
            try:
                lock_data = json.loads(path.read_text(encoding="ascii"))
                stale = not _pid_running(int(lock_data["pid"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                try:
                    stale = time.time() - path.stat().st_mtime > STALE_LOCK_SECONDS
                except OSError:
                    stale = False
            if stale:
                # Only one contender may reap a dead/malformed lock. Without
                # this guard, two reapers can observe the old lock and the
                # slower one can accidentally unlink a newly acquired lock.
                reap_path = path.with_suffix(path.suffix + ".reap")
                try:
                    reap_descriptor = os.open(
                        reap_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                except FileExistsError:
                    reap_descriptor = None
                if reap_descriptor is not None:
                    with os.fdopen(reap_descriptor, "wb") as reap_stream:
                        reap_stream.write(str(os.getpid()).encode("ascii"))
                        reap_stream.flush()
                        os.fsync(reap_stream.fileno())
                    try:
                        try:
                            current = json.loads(path.read_text(encoding="ascii"))
                            still_stale = not _pid_running(int(current["pid"]))
                        except (OSError, ValueError, KeyError, json.JSONDecodeError):
                            try:
                                still_stale = (
                                    time.time() - path.stat().st_mtime
                                    > STALE_LOCK_SECONDS
                                )
                            except OSError:
                                still_stale = False
                        if still_stale:
                            path.unlink(missing_ok=True)
                    finally:
                        reap_path.unlink(missing_ok=True)
                    continue
                try:
                    reap_pid = int(reap_path.read_text(encoding="ascii"))
                    abandoned_reap = not _pid_running(reap_pid)
                except (OSError, ValueError):
                    try:
                        abandoned_reap = (
                            time.time() - reap_path.stat().st_mtime
                            > STALE_LOCK_SECONDS
                        )
                    except OSError:
                        abandoned_reap = False
                if abandoned_reap:
                    reap_path.unlink(missing_ok=True)
                    continue
            if time.monotonic() >= deadline:
                raise OwnershipConflictError(
                    "ownership store is busy; another process may be updating it"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="ascii"))
            if current.get("token") == token:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass


def _to_dict(state: OwnershipState) -> dict[str, object]:
    return {
        "schema": OWNERSHIP_SCHEMA,
        "scope": state.scope,
        "revision": state.revision,
        "records": [
            {
                "blueprint_id": record.blueprint_id,
                "name": record.name,
                "acquisitions": [item.__dict__ for item in record.acquisitions],
            }
            for record in sorted(state.records.values(), key=lambda item: item.blueprint_id)
        ],
        "cursors": [cursor.__dict__ for cursor in sorted(state.cursors.values(), key=lambda item: item.identity)],
        "unresolved": [
            {
                "name": item.name,
                "reason": item.reason,
                "acquisition": item.acquisition.__dict__,
            }
            for item in state.unresolved
        ],
    }


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _valid_timestamp(value: str | None) -> bool:
    if value is None:
        return True
    if len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _parse_acquisition(item: object) -> Acquisition:
    if not isinstance(item, dict):
        raise OwnershipError("invalid acquisition evidence")
    _reject_unknown_fields(
        item,
        {
            "acquisition_id",
            "source",
            "acquired_at",
            "source_name",
            "source_fingerprint",
            "byte_offset",
            "line_sha256",
        },
        "acquisition evidence",
    )
    source_name = item.get("source_name")
    source = item.get("source")
    acquired_at = item.get("acquired_at")
    offset = item.get("byte_offset")
    line_hash = item.get("line_sha256")
    if (
        source not in {"log", "import"}
        or not isinstance(source_name, str)
        or not source_name
        or len(source_name) > 255
        or Path(source_name).name != source_name
        or any(char in source_name for char in "\r\n\0")
        or not _is_sha256(item.get("acquisition_id"))
        or not _is_sha256(item.get("source_fingerprint"))
        or not _valid_timestamp(acquired_at if isinstance(acquired_at, str) else None)
        or (acquired_at is not None and not isinstance(acquired_at, str))
        or (offset is not None and (type(offset) is not int or offset < 0))
        or (line_hash is not None and not _is_sha256(line_hash))
    ):
        raise OwnershipError("invalid acquisition evidence")
    if source == "log" and (offset is None or line_hash is None or acquired_at is None):
        raise OwnershipError("log acquisition evidence is incomplete")
    return Acquisition(
        acquisition_id=item["acquisition_id"],
        source=source,
        acquired_at=acquired_at,
        source_name=source_name,
        source_fingerprint=item["source_fingerprint"],
        byte_offset=offset,
        line_sha256=line_hash,
    )


def _from_dict(data: object, expected_scope: str) -> OwnershipState:
    if not isinstance(data, dict) or data.get("schema") != OWNERSHIP_SCHEMA:
        raise OwnershipError("unsupported ownership schema")
    _reject_unknown_fields(
        data,
        {"schema", "scope", "revision", "records", "cursors", "unresolved"},
        "ownership state",
    )
    if data.get("scope") != expected_scope:
        raise OwnershipError("ownership file belongs to a different channel scope")
    raw_records = data.get("records", [])
    raw_cursors = data.get("cursors", [])
    raw_unresolved = data.get("unresolved", [])
    if not all(isinstance(value, list) for value in (raw_records, raw_cursors, raw_unresolved)):
        raise OwnershipError("invalid ownership records or cursors")
    if len(raw_records) > MAX_IMPORT_ENTRIES:
        raise OwnershipError("ownership record limit exceeded")
    revision = data.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise OwnershipError("invalid ownership revision")
    state = OwnershipState(expected_scope, revision=revision)
    acquisition_count = 0
    acquisition_ids: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise OwnershipError("invalid ownership record")
        _reject_unknown_fields(
            raw, {"blueprint_id", "name", "acquisitions"}, "ownership record"
        )
        blueprint_id = raw.get("blueprint_id")
        name = raw.get("name")
        if (
            not isinstance(blueprint_id, str)
            or not blueprint_id
            or len(blueprint_id) > 256
            or any(char in blueprint_id for char in "\r\n\0")
            or not isinstance(name, str)
            or not name
            or len(name) > MAX_NAME_LENGTH
            or any(char in name for char in "\r\n\0")
        ):
            raise OwnershipError("invalid ownership identity")
        acquisitions = raw.get("acquisitions", [])
        if not isinstance(acquisitions, list):
            raise OwnershipError("invalid acquisition list")
        record = OwnershipRecord(blueprint_id, name)
        for item in acquisitions:
            acquisition = _parse_acquisition(item)
            if acquisition.acquisition_id in acquisition_ids:
                raise OwnershipError("duplicate acquisition identity")
            acquisition_ids.add(acquisition.acquisition_id)
            record.acquisitions.append(acquisition)
            acquisition_count += 1
        if blueprint_id in state.records:
            raise OwnershipError("duplicate ownership record identity")
        state.records[blueprint_id] = record
    if acquisition_count > MAX_ACQUISITIONS:
        raise OwnershipError("ownership acquisition limit exceeded")
    for raw in raw_cursors:
        if not isinstance(raw, dict):
            raise OwnershipError("invalid scan cursor")
        _reject_unknown_fields(
            raw,
            {
                "identity",
                "source_name",
                "offset",
                "size",
                "prefix_length",
                "prefix_sha256",
            },
            "scan cursor",
        )
        identity = raw.get("identity")
        source_name = raw.get("source_name")
        offset = raw.get("offset")
        size = raw.get("size")
        prefix_length = raw.get("prefix_length")
        prefix_hash = raw.get("prefix_sha256")
        if (
            not _is_sha256(identity)
            or not isinstance(source_name, str)
            or not source_name
            or len(source_name) > 255
            or Path(source_name).name != source_name
            or any(char in source_name for char in "\r\n\0")
            or any(type(value) is not int for value in (offset, size, prefix_length))
            or min(offset, size, prefix_length) < 0
            or offset > size
            or prefix_length > min(size, 4096)
            or not _is_sha256(prefix_hash)
        ):
            raise OwnershipError("invalid scan cursor values")
        cursor = FileCursor(identity, source_name, offset, size, prefix_length, prefix_hash)
        if cursor.identity in state.cursors:
            raise OwnershipError("duplicate scan cursor identity")
        state.cursors[cursor.identity] = cursor
    for raw in raw_unresolved:
        if not isinstance(raw, dict) or not isinstance(raw.get("acquisition"), dict):
            raise OwnershipError("invalid unresolved acquisition")
        _reject_unknown_fields(
            raw, {"name", "reason", "acquisition"}, "unresolved acquisition"
        )
        acquisition = _parse_acquisition(raw["acquisition"])
        name = raw.get("name")
        reason = raw.get("reason")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > MAX_NAME_LENGTH
            or any(char in name for char in "\r\n\0")
            or reason not in {"no-match", "ambiguous"}
        ):
            raise OwnershipError("invalid unresolved acquisition evidence")
        if acquisition.acquisition_id in acquisition_ids:
            raise OwnershipError("duplicate acquisition identity")
        acquisition_ids.add(acquisition.acquisition_id)
        state.unresolved.append(UnresolvedAcquisition(name, reason, acquisition))
        acquisition_count += 1
    if acquisition_count > MAX_ACQUISITIONS:
        raise OwnershipError("ownership acquisition limit exceeded")
    return state


@dataclass(frozen=True)
class OwnershipStore:
    channel: str
    root: Path | None = None
    link_live_hotfix: bool = False

    @property
    def scope(self) -> str:
        return ownership_scope(self.channel, link_live_hotfix=self.link_live_hotfix)

    @property
    def path(self) -> Path:
        return ownership_path(self.channel, root=self.root, link_live_hotfix=self.link_live_hotfix)

    @property
    def backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    def _load_path(self, path: Path) -> OwnershipState:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise OwnershipError("ownership state exceeds its size limit")
        try:
            text = path.read_text(encoding="utf-8")
            _check_json_depth(text, label="ownership state is invalid")
            data = json.loads(text, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OwnershipError(f"ownership state is invalid JSON: {exc}") from exc
        return _from_dict(data, self.scope)

    def load(self) -> OwnershipState:
        if not self.path.is_file():
            return OwnershipState(self.scope)
        try:
            return self._load_path(self.path)
        except OwnershipError as primary_error:
            if self.backup_path.is_file():
                try:
                    self._load_path(self.backup_path)
                except (OSError, OwnershipError):
                    pass
                else:
                    raise OwnershipRecoveryAvailable(
                        "ownership state is damaged; a validated last-known-good "
                        "backup is available through `blueprints recover`"
                    ) from primary_error
            raise

    def load_backup(self) -> OwnershipState:
        if not self.backup_path.is_file():
            raise OwnershipError("no ownership backup is available")
        return self._load_path(self.backup_path)

    def save(self, state: OwnershipState) -> None:
        if state.scope != self.scope:
            raise OwnershipError("refusing to save a different channel scope")
        with _store_lock(self.lock_path):
            current = self._load_path(self.path) if self.path.is_file() else OwnershipState(self.scope)
            if state.revision != current.revision:
                raise OwnershipConflictError(
                    f"ownership state changed concurrently (expected revision "
                    f"{state.revision}, found {current.revision}); reload and retry"
                )
            candidate = copy.deepcopy(state)
            candidate.revision += 1
            validated = _from_dict(_to_dict(candidate), self.scope)
            payload = (json.dumps(_to_dict(validated), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(payload) > MAX_STATE_BYTES:
                raise OwnershipError("ownership state exceeds its size limit")
            if self.path.is_file():
                _atomic_bytes(self.backup_path, self.path.read_bytes())
            _atomic_bytes(self.path, payload)
            state.revision = candidate.revision

    def recover(self) -> OwnershipState:
        with _store_lock(self.lock_path):
            candidate = self.load_backup()
            # The damaged primary was normally one revision newer than its
            # backup. Skip past that value so pre-corruption snapshots remain stale.
            candidate.revision += 2
            validated = _from_dict(_to_dict(candidate), self.scope)
            payload = (json.dumps(_to_dict(validated), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            _atomic_bytes(self.path, payload)
            return validated


def _file_identity(path: Path, stat: os.stat_result) -> str:
    if stat.st_ino:
        raw = f"inode:{stat.st_dev}:{stat.st_ino}"
    else:
        raw = "path:" + os.path.normcase(str(path.resolve()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stream_prefix(stream, length: int) -> str:
    position = stream.tell()
    try:
        stream.seek(0)
        return hashlib.sha256(stream.read(length)).hexdigest()
    finally:
        stream.seek(position)


def discover_log_files(channel_root: Path) -> tuple[Path, ...]:
    root = Path(channel_root)
    candidates = list((root / "logbackups").glob("*.log")) if (root / "logbackups").is_dir() else []
    if (root / "Game.log").is_file():
        candidates.append(root / "Game.log")
    readable: list[tuple[int, str, Path]] = []
    for path in candidates:
        try:
            readable.append((path.stat().st_mtime_ns, str(path), path))
        except OSError:
            continue
    return tuple(item[2] for item in sorted(readable))


@dataclass(frozen=True)
class ScanDiagnostic:
    source_name: str
    code: str
    message: str


@dataclass(frozen=True)
class ScanResult:
    state: OwnershipState
    files_seen: int
    files_read: int
    bytes_read: int
    events_seen: int
    acquisitions_added: int
    unresolved_added: int
    unresolved_reconciled: int
    unmatched_names: tuple[str, ...]
    diagnostics: tuple[ScanDiagnostic, ...]


def _parse_event(line: bytes) -> tuple[str, str] | None:
    text = line.decode("utf-8", errors="replace")
    if "Received Blueprint:" not in text:
        return None
    match = _EVENT.search(text)
    if not match:
        return None
    try:
        timestamp = datetime.fromisoformat(match.group("ts").replace("Z", "+00:00"))
    except ValueError:
        return None
    name = match.group("name").strip()
    if timestamp < BLUEPRINT_EPOCH or not name or len(name) > MAX_NAME_LENGTH:
        return None
    return timestamp.isoformat().replace("+00:00", "Z"), name


def _reconcile_unresolved(state: OwnershipState, catalog: BlueprintCatalog) -> int:
    remaining: list[UnresolvedAcquisition] = []
    reconciled = 0
    pending = state.unresolved
    state.unresolved = []
    for item in pending:
        candidates = catalog.resolve_name_candidates(item.name)
        if len(candidates) == 1:
            blueprint_id = candidates[0]
            if state.add(blueprint_id, catalog.by_id[blueprint_id].name, item.acquisition):
                reconciled += 1
        else:
            reason = "ambiguous" if candidates else "no-match"
            remaining.append(UnresolvedAcquisition(item.name, reason, item.acquisition))
    state.unresolved = remaining
    return reconciled


def scan_logs(
    paths: Iterable[Path],
    catalog: BlueprintCatalog,
    state: OwnershipState,
    *,
    full_rescan: bool = False,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ScanResult:
    """Incrementally scan explicit files, returning a preview state.

    The input state is never mutated, so cancellation or a caller declining a
    preview cannot advance watermarks.
    """

    result = copy.deepcopy(state)
    reconciled = _reconcile_unresolved(result, catalog)
    selected = tuple(dict.fromkeys(Path(path) for path in paths))
    diagnostics: list[ScanDiagnostic] = []
    unmatched: set[str] = set()
    files_read = bytes_read = events_seen = added = unresolved_added = 0
    for index, path in enumerate(selected):
        if cancel and cancel():
            raise ScanCancelled("blueprint log scan cancelled")
        if progress:
            progress(index, len(selected), path.name)
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = _file_identity(path, stat)
                previous = result.cursors.get(identity)
                start = 0 if full_rescan or previous is None else previous.offset
                code = None
                if previous is not None and not full_rescan:
                    compare_length = min(previous.prefix_length, stat.st_size)
                    changed = (
                        compare_length != previous.prefix_length
                        or _stream_prefix(stream, compare_length) != previous.prefix_sha256
                    )
                    if stat.st_size < previous.offset:
                        code = "truncated"
                    elif changed:
                        code = "replaced"
                    if code:
                        start = 0
                        diagnostics.append(
                            ScanDiagnostic(
                                path.name,
                                code,
                                "file identity content changed; restarted at byte zero",
                            )
                        )
                if start == stat.st_size:
                    continue
                files_read += 1
                stream.seek(start)
                remaining = stat.st_size - start
                buffer = b""
                buffer_offset = start
                committed = start
                discarding_oversized = False
                while remaining:
                    if cancel and cancel():
                        raise ScanCancelled("blueprint log scan cancelled")
                    chunk = stream.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    bytes_read += len(chunk)
                    if discarding_oversized:
                        newline = chunk.find(b"\n")
                        if newline < 0:
                            continue
                        committed = stream.tell() - len(chunk) + newline + 1
                        buffer_offset = committed
                        chunk = chunk[newline + 1 :]
                        discarding_oversized = False
                    buffer += chunk
                    while True:
                        newline = buffer.find(b"\n")
                        if newline < 0:
                            break
                        raw = buffer[:newline].rstrip(b"\r")
                        line_start = buffer_offset
                        consumed = newline + 1
                        buffer = buffer[consumed:]
                        buffer_offset += consumed
                        committed = buffer_offset
                        if len(raw) > MAX_LOG_LINE_BYTES:
                            diagnostics.append(
                                ScanDiagnostic(
                                    path.name,
                                    "oversized-line",
                                    f"discarded a log line exceeding {MAX_LOG_LINE_BYTES:,} bytes",
                                )
                            )
                            continue
                        event = _parse_event(raw)
                        if event is None:
                            continue
                        events_seen += 1
                        acquired_at, name = event
                        line_hash = hashlib.sha256(raw).hexdigest()
                        acquisition_id = hashlib.sha256(
                            f"log\0{acquired_at}\0{normalize_blueprint_name(name)}\0{line_hash}".encode("utf-8")
                        ).hexdigest()
                        acquisition = Acquisition(
                            acquisition_id=acquisition_id,
                            source="log",
                            acquired_at=acquired_at,
                            source_name=path.name,
                            source_fingerprint=identity,
                            byte_offset=line_start,
                            line_sha256=line_hash,
                        )
                        candidates = catalog.resolve_name_candidates(name)
                        if len(candidates) != 1:
                            unmatched.add(name)
                            if result.add_unresolved(
                                UnresolvedAcquisition(
                                    name,
                                    "ambiguous" if candidates else "no-match",
                                    acquisition,
                                )
                            ):
                                unresolved_added += 1
                            continue
                        blueprint_id = candidates[0]
                        if result.add(blueprint_id, catalog.by_id[blueprint_id].name, acquisition):
                            added += 1
                    if len(buffer) > MAX_LOG_LINE_BYTES:
                        diagnostics.append(
                            ScanDiagnostic(
                                path.name,
                                "oversized-line",
                                f"discarded a log line exceeding {MAX_LOG_LINE_BYTES:,} bytes",
                            )
                        )
                        buffer = b""
                        discarding_oversized = True
                # A partial final line is deliberately not committed. It will
                # be read again after the game completes it with a newline.
                prefix_length = min(stat.st_size, 4096)
                final_size = os.fstat(stream.fileno()).st_size
                if final_size < stat.st_size:
                    diagnostics.append(
                        ScanDiagnostic(
                            path.name,
                            "truncated-during-scan",
                            "file shrank during scanning; watermark was not advanced",
                        )
                    )
                else:
                    result.cursors[identity] = FileCursor(
                        identity=identity,
                        source_name=path.name,
                        offset=committed,
                        size=stat.st_size,
                        prefix_length=prefix_length,
                        prefix_sha256=_stream_prefix(stream, prefix_length),
                    )
        except ScanCancelled:
            raise
        except OSError as exc:
            diagnostics.append(ScanDiagnostic(path.name, "unreadable", str(exc)))
    if progress:
        progress(len(selected), len(selected), "")
    return ScanResult(
        result,
        len(selected),
        files_read,
        bytes_read,
        events_seen,
        added,
        unresolved_added,
        reconciled,
        tuple(sorted(unmatched, key=str.casefold)),
        tuple(diagnostics),
    )


@dataclass(frozen=True)
class ImportCandidate:
    blueprint_id: str
    name: str


@dataclass(frozen=True)
class ImportPlan:
    source_name: str
    source_sha256: str
    candidates: tuple[ImportCandidate, ...]
    unmatched_names: tuple[str, ...]
    already_owned: tuple[str, ...]

    @property
    def additions(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class ResolutionPlan:
    unresolved: UnresolvedAcquisition
    blueprint_id: str
    blueprint_name: str


def plan_resolution(
    state: OwnershipState,
    catalog: BlueprintCatalog,
    acquisition_selector: str,
    blueprint_id: str,
) -> ResolutionPlan:
    selector = acquisition_selector.strip().casefold()
    if len(selector) < 8 or not re.fullmatch(r"[0-9a-f]+", selector):
        raise OwnershipError("acquisition selector must be at least 8 hexadecimal characters")
    matches = [
        item
        for item in state.unresolved
        if item.acquisition.acquisition_id.casefold().startswith(selector)
    ]
    if not matches:
        raise OwnershipError("no unresolved acquisition matches that selector")
    if len(matches) > 1:
        raise OwnershipError("acquisition selector is ambiguous; provide more characters")
    item = matches[0]
    if blueprint_id not in catalog.by_id:
        raise OwnershipError("selected blueprint ID is not present in this catalog")
    candidates = catalog.resolve_name_candidates(item.name)
    if blueprint_id not in candidates:
        raise OwnershipError(
            "selected blueprint is not an exact-name candidate for this acquisition"
        )
    return ResolutionPlan(item, blueprint_id, catalog.by_id[blueprint_id].name)


def apply_resolution(plan: ResolutionPlan, state: OwnershipState) -> OwnershipState:
    result = copy.deepcopy(state)
    result.unresolved = [
        item
        for item in result.unresolved
        if item.acquisition.acquisition_id != plan.unresolved.acquisition.acquisition_id
    ]
    if len(result.unresolved) == len(state.unresolved):
        raise OwnershipError("unresolved acquisition changed after preview")
    result.add(plan.blueprint_id, plan.blueprint_name, plan.unresolved.acquisition)
    return result


def _read_import(path: Path) -> tuple[bytes, list[tuple[str | None, str]]]:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_IMPORT_BYTES:
        raise OwnershipError("ownership import is missing or exceeds the size limit")
    payload = path.read_bytes()
    if len(payload) > MAX_IMPORT_BYTES:
        raise OwnershipError("ownership import exceeds the size limit")
    rows: list[tuple[str | None, str]] = []
    try:
        text = payload.decode("utf-8-sig")
        if path.suffix.casefold() == ".json":
            _check_json_depth(text, label="invalid ownership import")
            data = json.loads(text, object_pairs_hook=_strict_object)
            if not isinstance(data, dict) or not isinstance(data.get("blueprints"), list):
                raise OwnershipError("JSON import requires a blueprints array")
            if len(data["blueprints"]) > MAX_IMPORT_ENTRIES:
                raise OwnershipError("ownership import entry limit exceeded")
            for raw in data["blueprints"]:
                if not isinstance(raw, dict) or raw.get("completed", True) is not True:
                    continue
                name = raw.get("name")
                blueprint_id = raw.get("blueprint_id") or raw.get("id")
                if isinstance(name, str):
                    rows.append((blueprint_id if isinstance(blueprint_id, str) else None, name))
        elif path.suffix.casefold() == ".csv":
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or "name" not in reader.fieldnames:
                raise OwnershipError("CSV import requires a name column")
            if len(reader.fieldnames) > 64 or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise OwnershipError("CSV import has duplicate or excessive columns")
            for raw in reader:
                if len(rows) >= MAX_IMPORT_ENTRIES:
                    raise OwnershipError("ownership import entry limit exceeded")
                name = raw.get("name") or ""
                if len(name) > 1 and name[0] == "'" and name[1] in "=+-@":
                    name = name[1:]
                rows.append((raw.get("blueprint_id") or None, name))
        else:
            raise OwnershipError("ownership import must be .json or .csv")
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, RecursionError) as exc:
        raise OwnershipError(f"invalid ownership import: {exc}") from exc
    if len(rows) > MAX_IMPORT_ENTRIES:
        raise OwnershipError("ownership import entry limit exceeded")
    return payload, rows


def plan_import(path: Path, catalog: BlueprintCatalog, state: OwnershipState) -> ImportPlan:
    payload, rows = _read_import(path)
    by_id = catalog.by_id
    candidates: dict[str, ImportCandidate] = {}
    unmatched: set[str] = set()
    already: set[str] = set()
    for supplied_id, raw_name in rows:
        name = raw_name.strip()
        if not name or len(name) > MAX_NAME_LENGTH or any(char in name for char in "\r\n\0"):
            if name:
                unmatched.add(name[:MAX_NAME_LENGTH])
            continue
        blueprint_id = supplied_id if supplied_id in by_id else catalog.resolve_name(name)
        if blueprint_id is None:
            unmatched.add(name)
        elif blueprint_id in state.records:
            already.add(by_id[blueprint_id].name)
        else:
            candidates[blueprint_id] = ImportCandidate(blueprint_id, by_id[blueprint_id].name)
    return ImportPlan(
        Path(path).name,
        hashlib.sha256(payload).hexdigest(),
        tuple(candidates[key] for key in sorted(candidates)),
        tuple(sorted(unmatched, key=str.casefold)),
        tuple(sorted(already, key=str.casefold)),
    )


def apply_import(plan: ImportPlan, state: OwnershipState) -> OwnershipState:
    result = copy.deepcopy(state)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for candidate in plan.candidates:
        acquisition_id = hashlib.sha256(
            f"import\0{plan.source_sha256}\0{candidate.blueprint_id}".encode("utf-8")
        ).hexdigest()
        result.add(
            candidate.blueprint_id,
            candidate.name,
            Acquisition(acquisition_id, "import", now, plan.source_name, plan.source_sha256),
        )
    return result


def export_json(state: OwnershipState, catalog: BlueprintCatalog) -> bytes:
    by_id = catalog.by_id
    payload = {
        "version": 1,
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "missions": [],
        "blueprints": [
            {
                "blueprint_id": blueprint_id,
                "name": by_id.get(blueprint_id, record).name,
                "completed": True,
                "favorite": False,
            }
            for blueprint_id, record in sorted(state.records.items())
        ],
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_safe(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def export_csv(state: OwnershipState, catalog: BlueprintCatalog) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(("blueprint_id", "name", "category", "acquired_at", "source"))
    by_id = catalog.by_id
    for blueprint_id, record in sorted(state.records.items()):
        entry = by_id.get(blueprint_id)
        acquired = min((item.acquired_at for item in record.acquisitions if item.acquired_at), default="")
        sources = ";".join(sorted({item.source for item in record.acquisitions}))
        writer.writerow((blueprint_id, _csv_safe(entry.name if entry else record.name), entry.category if entry else "unknown", acquired, sources))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def write_export(destination: Path, payload: bytes, *, store_path: Path | None = None) -> None:
    path = Path(destination)
    if path.suffix.casefold() not in {".json", ".csv"}:
        raise OwnershipError("ownership export destination must be .json or .csv")
    if store_path is not None and path.resolve() == Path(store_path).resolve():
        raise OwnershipError("refusing to overwrite the ownership store with an export")
    _atomic_bytes(path, payload)
