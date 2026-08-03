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

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.code}: {self.message}"


def validate_value(value: str) -> list[Issue]:
    """Check one rendered value. ERROR breaks the game; WARNING looks wrong."""
    issues: list[Issue] = []

    if "\n" in value or "\r" in value:
        issues.append(
            Issue(
                Severity.ERROR,
                "real-newline",
                r"contains a real newline; use the literal two characters \n",
            )
        )

    issues.extend(_check_tags(value))
    issues.extend(_check_mission_tokens(value))
    return issues


def _check_tags(value: str) -> list[Issue]:
    issues: list[Issue] = []
    stack: list[str] = []

    for closing, name in _TAG.findall(value):
        if name not in ALLOWED_TAGS:
            issues.append(
                Issue(Severity.ERROR, "unknown-tag", f"<{name}> is not a supported tag")
            )
            continue
        if name in VOID_TAGS:
            continue
        if not closing:
            stack.append(name)
        elif not stack:
            issues.append(
                Issue(Severity.WARNING, "unbalanced-tag", f"</{name}> has no opening tag")
            )
        elif stack[-1] != name:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "mismatched-tag",
                    f"<{stack.pop()}> closed by </{name}>",
                )
            )
        else:
            stack.pop()

    issues.extend(
        Issue(Severity.WARNING, "unbalanced-tag", f"<{name}> is never closed")
        for name in stack
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
