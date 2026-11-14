from fastapi.testclient import TestClient

from bormochecker.contract import Report
import bormochecker.extract
from bormochecker.web import app


def test_smoke_app(monkeypatch):
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    # Подменяем LLM: заглушка возвращает пустой список утверждений
    def dummy_extract(*args, **kwargs):
        return []

    monkeypatch.setattr(bormochecker.extract, "extract_claims", dummy_extract)

    client = TestClient(app)

    # 1. GET /
    res_root = client.get("/")
    assert res_root.status_code == 200

    # 2. GET /api/queue
    res_queue = client.get("/api/queue")
    assert res_queue.status_code == 200
    queue_data = res_queue.json()
    assert "running" in queue_data
    assert "waiting" in queue_data

    # 3. POST /api/check с коротким текстом в режиме local_frp
    payload = {
        "text": "В школах продолжается учебный год.",
        "note": "тестовая проверка",
        "mode": "local_frp",
    }
    res_check = client.post("/api/check", json=payload)
    assert res_check.status_code == 200
    data = res_check.json()

    # Валидный JSON по текущей схеме Report
    report = Report.model_validate(data)
    assert report.status in ("ok", "partial", "failed")
    assert report.text == payload["text"]
    assert report.claims == []
