import time
import pytest
from fastapi.testclient import TestClient

from bormochecker.crypto import sign
from bormochecker.contract import Report
import bormochecker.extract
from bormochecker.web import app


@pytest.fixture(autouse=True)
def mock_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяем LLM extract_claims, чтобы не требовать реальную модель."""
    def dummy_extract(*args, **kwargs):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(bormochecker.extract, "extract_claims", dummy_extract)


def test_guest_journalism_full_gets_403_when_auth_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гость с journalism_full получает 403 при FACTCHECK_REQUIRE_AUTH=0."""
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    client = TestClient(app)
    res = client.post(
        "/api/check",
        json={"text": "Новость о конкурсе.", "mode": "journalism_full"},
    )
    assert res.status_code == 403
    assert "недоступен в гостевом доступе" in res.json()["detail"]


def test_guest_journalism_full_gets_403_when_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гость с journalism_full получает 403 при FACTCHECK_REQUIRE_AUTH=1."""
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "1")
    client = TestClient(app)
    res = client.post(
        "/api/check",
        json={"text": "Новость о конкурсе.", "mode": "journalism_full"},
    )
    assert res.status_code == 403
    assert "недоступен в гостевом доступе" in res.json()["detail"]


def test_pro_user_journalism_full_gets_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRO-пользователь получает 200 при вызове journalism_full."""
    secret = "secret_pro_key_test"
    monkeypatch.setenv("PASSPORT_HMAC_SECRET", secret)
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "1")

    # Подписываем куку bc_session с активной PRO подпиской
    token = sign(secret.encode(), {"pro_until": time.time() + 7200, "role": "teacher"})

    client = TestClient(app)
    client.cookies.set("bc_session", token)

    res = client.post(
        "/api/check",
        json={"text": "Текст новости о школе.", "mode": "journalism_full"},
    )
    assert res.status_code == 200
    report = Report.model_validate(res.json())
    assert report.status in ("ok", "partial", "failed")


def test_guest_allowed_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гостю доступны режимы из FACTCHECK_GUEST_MODES (по умолчанию local_frp, education)."""
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    client = TestClient(app)

    res_ed = client.post(
        "/api/check",
        json={"text": "Текст новости.", "mode": "education"},
    )
    assert res_ed.status_code == 200

    res_frp = client.post(
        "/api/check",
        json={"text": "Текст новости.", "mode": "local_frp"},
    )
    assert res_frp.status_code == 200


def test_guest_education_requires_auth_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """При FACTCHECK_REQUIRE_AUTH=1 гость на разрешённом режиме получает 401."""
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "1")
    client = TestClient(app)

    res = client.post(
        "/api/check",
        json={"text": "Текст новости.", "mode": "education"},
    )
    assert res.status_code == 401
