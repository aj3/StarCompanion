"""Local-only persistence for window, splitter, and table layout.

Layout is intentionally stored outside ``preferences.json``.  Portable
settings describe behaviour; this file describes one machine's screens and
widget sizes and must never be included in a settings export.
"""

from __future__ import annotations

import json
import os
import re
import stat as stat_module
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..user_edits import data_dir

LAYOUT_SCHEMA = 1
LAYOUT_FILENAME = "ui-layout.json"
MAX_LAYOUT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 16
MAX_SPLITTERS = 16
MAX_SPLITTER_PARTS = 8
MAX_COLUMN_GROUPS = 16
MAX_COLUMNS = 32
MAX_WIDGET_SIZE = 32_768
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 480
MIN_COLUMN_WIDTH = 16
MAX_COLUMN_WIDTH = 4_096
MAX_SCREEN_COORDINATE = 1_000_000

_LAYOUT_KEY = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


class LayoutError(ValueError):
    """A local layout file is unsafe, unsupported, or invalid."""


@dataclass(frozen=True)
class WindowGeometry:
    x: int
    y: int
    width: int
    height: int
    maximized: bool = False


@dataclass(frozen=True)
class LayoutState:
    """Structured Qt-independent layout values keyed by stable widget names."""

    window: WindowGeometry | None = None
    splitters: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    columns: Mapping[str, tuple[int, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LayoutLoad:
    state: LayoutState
    warning: str | None = None
    write_blocked: bool = False


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LayoutError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _check_json_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise LayoutError("layout JSON nesting limit exceeded")
        elif character in "]}":
            depth = max(0, depth - 1)


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = expected - actual
        unknown = actual - expected
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise LayoutError(f"{label} fields are invalid ({'; '.join(details)})")


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LayoutError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _window_from_json(value: object) -> WindowGeometry | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LayoutError("window geometry must be an object or null")
    _exact_fields(value, {"x", "y", "width", "height", "maximized"}, "window")
    maximized = value["maximized"]
    if type(maximized) is not bool:
        raise LayoutError("window maximized must be true or false")
    return WindowGeometry(
        _integer(
            value["x"],
            -MAX_SCREEN_COORDINATE,
            MAX_SCREEN_COORDINATE,
            "window x",
        ),
        _integer(
            value["y"],
            -MAX_SCREEN_COORDINATE,
            MAX_SCREEN_COORDINATE,
            "window y",
        ),
        _integer(value["width"], MIN_WINDOW_WIDTH, MAX_WIDGET_SIZE, "window width"),
        _integer(value["height"], MIN_WINDOW_HEIGHT, MAX_WIDGET_SIZE, "window height"),
        maximized,
    )


def _sized_groups(
    value: object,
    *,
    label: str,
    maximum_groups: int,
    minimum_parts: int,
    maximum_parts: int,
    minimum_size: int,
    maximum_size: int,
) -> dict[str, tuple[int, ...]]:
    if not isinstance(value, dict):
        raise LayoutError(f"{label} must be an object")
    if len(value) > maximum_groups:
        raise LayoutError(f"{label} group limit exceeded")
    result: dict[str, tuple[int, ...]] = {}
    for key, raw_sizes in value.items():
        if not isinstance(key, str) or not _LAYOUT_KEY.fullmatch(key):
            raise LayoutError(f"{label} contains an invalid widget key")
        if not isinstance(raw_sizes, list) or not minimum_parts <= len(raw_sizes) <= maximum_parts:
            raise LayoutError(
                f"{label} {key!r} must contain {minimum_parts} to {maximum_parts} sizes"
            )
        sizes = tuple(
            _integer(item, minimum_size, maximum_size, f"{label} {key!r} size")
            for item in raw_sizes
        )
        if label == "splitters" and not any(sizes):
            raise LayoutError(f"splitters {key!r} cannot contain only zero sizes")
        result[key] = sizes
    return result


def decode_layout(payload: bytes) -> LayoutState:
    """Decode and validate one complete versioned layout document."""

    if len(payload) > MAX_LAYOUT_BYTES:
        raise LayoutError("layout exceeds the size limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LayoutError("layout is not UTF-8 JSON") from exc
    _check_json_depth(text)
    try:
        raw = json.loads(text, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise LayoutError(f"layout is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise LayoutError("layout must be a JSON object")
    _exact_fields(raw, {"schema", "window", "splitters", "columns"}, "layout")
    if type(raw["schema"]) is not int or raw["schema"] != LAYOUT_SCHEMA:
        raise LayoutError(f"unsupported layout schema {raw['schema']!r}")
    return LayoutState(
        window=_window_from_json(raw["window"]),
        splitters=_sized_groups(
            raw["splitters"],
            label="splitters",
            maximum_groups=MAX_SPLITTERS,
            minimum_parts=2,
            maximum_parts=MAX_SPLITTER_PARTS,
            minimum_size=0,
            maximum_size=MAX_WIDGET_SIZE,
        ),
        columns=_sized_groups(
            raw["columns"],
            label="columns",
            maximum_groups=MAX_COLUMN_GROUPS,
            minimum_parts=1,
            maximum_parts=MAX_COLUMNS,
            minimum_size=MIN_COLUMN_WIDTH,
            maximum_size=MAX_COLUMN_WIDTH,
        ),
    )


def encode_layout(state: LayoutState) -> bytes:
    """Validate and canonically encode a layout supplied by the GUI."""

    if not isinstance(state, LayoutState):
        raise LayoutError("layout state has the wrong type")
    window = state.window
    raw_window = None
    if window is not None:
        if not isinstance(window, WindowGeometry):
            raise LayoutError("window geometry has the wrong type")
        raw_window = {
            "x": window.x,
            "y": window.y,
            "width": window.width,
            "height": window.height,
            "maximized": window.maximized,
        }
    # Round-tripping through the decoder keeps one validation path for both
    # disk input and values captured from Qt widgets.
    try:
        document = {
            "schema": LAYOUT_SCHEMA,
            "window": raw_window,
            "splitters": {key: list(value) for key, value in state.splitters.items()},
            "columns": {key: list(value) for key, value in state.columns.items()},
        }
        payload = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (AttributeError, TypeError, ValueError) as exc:
        raise LayoutError("layout state contains invalid mappings or sizes") from exc
    decode_layout(payload)
    return payload


def _ordinary_file(path: Path) -> bool:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LayoutError(
            f"layout path could not be inspected: {exc.strerror or exc.__class__.__name__}"
        ) from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = bool(attributes & getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if reparse or path.is_symlink() or not stat_module.S_ISREG(metadata.st_mode):
        raise LayoutError("layout path is not an ordinary local file")
    return True


def _read_layout_file(path: Path) -> bytes | None:
    if not _ordinary_file(path):
        return None
    try:
        size = path.stat().st_size
        if size > MAX_LAYOUT_BYTES:
            raise LayoutError("layout exceeds the size limit")
        payload = path.read_bytes()
    except OSError as exc:
        raise LayoutError(
            f"layout could not be read: {exc.strerror or exc.__class__.__name__}"
        ) from exc
    if len(payload) > MAX_LAYOUT_BYTES:
        raise LayoutError("layout exceeds the size limit")
    return payload


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class LocalLayoutStore:
    """Fail-safe owner of the non-portable ``ui-layout.json`` file."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or data_dir()).resolve()
        self._write_blocked = False

    @property
    def path(self) -> Path:
        return self.root / LAYOUT_FILENAME

    @property
    def write_blocked(self) -> bool:
        return self._write_blocked

    def load(self) -> LayoutLoad:
        try:
            payload = _read_layout_file(self.path)
            state = decode_layout(payload) if payload is not None else LayoutState()
        except (LayoutError, OSError) as exc:
            self._write_blocked = True
            return LayoutLoad(
                LayoutState(),
                f"Window layout could not be loaded: {exc}. The file was left unchanged; "
                "use Reset window layout to replace it.",
                True,
            )
        self._write_blocked = False
        return LayoutLoad(state)

    def save(self, state: LayoutState) -> None:
        payload = encode_layout(state)
        if self._write_blocked:
            raise LayoutError(
                "refusing to overwrite an invalid layout; reset the window layout first"
            )
        # Revalidate an existing file immediately before replacement.  This
        # also catches external corruption after a successful startup load.
        try:
            current = _read_layout_file(self.path)
            if current is not None:
                decode_layout(current)
        except LayoutError as exc:
            self._write_blocked = True
            raise LayoutError(
                "refusing to overwrite an invalid layout; reset the window layout first"
            ) from exc
        _atomic_write(self.path, payload)

    def clear(self) -> bool:
        """Remove an ordinary layout file after an explicit UI reset."""

        if not _ordinary_file(self.path):
            self._write_blocked = False
            return False
        try:
            self.path.unlink()
        except OSError as exc:
            raise LayoutError(
                f"layout could not be cleared: {exc.strerror or exc.__class__.__name__}"
            ) from exc
        self._write_blocked = False
        return True

    reset = clear


__all__ = [
    "LAYOUT_FILENAME",
    "LAYOUT_SCHEMA",
    "LayoutError",
    "LayoutLoad",
    "LayoutState",
    "LocalLayoutStore",
    "WindowGeometry",
    "decode_layout",
    "encode_layout",
]
