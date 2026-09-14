"""Build a deterministic review catalog from visible GUI source text."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from .ui_text import pseudo_localize

CATALOG_SCHEMA = 1
DEFAULT_SOURCE_ROOT = Path(__file__).parent
DEFAULT_REVIEW_PATH = Path(__file__).parents[3] / "docs" / "ui-translation-source.json"

_VISIBLE_CONSTRUCTORS = frozenset(
    {
        "QAction",
        "QCheckBox",
        "QCommandLinkButton",
        "QGroupBox",
        "QLabel",
        "QListWidgetItem",
        "QMenu",
        "QProgressDialog",
        "QPushButton",
        "QRadioButton",
        "AppEvent",
        "CoachStep",
        "DeltaPackError",
        "EditCommand",
        "EmptyState",
        "IndexError",
        "LayoutError",
        "LayoutLoad",
        "MetricTile",
        "NoticeBanner",
        "OwnershipConflictError",
        "PortabilityError",
        "PreferenceLoad",
        "RuntimeError",
        "SectionCard",
        "StatusCard",
        "TypeError",
        "UserEditError",
        "ValueError",
    }
)
_VISIBLE_METHODS = frozenset(
    {
        "addAction",
        "addItem",
        "addMenu",
        "addRow",
        "addTab",
        "append",
        "extend",
        "insertItem",
        "publish",
        "setAccessibleDescription",
        "setAccessibleName",
        "setHeaderLabels",
        "setLabelText",
        "setPlaceholderText",
        "setPlainText",
        "setPrefix",
        "setSpecialValueText",
        "setStatusTip",
        "setSuffix",
        "setText",
        "setTitle",
        "setToolTip",
        "setWindowTitle",
        "set_message",
        "set_status",
        "set_value",
        "_apply_ship_command",
        "_badge",
        "_choice_button",
        "_filter_combo",
        "_muted",
        "_run_operation",
        "_plan_data_location",
        "set_ui_preference_warning",
        "_start_job",
        "_text_view",
        "_warn",
    }
)
_DIALOG_METHODS = frozenset(
    {
        "critical",
        "getExistingDirectory",
        "getMultiLineText",
        "getOpenFileName",
        "getSaveFileName",
        "information",
        "question",
        "warning",
    }
)
_VISIBLE_CONSTANT = re.compile(
    r"(?:ARTICLE|CAPTION|CHOICE|DESCRIPTION|DETAIL|ENGLISH|HEADER|HELP|LABEL|LINE|LOOK|"
    r"MESSAGE|METRIC|MODE|OPTION|OUTCOME|PREFIX|PROMPT|QUESTION|SEPARATOR|STATUS|STEP|"
    r"STYLE|TEXT|TITLE|TOGGLE|WORDING)",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class UiStringLocation:
    path: str
    line: int
    context: str


@dataclass(frozen=True)
class UiStringEntry:
    message_id: str
    text: str
    pseudo: str
    placeholders: tuple[str, ...]
    locations: tuple[UiStringLocation, ...]


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _placeholder(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "value"


def _template(node: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value, ()) if node.value.strip() else None
    if not isinstance(node, ast.JoinedStr):
        return None
    parts: list[str] = []
    placeholders: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        elif isinstance(part, ast.FormattedValue):
            name = _placeholder(part.value)
            placeholders.append(name)
            parts.append("{" + name + "}")
    text = "".join(parts)
    return (text, tuple(placeholders)) if text.strip() else None


def _iter_strings(node: ast.AST) -> Iterable[ast.AST]:
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        if _template(node) is not None:
            yield node
        return
    if isinstance(node, ast.Dict):
        for value in node.values:
            yield from _iter_strings(value)
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_strings(child)


class _VisibleStringVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.found: list[tuple[str, tuple[str, ...], UiStringLocation]] = []

    def _add(self, node: ast.AST, context: str) -> None:
        rendered = _template(node)
        if rendered is None:
            return
        text, placeholders = rendered
        self.found.append(
            (text, placeholders, UiStringLocation(self.path, node.lineno, context))
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        selected: Iterable[ast.AST] = ()
        if name in _VISIBLE_CONSTRUCTORS:
            selected = node.args
        elif name == "PageSpec":
            selected = node.args[1:]
        elif name in _VISIBLE_METHODS:
            if name in {"addItem", "insertItem", "addAction"}:
                selected = node.args[:1]
            elif name == "addTab":
                selected = node.args[1:2]
            else:
                selected = node.args
        elif name in _DIALOG_METHODS:
            selected = node.args[1:]
        for argument in selected:
            for text_node in _iter_strings(argument):
                self._add(text_node, name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if any(_VISIBLE_CONSTANT.search(name) for name in names):
            for text_node in _iter_strings(node.value):
                self._add(text_node, names[0])
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and node.value is not None
            and _VISIBLE_CONSTANT.search(node.target.id)
        ):
            for text_node in _iter_strings(node.value):
                self._add(text_node, node.target.id)
        self.generic_visit(node)


def collect_ui_strings(source_root: Path = DEFAULT_SOURCE_ROOT) -> tuple[UiStringEntry, ...]:
    """Return every statically identifiable user-facing GUI string."""

    grouped: dict[str, tuple[str, set[str], set[UiStringLocation]]] = {}
    for path in sorted(source_root.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        relative = path.relative_to(source_root).as_posix()
        visitor = _VisibleStringVisitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        for value, placeholders, location in visitor.found:
            marker = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            source, found_placeholders, locations = grouped.setdefault(
                marker, (value, set(), set())
            )
            if source != value:
                raise ValueError("UI catalog message-id collision")
            found_placeholders.update(placeholders)
            locations.add(location)

    entries = []
    for marker, (source, placeholders, locations) in grouped.items():
        entries.append(
            UiStringEntry(
                f"ui.{marker}",
                source,
                pseudo_localize(source),
                tuple(sorted(placeholders)),
                tuple(sorted(locations)),
            )
        )
    return tuple(sorted(entries, key=lambda item: (item.locations[0], item.message_id)))


def catalog_document(source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, object]:
    entries = collect_ui_strings(source_root)
    return {
        "schema": CATALOG_SCHEMA,
        "source_locale": "en-US",
        "string_count": len(entries),
        "location_count": sum(len(item.locations) for item in entries),
        "entries": [
            {
                "id": item.message_id,
                "text": item.text,
                "pseudo": item.pseudo,
                "placeholders": list(item.placeholders),
                "locations": [
                    {"path": loc.path, "line": loc.line, "context": loc.context}
                    for loc in item.locations
                ],
                "translator_review": "required",
            }
            for item in entries
        ],
    }


def render_catalog(source_root: Path = DEFAULT_SOURCE_ROOT) -> str:
    return json.dumps(catalog_document(source_root), ensure_ascii=False, indent=2) + "\n"


def write_catalog(path: Path, source_root: Path = DEFAULT_SOURCE_ROOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_catalog(source_root), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify the offline UI review catalog.")
    parser.add_argument("--output", type=Path, default=DEFAULT_REVIEW_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = render_catalog()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            parser.error(f"UI review catalog is stale: {args.output}")
        return 0
    write_catalog(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CATALOG_SCHEMA",
    "DEFAULT_REVIEW_PATH",
    "UiStringEntry",
    "UiStringLocation",
    "catalog_document",
    "collect_ui_strings",
    "render_catalog",
    "write_catalog",
]
