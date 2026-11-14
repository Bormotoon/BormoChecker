import pytest
from fastapi.testclient import TestClient

from bormochecker.web import app, reset_rate_limiter, validate_content_type_by_bytes


@pytest.fixture(autouse=True)
def clean_rate_limits() -> None:
    reset_rate_limiter()


def test_payload_too_large_gives_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """Слишком большое тело запроса даёт 413 Payload Too Large."""
    # Устанавливаем лимит 500 байт для быстрого теста
    monkeypatch.setenv("FACTCHECK_MAX_BODY_BYTES", "500")
    client = TestClient(app)

    # Запрос с телом больше 500 байт
    large_payload = {"text": "A" * 600, "mode": "local_frp"}
    res = client.post("/api/check", json=large_payload)
    assert res.status_code == 413
    assert "413 Payload Too Large" in res.json()["detail"]


def test_rate_limit_gives_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """Превышение частоты запросов даёт 429 Too Many Requests."""
    monkeypatch.setenv("FACTCHECK_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("FACTCHECK_RATE_LIMIT_WINDOW", "60")
    client = TestClient(app)

    # Первые 3 запроса успешны (например к /api/queue)
    for _ in range(3):
        r = client.get("/api/queue")
        assert r.status_code in (200, 401)

    # 4-й запрос превышает лимит
    r4 = client.get("/api/queue")
    assert r4.status_code == 429
    assert "429 Too Many Requests" in r4.json()["detail"]


def test_internal_error_500_does_not_contain_stack_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ 500 не содержит трассировки стека и внутренних деталей ошибки."""
    @app.get("/api/test-crash-unhandled", include_in_schema=False)
    def crash_route():  # type: ignore[no-untyped-def]
        raise RuntimeError("Секретный пароль базы данных: pass12345, стек вызовов: /internal/secret/path.py")

    try:
        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/api/test-crash-unhandled")
        assert res.status_code == 500
        assert res.json() == {"detail": "Внутренняя ошибка сервера."}
        assert "pass12345" not in res.text
        assert "Traceback" not in res.text
        assert "traceback" not in res.text
    finally:
        app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/api/test-crash-unhandled"]
        app.openapi_schema = None


def test_security_headers_present() -> None:
    """Заголовки безопасности X-Content-Type-Options, Referrer-Policy, Content-Security-Policy присутствуют."""
    client = TestClient(app)
    res = client.get("/api/queue")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_validate_content_type_by_bytes() -> None:
    """Проверка типа файла по содержимому (магическим байтам)."""
    assert "wordprocessingml" in validate_content_type_by_bytes(b"PK\x03\x04\x14\x00...")
    assert validate_content_type_by_bytes(b"%PDF-1.4...") == "application/pdf"
    assert validate_content_type_by_bytes(b"<!DOCTYPE html><html>...") == "text/html"
    assert validate_content_type_by_bytes(b"Plain text note...") == "text/plain"
