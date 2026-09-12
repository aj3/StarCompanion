"""Evidence-backed route and mining tags from stock mission tokens."""

from __future__ import annotations

import re

from .model import Contract, Evidence, StringKind

_TOKEN = re.compile(r"~mission\(\s*([A-Za-z][A-Za-z0-9_]*)\s*(\|[^)]*)?\)")
_ROUTE_FAMILIES = ("haulcargo", "delivery", "courier")
_RESOURCE_FAMILIES = ("rpt_scan", "rpt_scanmine")


def _role(name: str) -> str | None:
    lowered = name.casefold()
    if lowered.startswith(("location", "pickup")):
        return "from"
    if lowered.startswith(("destination", "dropoff")):
        return "to"
    return None


def _token(name: str, original: str, detail: str) -> str:
    if re.fullmatch(r"(?i)(location|destination)\d*", name):
        return f"~mission({name}|{'name' if detail == 'name' else 'Address'})"
    return original


def _agreed(groups: list[dict[str, str]]) -> dict[str, str]:
    if not groups:
        return {}
    common = set(groups[0])
    for group in groups[1:]:
        common &= set(group)
    return {name: token for name, token in groups[0].items() if name in common}


def route_fragment(contract: Contract, *, arrow: str = ">", detail: str = "address") -> str:
    """Return only endpoint tokens shared by every contributing description."""

    if arrow not in {">", "->", "to"} or detail not in {"address", "name"}:
        raise ValueError("unsupported route presentation")
    if not any(any(part in key.casefold() for part in _ROUTE_FAMILIES) for key in contract.keys_of(StringKind.TITLE)):
        return ""
    if any(_role(match.group(1)) for key in contract.keys_of(StringKind.TITLE) for match in _TOKEN.finditer(contract.base_text(key) or "")):
        return ""
    from_groups: list[dict[str, str]] = []
    to_groups: list[dict[str, str]] = []
    for key in contract.keys_of(StringKind.DESC):
        source: dict[str, dict[str, str]] = {"from": {}, "to": {}}
        for match in _TOKEN.finditer(contract.base_text(key) or ""):
            role = _role(match.group(1))
            if role:
                source[role].setdefault(match.group(1), match.group(0))
        if source["from"]:
            from_groups.append(source["from"])
        if source["to"]:
            to_groups.append(source["to"])
    origins, destinations = _agreed(from_groups), _agreed(to_groups)
    left = ", ".join(_token(name, value, detail) for name, value in origins.items())
    right = ", ".join(_token(name, value, detail) for name, value in destinations.items())
    if left and right:
        return f"{left} {arrow} {right}"
    return f"from {left}" if left else (f"to {right}" if right else "")


def resource_signature(contract: Contract) -> str:
    """Return exact mining tokens for reviewed Battaglia scan families."""

    if not any(any(part in key.casefold() for part in _RESOURCE_FAMILIES) for key in contract.all_keys()):
        return ""
    found: dict[str, str] = {}
    for key in contract.keys_of(StringKind.DESC):
        for match in _TOKEN.finditer(contract.base_text(key) or ""):
            if match.group(1).casefold() in {"resources", "mineabletype"}:
                found.setdefault(match.group(1).casefold(), match.group(0))
    return " ".join(found[name] for name in ("resources", "mineabletype") if name in found)


def token_evidence(
    contract: Contract,
    *,
    route_text: str = "",
    resource_text: str = "",
) -> tuple[Evidence, ...]:
    """Return evidence only for tokens that actually reached rendered text."""

    provider = "local-stock-mission-tokens"
    rendered_route = {
        match.group(1).casefold() for match in _TOKEN.finditer(route_text)
    }
    rendered_resources = {
        match.group(1).casefold() for match in _TOKEN.finditer(resource_text)
    }
    evidence = []
    for key in contract.keys_of(StringKind.DESC):
        text = contract.base_text(key) or ""
        for match in _TOKEN.finditer(text):
            name = match.group(1)
            lowered = name.casefold()
            if (lowered in rendered_route and _role(name)) or (
                lowered in rendered_resources
                and lowered in {"resources", "mineabletype"}
            ):
                evidence.append(
                    Evidence(
                        provider,
                        contract.id,
                        f"localization:{key}",
                        f"token:{name}",
                        match.group(0),
                    )
                )
    return tuple(dict.fromkeys(evidence))
