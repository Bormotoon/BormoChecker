import socket
import pytest


def test_network_disabled_without_marker():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="network disabled in tests"):
        s.connect(("8.8.8.8", 53))


def test_localhost_allowed_without_marker():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Connecting to closed localhost port will raise ConnectionRefusedError or OSError, not RuntimeError
    with pytest.raises(OSError) as excinfo:
        s.connect(("127.0.0.1", 65432))
    assert not isinstance(excinfo.value, RuntimeError)
