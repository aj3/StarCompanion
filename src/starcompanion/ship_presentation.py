"""Safe user-authored prefixes for ship favorites and ASOP ordering."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .user_edits import MAX_VALUE_LENGTH, UserEditError

DEFAULT_FAVORITE_PREFIX = "*"
_ORDER = re.compile(r"([0-9]{2})-")


@dataclass(frozen=True)
class ShipPrefix:
    base_text: str
    favorite: bool = False
    order: int | None = None


def _validate_prefix(prefix: str) -> None:
    if (
        len(prefix) != 1
        or not prefix.isprintable()
        or prefix.isspace()
        or prefix.isalnum()
        or prefix in "-=\r\n"
    ):
        raise UserEditError("favorite prefix must be one printable symbol")


def parse_ship_prefix(
    value: str,
    *,
    prefix: str = DEFAULT_FAVORITE_PREFIX,
) -> ShipPrefix:
    """Parse only self-identifying prefixes; numeric ship names stay intact."""

    _validate_prefix(prefix)
    favorite = value.startswith(prefix)
    body = value[1:] if favorite else value
    match = _ORDER.match(body)
    order = int(match.group(1)) if match and match.group(1) != "00" else None
    if order is not None:
        body = body[match.end() :]
    if not body:
        raise UserEditError("ship display name must not be empty")
    return ShipPrefix(body, favorite, order)


def _current(
    source: str,
    override: str | None,
    prefix: str,
) -> ShipPrefix:
    if not source or len(source) > MAX_VALUE_LENGTH:
        raise UserEditError("ship source value is invalid")
    if override is None or override == source:
        return ShipPrefix(source)
    if len(override) > MAX_VALUE_LENGTH:
        raise UserEditError("ship override value is too large")
    return parse_ship_prefix(override, prefix=prefix)


def _render(
    source: str,
    state: ShipPrefix,
    prefix: str,
) -> str | None:
    value = (
        (prefix if state.favorite else "")
        + (f"{state.order:02d}-" if state.order is not None else "")
        + state.base_text
    )
    if len(value) > MAX_VALUE_LENGTH:
        raise UserEditError("ship override value is too large")
    return None if value == source else value


def set_ship_favorite(
    source: str,
    override: str | None,
    enabled: bool,
    *,
    prefix: str = DEFAULT_FAVORITE_PREFIX,
) -> str | None:
    """Set favorite state while preserving a user name and ASOP order."""

    _validate_prefix(prefix)
    state = _current(source, override, prefix)
    return _render(source, ShipPrefix(state.base_text, enabled, state.order), prefix)


def set_ship_order(
    source: str,
    override: str | None,
    order: int | None,
    *,
    prefix: str = DEFAULT_FAVORITE_PREFIX,
) -> str | None:
    """Set or clear a 1–99 ASOP order while preserving favorite state."""

    _validate_prefix(prefix)
    if order is not None and (isinstance(order, bool) or not 1 <= order <= 99):
        raise UserEditError("ASOP order must be between 1 and 99")
    state = _current(source, override, prefix)
    return _render(source, ShipPrefix(state.base_text, state.favorite, order), prefix)


__all__ = [
    "DEFAULT_FAVORITE_PREFIX",
    "ShipPrefix",
    "parse_ship_prefix",
    "set_ship_favorite",
    "set_ship_order",
]
