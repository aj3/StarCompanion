"""Auditable opt-in guard proving core workflows require no network access."""

from __future__ import annotations

import os
import socket
import sys

OFFLINE_ENV = "STARCOMPANION_ENFORCE_OFFLINE"
_installed = False


class OfflineNetworkError(RuntimeError):
    pass


def enforce_offline_from_environment() -> bool:
    """Install an audit hook once; enforcement follows the environment flag."""
    global _installed
    if not _installed:
        sys.addaudithook(_audit_network)
        _installed = True
    return os.environ.get(OFFLINE_ENV) == "1"


def _audit_network(event: str, args: tuple[object, ...]) -> None:
    if os.environ.get(OFFLINE_ENV) != "1":
        return
    if event == "socket.__new__":
        family = args[1] if len(args) > 1 else None
        if family in (socket.AF_INET, socket.AF_INET6):
            raise OfflineNetworkError("network sockets are disabled by offline verification")
    elif event.startswith("socket.getaddr") or event.startswith("socket.gethost"):
        raise OfflineNetworkError("DNS/network lookup is disabled by offline verification")


__all__ = ["OFFLINE_ENV", "OfflineNetworkError", "enforce_offline_from_environment"]
