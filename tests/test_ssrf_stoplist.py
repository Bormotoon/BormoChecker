import ipaddress
import socket

import httpx
import pytest

from bormochecker.retrieval import (
    SafeDocumentFetcher,
    UnsafeUrlError,
    assert_safe_http_url,
    is_blocked_host,
)


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гарантирует, что ни один тест стоп-листа не сделает реальный сетевой запрос."""
    def no_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Сетевой запрос строго запрещён в test_ssrf_stoplist!")

    monkeypatch.setattr(socket.socket, "connect", no_connect)


@pytest.mark.parametrize(
    "target",
    [
        "internal.example",
        "internal.example.",
        "INTERNAL.Example",
        "http://sub.internal.example/path",
        "http://blocked.example/",
        "http://пример.рф/",
        "http://xn--e1afmkfd.xn--p1ai/",
        "http://127.0.0.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://localhost/",
    ],
)
def test_is_blocked_host_catches_all_variants(target: str) -> None:
    assert is_blocked_host(target) is True


def test_env_configured_domains_and_cidrs_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTCHECK_BLOCKED_DOMAINS", "custom-portal.example,тест-блок.рф")
    monkeypatch.setenv("FACTCHECK_BLOCKED_CIDRS", "198.51.100.0/24")
    monkeypatch.setenv("FACTCHECK_BLOCKED_IPS", "203.0.113.50")

    assert is_blocked_host("https://sub.custom-portal.example/news") is True
    assert is_blocked_host("http://тест-блок.рф/") is True
    assert is_blocked_host(f"http://{'тест-блок.рф'.encode('idna').decode('ascii')}/") is True
    assert is_blocked_host("http://198.51.100.42/") is True
    assert is_blocked_host(f"http://{int(ipaddress.IPv4Address('203.0.113.50'))}/") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://2130706433/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/",
        "https://internal.example./",
        "http://sub.blocked.example/test",
        "http://пример.рф/article",
    ],
)
def test_assert_safe_http_url_rejects_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_http_url(url)


def test_fetcher_rejects_blocked_host_before_request() -> None:
    fetcher = SafeDocumentFetcher()
    with pytest.raises(UnsafeUrlError):
        fetcher.fetch("http://internal.example/page")
    with pytest.raises(UnsafeUrlError):
        fetcher.fetch("http://127.0.0.1/page")
    with pytest.raises(UnsafeUrlError):
        fetcher.fetch("http://[::ffff:127.0.0.1]/page")


def test_fetcher_rejects_redirect_to_blocked_host() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    transport = httpx.MockTransport(mock_handler)
    fetcher = SafeDocumentFetcher(transport=transport)
    with pytest.raises(UnsafeUrlError, match="Редирект на заблокированный хост"):
        fetcher.fetch("http://example.com/start")
