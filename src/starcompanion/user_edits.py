"""Per-channel user overrides, imports, and model-level undo/redo.

``user.ini`` is the only authoritative user value store.  The adjacent JSON
journal contains reversible commands, but is trusted only while its digest
matches the INI.  A stale or damaged journal can therefore lose undo history;
it can never replay over newer user data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .ini import BOM, LocalizationFile
from .install import normalize_channel
from .validate import Severity, validate_value

APP_NAME = "StarCompanion"
HISTORY_SCHEMA = 1
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_KEY_LENGTH = 512
MAX_VALUE_LENGTH = 1024 * 1024
MAX_HISTORY = 100
MAX_HISTORY_FILE_BYTES = 64 * 1024 * 1024
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._()\-]{0,63}\Z")


class UserEditError(ValueError):
    pass


class EmptyUserModelError(UserEditError):
    pass


class NothingToUndoError(UserEditError):
    pass


class NothingToRedoError(UserEditError):
    pass


class ConflictChoice(Enum):
    ERROR = "error"
    KEEP = "keep"
    INCOMING = "incoming"


def data_dir() -> Path:
    configured = os.environ.get("STARCOMPANION_DATA")
    if configured:
        return Path(configured)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME / "data"
    root = os.environ.get("XDG_DATA_HOME")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _scope(value: str, label: str, *, uppercase: bool = False) -> str:
    normalized = value.strip().upper() if uppercase else value.strip().casefold()
    if not _SCOPE.fullmatch(normalized):
        raise UserEditError(
            f"invalid {label} {value!r}; use letters, numbers, dot, dash, "
            "underscore, or language parentheses"
        )
    return normalized


def user_ini_path(channel: str, language: str, *, root: Path | None = None) -> Path:
    base = (root or data_dir()).resolve()
    try:
        normalized_channel = normalize_channel(channel)
    except ValueError as exc:
        raise UserEditError(str(exc)) from exc
    path = base / "channels" / normalized_channel / _scope(
        language, "language"
    ) / "user.ini"
    if base not in path.resolve().parents:
        raise UserEditError("user.ini path escapes the selected data root")
    return path


def _validate_key(key: str) -> None:
    if (
        not key
        or len(key) > MAX_KEY_LENGTH
        or key.strip() != key
        or any(char in key for char in "=\r\n")
    ):
        raise UserEditError(f"invalid localization key {key!r}")


def _validate_values(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        _validate_key(key)
        if not isinstance(value, str):
            raise UserEditError(f"value for {key!r} must be text")
        if len(value) > MAX_VALUE_LENGTH:
            raise UserEditError(f"value for {key!r} is too large")
        # Unknown complete placeholder tags are context-dependent: CIG stock
        # strings legitimately contain names outside our emphasis vocabulary.
        # Keep them in the user model, then validate against the selected stock
        # value during render/apply.  Universally malformed constructs remain
        # rejected here.
        errors = [
            issue
            for issue in validate_value(value)
            if issue.severity is Severity.ERROR and issue.code != "unknown-tag"
        ]
        if errors:
            raise UserEditError(f"invalid value for {key!r}: {errors[0]}")


def _serialize(values: Mapping[str, str]) -> str:
    body = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    return BOM + body + ("\n" if body else "")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def load_ini(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    if path.stat().st_size > MAX_FILE_BYTES:
        raise UserEditError(f"{path} exceeds the {MAX_FILE_BYTES:,}-byte import limit")
    parsed = LocalizationFile.load(path)
    if len(parsed.entries()) > MAX_ENTRIES:
        raise UserEditError(f"{path} exceeds the {MAX_ENTRIES:,}-entry import limit")
    values: dict[str, str] = {}
    for entry in parsed.entries():
        if entry.key in values:
            raise UserEditError(f"duplicate localization key {entry.key!r} in {path}")
        values[entry.key] = entry.value
    _validate_values(values)
    return values


@dataclass(frozen=True)
class UserEditStore:
    channel: str
    language: str = "english"
    root: Path | None = None

    @property
    def path(self) -> Path:
        return user_ini_path(self.channel, self.language, root=self.root)

    @property
    def history_path(self) -> Path:
        return self.path.with_name("history.json")

    def load(self) -> dict[str, str]:
        return load_ini(self.path)

    def save(self, values: Mapping[str, str], *, allow_empty: bool = False) -> None:
        materialized = dict(values)
        _validate_values(materialized)
        if len(materialized) > MAX_ENTRIES:
            raise UserEditError(
                f"user.ini exceeds the {MAX_ENTRIES:,}-entry storage limit"
            )
        serialized = _serialize(materialized)
        if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
            raise UserEditError(
                f"user.ini exceeds the {MAX_FILE_BYTES:,}-byte storage limit"
            )
        if not materialized and self.path.is_file() and self.load() and not allow_empty:
            raise EmptyUserModelError(
                "refusing to replace populated user.ini with an empty model"
            )
        _atomic_write(self.path, serialized)

    def export(self, destination: Path) -> None:
        values = self.load()
        _atomic_write(destination, _serialize(values))


@dataclass(frozen=True)
class Change:
    key: str
    before: str | None
    after: str | None

    def reversed(self) -> Change:
        return Change(self.key, self.after, self.before)


@dataclass(frozen=True)
class EditCommand:
    label: str
    changes: tuple[Change, ...]

    @classmethod
    def set(cls, values: Mapping[str, str], key: str, value: str) -> EditCommand:
        return cls(f"set {key}", (Change(key, values.get(key), value),))

    @classmethod
    def remove(cls, values: Mapping[str, str], key: str) -> EditCommand:
        if key not in values:
            raise UserEditError(f"no user override exists for {key!r}")
        return cls(f"remove {key}", (Change(key, values[key], None),))

    def apply(self, values: Mapping[str, str]) -> dict[str, str]:
        result = dict(values)
        for change in self.changes:
            if result.get(change.key) != change.before:
                raise UserEditError(
                    f"command {self.label!r} is stale at {change.key!r}"
                )
            if change.after is None:
                result.pop(change.key, None)
            else:
                result[change.key] = change.after
        return result

    def undo(self, values: Mapping[str, str]) -> dict[str, str]:
        result = dict(values)
        for change in reversed(self.changes):
            reverse = change.reversed()
            if result.get(reverse.key) != reverse.before:
                raise UserEditError(
                    f"command {self.label!r} cannot be undone at {reverse.key!r}"
                )
            if reverse.after is None:
                result.pop(reverse.key, None)
            else:
                result[reverse.key] = reverse.after
        return result


@dataclass(frozen=True)
class ImportPlan:
    incoming: Mapping[str, str]
    changes: tuple[Change, ...]
    conflicts: tuple[str, ...]
    unchanged: tuple[str, ...]
    choice: ConflictChoice

    @property
    def added(self) -> tuple[str, ...]:
        return tuple(change.key for change in self.changes if change.before is None)

    @property
    def changed(self) -> tuple[str, ...]:
        return tuple(change.key for change in self.changes if change.before is not None)

    def summary(self) -> str:
        return (
            f"{len(self.added)} add, {len(self.changed)} change, "
            f"{len(self.unchanged)} unchanged, {len(self.conflicts)} conflict"
        )


def plan_import(
    current: Mapping[str, str],
    incoming: Mapping[str, str],
    *,
    choice: ConflictChoice = ConflictChoice.ERROR,
) -> ImportPlan:
    _validate_values(incoming)
    changes: list[Change] = []
    conflicts: list[str] = []
    unchanged: list[str] = []
    for key in sorted(incoming):
        value = incoming[key]
        if key not in current:
            changes.append(Change(key, None, value))
        elif current[key] == value:
            unchanged.append(key)
        else:
            conflicts.append(key)
            if choice is ConflictChoice.INCOMING:
                changes.append(Change(key, current[key], value))
    return ImportPlan(
        incoming=dict(incoming),
        changes=tuple(changes),
        conflicts=tuple(conflicts),
        unchanged=tuple(unchanged),
        choice=choice,
    )


def _digest(values: Mapping[str, str]) -> str:
    return hashlib.sha256(_serialize(values).encode("utf-8")).hexdigest()


def _command_to_data(command: EditCommand) -> dict[str, object]:
    return {
        "label": command.label,
        "changes": [
            {"key": item.key, "before": item.before, "after": item.after}
            for item in command.changes
        ],
    }


def _command_from_data(data: object) -> EditCommand:
    if not isinstance(data, dict) or not isinstance(data.get("label"), str):
        raise UserEditError("invalid edit history command")
    raw_changes = data.get("changes")
    if not isinstance(raw_changes, list):
        raise UserEditError("invalid edit history changes")
    if len(raw_changes) > MAX_ENTRIES:
        raise UserEditError("edit history command has too many changes")
    changes: list[Change] = []
    for raw in raw_changes:
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            raise UserEditError("invalid edit history change")
        before, after = raw.get("before"), raw.get("after")
        if before is not None and not isinstance(before, str):
            raise UserEditError("invalid edit history before value")
        if after is not None and not isinstance(after, str):
            raise UserEditError("invalid edit history after value")
        _validate_key(raw["key"])
        if before is not None:
            _validate_values({raw["key"]: before})
        if after is not None:
            _validate_values({raw["key"]: after})
        changes.append(Change(raw["key"], before, after))
    return EditCommand(data["label"], tuple(changes))


class EditSession:
    """Shared command model for CLI now and Qt later."""

    def __init__(self, store: UserEditStore):
        self.store = store
        self.values = store.load()
        self.commands: list[EditCommand] = []
        self.cursor = 0
        self.history_recovered = True
        self._load_history()

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.commands)

    def execute(self, command: EditCommand, *, allow_empty: bool = False) -> None:
        updated = command.apply(self.values)
        commands = self.commands[: self.cursor] + [command]
        if len(commands) > MAX_HISTORY:
            commands = commands[-MAX_HISTORY:]
        self._commit(updated, commands, len(commands), allow_empty=allow_empty)

    def import_plan(self, plan: ImportPlan) -> None:
        if plan.conflicts and plan.choice is ConflictChoice.ERROR:
            raise UserEditError(
                "import has unresolved conflicts: " + ", ".join(plan.conflicts[:10])
            )
        if not plan.changes:
            return
        self.execute(EditCommand("import user.ini", plan.changes))

    def undo(self) -> EditCommand:
        if not self.can_undo:
            raise NothingToUndoError("nothing to undo")
        command = self.commands[self.cursor - 1]
        updated = command.undo(self.values)
        self._commit(updated, self.commands, self.cursor - 1, allow_empty=True)
        return command

    def redo(self) -> EditCommand:
        if not self.can_redo:
            raise NothingToRedoError("nothing to redo")
        command = self.commands[self.cursor]
        updated = command.apply(self.values)
        self._commit(updated, self.commands, self.cursor + 1, allow_empty=True)
        return command

    def _commit(
        self,
        values: Mapping[str, str],
        commands: list[EditCommand],
        cursor: int,
        *,
        allow_empty: bool,
    ) -> None:
        # The authoritative INI is committed first.  If the process stops
        # before the journal update, its digest mismatch makes history inert.
        if self.store.load() != self.values:
            raise UserEditError(
                "user.ini changed outside this edit session; reload before saving"
            )
        self.store.save(values, allow_empty=allow_empty)
        kept_commands = list(commands)
        kept_cursor = cursor
        while True:
            payload = {
                "schema_version": HISTORY_SCHEMA,
                "cursor": kept_cursor,
                "user_ini_sha256": _digest(values),
                "commands": [
                    _command_to_data(command) for command in kept_commands
                ],
            }
            history_text = json.dumps(payload, indent=2) + "\n"
            if len(history_text.encode("utf-8")) <= MAX_HISTORY_FILE_BYTES:
                break
            if not kept_commands:
                raise UserEditError("empty edit history exceeds its size limit")
            if kept_cursor > 0:
                kept_commands.pop(0)
                kept_cursor -= 1
            else:
                kept_commands.pop()
        try:
            _atomic_write(self.store.history_path, history_text)
        except OSError:
            # user.ini is already committed.  Keep this live model aligned and
            # make the old, now-mismatched history inert before reporting the
            # journal failure to the caller.
            self.values = dict(values)
            self.commands = []
            self.cursor = 0
            self.history_recovered = False
            raise
        self.values = dict(values)
        self.commands = kept_commands
        self.cursor = kept_cursor

    def _load_history(self) -> None:
        try:
            if self.store.history_path.stat().st_size > MAX_HISTORY_FILE_BYTES:
                raise UserEditError("edit history exceeds its size limit")
            data = json.loads(self.store.history_path.read_text(encoding="utf-8"))
            if data.get("schema_version") != HISTORY_SCHEMA:
                raise UserEditError("unsupported edit history schema")
            if data.get("user_ini_sha256") != _digest(self.values):
                raise UserEditError("edit history does not match user.ini")
            commands = [_command_from_data(item) for item in data.get("commands", [])]
            if len(commands) > MAX_HISTORY:
                raise UserEditError("edit history contains too many commands")
            cursor = data.get("cursor")
            if not isinstance(cursor, int) or not 0 <= cursor <= len(commands):
                raise UserEditError("invalid edit history cursor")
            # Prove that both the undo chain leading to the current INI and the
            # remaining redo branch are internally coherent before exposing
            # either to callers.
            base = dict(self.values)
            for command in reversed(commands[:cursor]):
                base = command.undo(base)
            replay = dict(base)
            for command in commands[:cursor]:
                replay = command.apply(replay)
            if replay != self.values:
                raise UserEditError("edit history does not reconstruct user.ini")
            for command in commands[cursor:]:
                replay = command.apply(replay)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, UserEditError, AttributeError):
            self.history_recovered = False
            return
        self.commands = commands
        self.cursor = cursor


__all__ = [
    "Change",
    "ConflictChoice",
    "EditCommand",
    "EditSession",
    "EmptyUserModelError",
    "ImportPlan",
    "NothingToRedoError",
    "NothingToUndoError",
    "UserEditError",
    "UserEditStore",
    "data_dir",
    "load_ini",
    "plan_import",
    "user_ini_path",
]
