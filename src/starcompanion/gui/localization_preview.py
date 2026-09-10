"""Safe, bounded rich previews for Star Citizen localization values."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ..validate import ALLOWED_TAGS, VOID_TAGS

MAX_PREVIEW_CHARACTERS = 32 * 1024

_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)>")
_MISSION = re.compile(r"~mission\(([^()\r\n]*)\)")
_TOKEN = re.compile(r"</?[A-Za-z][A-Za-z0-9]*>|~mission\([^()\r\n]*\)")

_STYLE = """<style>
.sc-localization-preview { white-space: pre-wrap; overflow-wrap: anywhere; }
.sc-cig-em { border-bottom: 1px dotted currentColor; }
.sc-cig-em-label { font-size: 0.75em; font-weight: 600; opacity: 0.72; }
.sc-cig-token { border: 1px solid currentColor; padding: 0 0.2em; }
.sc-cig-none { border: 1px dotted currentColor; font-weight: 600; }
.sc-preview-note { border-top: 1px solid currentColor; margin-top: 0.7em;
                   padding-top: 0.45em; font-style: italic; opacity: 0.8; }
</style>"""


@dataclass(frozen=True)
class LocalizationPreview:
    """A self-contained HTML fragment and its truncation state."""

    html: str
    truncated: bool
    message: str
    source_characters: int
    rendered_characters: int


@dataclass
class _TagFrame:
    name: str
    opening_index: int
    nested_indices: set[int] = field(default_factory=set)


def _balanced_tag_indices(tokens: list[re.Match[str]]) -> set[int]:
    """Return only complete, correctly nested allowlisted tag groups."""

    valid: set[int] = set()
    stack: list[_TagFrame] = []
    for index, token in enumerate(tokens):
        parsed = _TAG.fullmatch(token.group())
        if parsed is None:
            continue
        closing, name = parsed.groups()
        if name not in ALLOWED_TAGS:
            continue
        if name in VOID_TAGS:
            if not closing:
                valid.add(index)
            continue
        if not closing:
            stack.append(_TagFrame(name, index))
            continue
        if not stack or stack[-1].name != name:
            # Crossing or mismatched tags invalidate the open group. Clearing
            # the stack lets a later independent, valid group still preview.
            stack.clear()
            continue
        frame = stack.pop()
        group = {frame.opening_index, index, *frame.nested_indices}
        if stack:
            stack[-1].nested_indices.update(group)
        else:
            valid.update(group)
    return valid


def _recognized_mission_token(raw: str) -> bool:
    match = _MISSION.fullmatch(raw)
    if match is None or not match.group(1).strip():
        return False
    return all(part.strip() for part in match.group(1).split("|"))


def _render_token(raw: str, *, valid_tag: bool) -> str:
    tag = _TAG.fullmatch(raw)
    if tag is not None and valid_tag:
        closing, name = tag.groups()
        if name in VOID_TAGS:
            return (
                '<span class="sc-cig-none">'
                f"{html.escape(raw, quote=True)}</span>"
            )
        if name == "b":
            return "</strong>" if closing else "<strong>"
        if name == "i":
            return "</em>" if closing else "<em>"
        if closing:
            return "</span>"
        label = html.escape(f"[{name}]", quote=True)
        return (
            f'<span class="sc-cig-em">'
            f'<span class="sc-cig-em-label">{label}</span> '
        )
    if _recognized_mission_token(raw):
        return f'<span class="sc-cig-token">{html.escape(raw, quote=True)}</span>'
    return html.escape(raw, quote=True)


def _render_value(value: str) -> str:
    tokens = list(_TOKEN.finditer(value))
    valid_tags = _balanced_tag_indices(tokens)
    parts: list[str] = []
    cursor = 0
    for index, token in enumerate(tokens):
        parts.append(html.escape(value[cursor : token.start()], quote=True))
        parts.append(_render_token(token.group(), valid_tag=index in valid_tags))
        cursor = token.end()
    parts.append(html.escape(value[cursor:], quote=True))
    return "".join(parts)


def render_localization_preview(
    value: str,
    *,
    max_characters: int = MAX_PREVIEW_CHARACTERS,
) -> LocalizationPreview:
    """Render safe rich text without producing links or resource elements.

    Every source character is escaped first unless it belongs to a complete,
    balanced allowlisted tag or a structurally valid mission token. The hard
    ceiling cannot be raised by callers, keeping hostile previews bounded.
    """

    if not isinstance(value, str):
        raise TypeError("localization preview value must be text")
    if isinstance(max_characters, bool) or not isinstance(max_characters, int):
        raise TypeError("max_characters must be an integer")
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")

    limit = min(max_characters, MAX_PREVIEW_CHARACTERS)
    truncated = len(value) > limit
    visible = value[:limit]
    message = ""
    if truncated:
        message = (
            f"Rich preview truncated to {limit:,} of {len(value):,} characters. "
            "Review the complete plain-text value before saving."
        )

    rendered = _render_value(visible)
    body = f'<div class="sc-localization-preview">{rendered}</div>'
    if message:
        body += (
            '<div class="sc-preview-note" role="note">'
            f"{html.escape(message, quote=True)}</div>"
        )
    return LocalizationPreview(
        html=_STYLE + body,
        truncated=truncated,
        message=message,
        source_characters=len(value),
        rendered_characters=len(visible),
    )


__all__ = [
    "LocalizationPreview",
    "MAX_PREVIEW_CHARACTERS",
    "render_localization_preview",
]
