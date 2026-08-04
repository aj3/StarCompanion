"""Pre-write validation of rendered localization values.

The failure mode this exists to prevent is silent: a malformed value makes the
contract render blank in-game rather than raising anything, so problems must be
caught before the file is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# The only markup the localization renderer understands. Verified by scanning
# every tag occurrence in a full global.ini -- there is no hex/RGB colour tag.
ALLOWED_TAGS = frozenset({"EM", "EM1", "EM2", "EM3", "EM4", "b", "i", "None"})

# Tags that carry no closing partner and so are exempt from balance checks.
VOID_TAGS = frozenset({"None"})

# Tags usable to wrap text, i.e. valid choices for configurable emphasis.
EMPHASIS_TAGS = ALLOWED_TAGS - VOID_TAGS

_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)>")
_ANGLE_TAG = re.compile(r"<[^<>]*>|<[^<>]*$")
_TAG_NAME = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")
_MISSION_TOKEN = re.compile(r"~mission\(([^)]*)\)")
_UNCLOSED_MISSION = re.compile(r"~mission\((?:[^)]*)$")


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    offset: int | None = None

    def __str__(self) -> str:
        location = f" at character {self.offset}" if self.offset is not None else ""
        return f"[{self.severity.value}] {self.code}{location}: {self.message}"


def validate_value(value: str, *, trusted_source: str = "") -> list[Issue]:
    """Check one rendered value.

    CIG occasionally ships placeholder tags outside the documented emphasis
    set. A renderer may preserve names already present in its pristine source,
    but it may not introduce a new unsupported name.
    """
    issues: list[Issue] = []

    if "\n" in value or "\r" in value:
        issues.append(
            Issue(
                Severity.ERROR,
                "real-newline",
                r"contains a real newline; use the literal two characters \n",
            )
        )

    trusted_tags = {match.group(2) for match in _TAG.finditer(trusted_source)}
    issues.extend(_check_tags(value, trusted_tags))
    issues.extend(_check_mission_tokens(value))
    return issues


def _check_tags(value: str, trusted_tags: set[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    stack: list[tuple[str, int]] = []

    for candidate in _ANGLE_TAG.finditer(value):
        raw = candidate.group()
        name_match = _TAG_NAME.match(raw)
        if name_match is None:
            continue  # ordinary angle-bracket text, not tag-like markup

        name = name_match.group(1)
        parsed = _TAG.fullmatch(raw)
        if name not in ALLOWED_TAGS and name in (trusted_tags or set()):
            # CIG substitution placeholders such as <years> and <PH> do not
            # follow emphasis-tag balance rules. Preserve complete source tags.
            if parsed is None:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        "malformed-tag",
                        f"{raw} is not the complete trusted tag <{name}> or </{name}>",
                        candidate.start(),
                    )
                )
            continue
        if name not in ALLOWED_TAGS:
            issues.append(
                Issue(
                    Severity.ERROR,
                    "unknown-tag",
                    f"{raw} uses unsupported tag {name!r}",
                    candidate.start(),
                )
            )
            continue
        if parsed is None:
            issues.append(
                Issue(
                    Severity.ERROR,
                    "malformed-tag",
                    f"{raw} is not a complete <{name}> or </{name}> tag",
                    candidate.start(),
                )
            )
            continue

        closing = parsed.group(1)
        if name in VOID_TAGS:
            if closing:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        "invalid-void-close",
                        f"</{name}> closes a tag that has no closing partner",
                        candidate.start(),
                    )
                )
            continue
        if not closing:
            stack.append((name, candidate.start()))
        elif not stack:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "unbalanced-tag",
                    f"</{name}> has no opening tag",
                    candidate.start(),
                )
            )
        elif stack[-1][0] != name:
            opened, _offset = stack.pop()
            issues.append(
                Issue(
                    Severity.WARNING,
                    "mismatched-tag",
                    f"<{opened}> closed by </{name}>",
                    candidate.start(),
                )
            )
        else:
            stack.pop()

    issues.extend(
        Issue(
            Severity.WARNING,
            "unbalanced-tag",
            f"<{name}> is never closed",
            offset,
        )
        for name, offset in stack
    )
    return issues


def _check_mission_tokens(value: str) -> list[Issue]:
    issues: list[Issue] = []

    if _UNCLOSED_MISSION.search(value):
        issues.append(
            Issue(Severity.ERROR, "unclosed-token", "~mission( is missing its closing paren")
        )

    for body in _MISSION_TOKEN.findall(value):
        if not body.strip():
            issues.append(Issue(Severity.ERROR, "empty-token", "~mission() has no token name"))
        elif any(part.strip() == "" for part in body.split("|")):
            issues.append(
                Issue(
                    Severity.ERROR,
                    "malformed-token",
                    f"~mission({body}) has an empty segment",
                )
            )
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
