"""Тест-снимок схемы ответа /api/check (Report) (T603)."""

from __future__ import annotations

import json
from pathlib import Path

from bormochecker.contract import Claim, Report

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "report_schema_snapshot.json"


def test_report_schema_matches_snapshot() -> None:
    """Схема Report (ответа /api/check) совпадает с эталонным снимком."""
    current_schema = Report.model_json_schema()

    assert SNAPSHOT_PATH.is_file(), f"Файл снимка не найден: {SNAPSHOT_PATH}"
    snapshot_schema = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert current_schema == snapshot_schema, (
        "Схема ответа Report изменилась! Если изменение осознанное, "
        "обновите файл factcheck/tests/snapshots/report_schema_snapshot.json"
    )


def test_report_schema_contains_required_fields() -> None:
    """Схема содержит все обязательные поля контракта: status, reference_datetime, claim_failures, kind."""
    schema = Report.model_json_schema()

    props = schema["properties"]
    assert "status" in props
    assert props["status"]["enum"] == ["ok", "partial", "failed"]

    assert "reference_datetime" in props
    assert "claim_failures" in props
    assert "claims" in props
    assert "costs" in props

    # Проверка схемы Claim ($defs)
    claim_schema = schema["$defs"]["Claim"]["properties"]
    assert "kind" in claim_schema
    assert "sources_unavailable" in claim_schema
    assert "start" in claim_schema
    assert "end" in claim_schema
    assert "label" in claim_schema
    assert "explanation" in claim_schema


def test_claim_model_supports_kind_and_sources_unavailable() -> None:
    """Модель Claim поддерживает инициализацию с kind и sources_unavailable."""
    c = Claim(
        start=0,
        end=4,
        text="тест",
        label="confirmed",  # type: ignore[arg-type]
        explanation="объяснение",
        kind="web_checkable",
        sources_unavailable=True,
    )
    assert c.kind == "web_checkable"
    assert c.sources_unavailable is True
