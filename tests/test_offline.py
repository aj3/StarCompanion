import socket

import pytest

from starcompanion.offline import (
    OFFLINE_ENV,
    OfflineNetworkError,
    enforce_offline_from_environment,
)


def test_offline_guard_blocks_network_and_dns_but_allows_local_socket(monkeypatch):
    monkeypatch.setenv(OFFLINE_ENV, "1")
    assert enforce_offline_from_environment() is True

    with pytest.raises(OfflineNetworkError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(OfflineNetworkError):
        socket.getaddrinfo("example.com", 443)

    if hasattr(socket, "AF_UNIX"):
        local = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        local.close()


def test_offline_guard_is_inactive_without_explicit_environment(monkeypatch):
    monkeypatch.delenv(OFFLINE_ENV, raising=False)
    assert enforce_offline_from_environment() is False

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()
