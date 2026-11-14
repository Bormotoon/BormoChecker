"""Тест /healthz, /readyz и структурных журналов (T507)."""

import json
from unittest.mock import patch
from fastapi.testclient import TestClient

from bormochecker import llm, pipeline, web


def test_healthz_returns_200() -> None:
    """GET /healthz возвращает 200 (процесс жив)."""
    client = TestClient(web.app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json().get("status") == "ok"


def test_readyz_returns_200_when_model_and_index_ready() -> None:
    """GET /readyz возвращает 200, когда модель отвечает и индекс доступен."""
    client = TestClient(web.app)
    with patch.object(llm, "check_health", return_value=True):
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json().get("status") == "ready"


def test_readyz_returns_503_when_model_unavailable() -> None:
    """GET /readyz возвращает 503 при недоступной модели."""
    client = TestClient(web.app)
    with patch.object(llm, "check_health", return_value=False):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json().get("status") == "unavailable"


def test_structured_json_logging_contains_metrics_and_no_user_text(caplog) -> None:
    """Структурный журнал содержит метрики этапов, провайдеров и меток, и не содержит текст пользователя."""
    secret_text = "Секретный текст пользователя, который запрещено писать в логи"
    text = f"{secret_text}. Второе предложение для проверки."

    mock_atoms = [
        {"start": 0, "end": len(secret_text), "text": secret_text, "kind": "factual", "importance": "high"},
    ]

    with caplog.at_level("INFO"):
        with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms):
            with patch.object(pipeline.Sources, "preload", return_value=[]):
                with patch.object(pipeline.evaluate, "evaluate_claim", return_value=("supported", "ok", [], None)):
                    report = pipeline.check(text, mode="local_frp")

    assert report.status in ("ok", "partial")

    # Ищем структурированную запись метрик в логах
    metrics_records = [
        r for r in caplog.records
        if "FACTCHECK_METRICS" in r.message or "factcheck_check" in r.message
    ]
    assert len(metrics_records) >= 1, "Ожидалась структурная запись метрик проверки"

    log_text = metrics_records[0].message
    assert secret_text not in log_text, "Текст пользователя не должен попадать в структурный журнал"

    # Парсим JSON метрик
    json_str = log_text.split("FACTCHECK_METRICS", 1)[-1].strip()
    data = json.loads(json_str)

    assert "request_id" in data
    assert "mode" in data
    assert "outcome" in data
    assert "stage_durations_seconds" in data
    assert "extract" in data["stage_durations_seconds"]
    assert "evaluate" in data["stage_durations_seconds"]
    assert "claims_by_label" in data
    assert "requests_by_provider" in data
