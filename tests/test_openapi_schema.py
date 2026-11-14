"""Тест схемы OpenAPI и сверка со снимком docs/openapi.json (T909)."""

from __future__ import annotations

import json

from bormochecker.paths import repo_root
from bormochecker.web import app

SNAPSHOT_PATH = repo_root() / "docs" / "openapi.json"


def test_openapi_schema_matches_snapshot() -> None:
    """Схема OpenAPI приложения совпадает с эталонным снимком docs/openapi.json."""
    assert SNAPSHOT_PATH.is_file(), f"Файл снимка не найден: {SNAPSHOT_PATH}"

    current_schema = app.openapi()
    snapshot_schema = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert current_schema == snapshot_schema, (
        "Схема OpenAPI изменилась! Если изменение осознанное, обновите снимок:\n"
        ".venv/bin/python -c 'from bormochecker.web import app; import json, pathlib; "
        "pathlib.Path(\"docs/openapi.json\").write_text("
        "json.dumps(app.openapi(), indent=2, ensure_ascii=False) + \"\\n\", encoding=\"utf-8\")'"
    )


def test_openapi_schema_contains_required_error_codes() -> None:
    """Эндпоинт /api/check документирует коды ошибок 403, 413, 429, 503."""
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/check" in paths, "Маршрут /api/check отсутствует в схеме OpenAPI"

    check_post = paths["/api/check"]["post"]
    responses = check_post.get("responses", {})

    required_codes = ["200", "403", "413", "429", "503"]
    for code in required_codes:
        assert code in responses, f"Код {code} отсутствует в описании ответов /api/check"

    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "QueueStatusResponse" in schema["components"]["schemas"]
    assert "HealthResponse" in schema["components"]["schemas"]


def test_openapi_endpoints_and_metadata() -> None:
    """Проверка наличия ключевых эндпоинтов и метаданных OpenAPI."""
    schema = app.openapi()
    assert schema["info"]["title"] == "BormoChecker API"
    assert schema["info"]["version"] == "1.0.0"

    paths = schema["paths"]
    assert "/healthz" in paths
    assert "/readyz" in paths
    assert "/api/queue" in paths
    assert "/api/check" in paths
