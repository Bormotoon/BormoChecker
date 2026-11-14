import socket
import pytest


@pytest.fixture(autouse=True)
def _guard_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        return

    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        # Allow unix domain sockets
        if getattr(self, "family", None) == getattr(socket, "AF_UNIX", -1):
            return real_connect(self, address)
        if isinstance(address, (str, bytes)):
            return real_connect(self, address)
        if isinstance(address, tuple) and len(address) >= 2:
            host = address[0]
            if host == "127.0.0.1":
                return real_connect(self, address)
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
