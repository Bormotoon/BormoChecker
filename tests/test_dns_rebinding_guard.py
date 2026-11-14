import socket
import pytest
import httpx
from bormochecker.retrieval import (
    FetchError,
    SafeDocumentFetcher,
    SafePinnedBackend,
    UnsafeUrlError,
)


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гарантирует, что ни один тест не сделает реальный сетевой запрос."""
    def no_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Сетевой запрос строго запрещён в test_dns_rebinding_guard!")

    monkeypatch.setattr(socket.socket, "connect", no_connect)


def test_dns_rebinding_pins_verified_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тест: резолвер-заглушка сначала отдаёт внешний адрес, затем 127.0.0.1: соединение идёт только на первый проверенный адрес."""
    call_count = 0

    def mock_resolver(host: str, port: int, family: int = 0, type: int = 0):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    backend = SafePinnedBackend(dns_resolver=mock_resolver)

    # Перехватываем create_connection, чтобы проверить адрес и вернуть фиктивный сокет
    connected_target = None

    def mock_create_connection(address, timeout=None, source_address=None):  # type: ignore[no-untyped-def]
        nonlocal connected_target
        connected_target = address
        pair = socket.socketpair()
        pair[0].close()
        return pair[1]

    monkeypatch.setattr(socket, "create_connection", mock_create_connection)

    backend.connect_tcp("rebind.example.com", 80)
    assert connected_target == ("93.184.216.34", 80)
    # Повторный вызов резолвера (если бы он случился после проверки) отдал бы 127.0.0.1,
    # но соединение произошло строго с первым проверенным адресом
    assert call_count == 1


def test_fetcher_rejects_redirect_to_blocked_host() -> None:
    """Редирект на заблокированный хост отклоняется с UnsafeUrlError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/target"})
        return httpx.Response(200, text="OK")

    transport = httpx.MockTransport(mock_handler)
    fetcher = SafeDocumentFetcher(transport=transport)
    with pytest.raises(UnsafeUrlError, match="Редирект на заблокированный хост"):
        fetcher.fetch("http://example.com/start")


def test_fetcher_rejects_unsupported_schemes() -> None:
    """Разрешены только схемы http и https."""
    fetcher = SafeDocumentFetcher()
    with pytest.raises(UnsafeUrlError, match="неподдерживаемая схема"):
        fetcher.fetch("ftp://example.com/file")
    with pytest.raises(UnsafeUrlError, match="неподдерживаемая схема"):
        fetcher.fetch("file:///etc/passwd")


def test_fetcher_redirect_limit_max_5() -> None:
    """Не более 5 редиректов."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        count = int(request.url.path.removeprefix("/step"))
        return httpx.Response(302, headers={"Location": f"http://example.com/step{count + 1}"})

    transport = httpx.MockTransport(mock_handler)
    fetcher = SafeDocumentFetcher(transport=transport, max_redirects=5)
    with pytest.raises(FetchError, match="слишком много редиректов"):
        fetcher.fetch("http://example.com/step1")
