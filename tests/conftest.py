"""Block socket connections in tests unless explicitly marked postgres."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch, request):
    """Reject socket connections unless the test has a postgres marker."""
    if request.node.get_closest_marker("postgres"):
        return

    def blocked(*args, **kwargs):
        """Fail a socket connection attempt during an offline test."""
        raise AssertionError("Tests must not connect to live APIs or databases")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)