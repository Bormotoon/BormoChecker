"""Тест разделения сбоев провайдеров и отсутствия результатов (T505 / [P04])."""

from unittest.mock import patch

from bormochecker import pipeline
from bormochecker.contract import Label
import bormochecker.search as nps


def test_all_providers_raise_exception_gives_partial_status_and_clear_reason() -> None:
    """Все провайдеры бросают исключение → статус partial, причина в отчёте."""
    text = "В 2026 году открыта новая школа на 1000 мест."

    mock_atoms = [
        {"start": 0, "end": len(text), "text": text, "kind": "web_checkable", "importance": "high"},
    ]

    # Мокаем извлечение утверждений: все источники падают с ошибками
    with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms):
        with patch.object(pipeline.Sources, "preload", return_value=[]):
            with patch("bormochecker.local_index.LocalKnowledgeIndex.search_docs", return_value=[]):
                with patch("bormochecker.direct_apis.DirectAPIs.fetch_candidates", side_effect=RuntimeError("Direct API failed")):
                    with patch.object(nps.FreeRuNetSearch, "search", side_effect=nps.SearchError("connection refused")):
                        report = pipeline.check(
                            text,
                            mode="education",
                            time_budget_seconds=5.0,
                        )

    # Статус отчёта должен быть partial, а не ok
    assert report.status == "partial", f"Ожидался статус partial при сбое всех провайдеров, получен {report.status}"

    # Утверждение не должно быть Label.error (сбой не превращается в ошибку)
    assert len(report.claims) == 1
    claim = report.claims[0]
    assert claim.label == Label.clarify, f"Ожидался Label.clarify, получен {claim.label}"

    # Причина «источники недоступны» должна присутствовать в объяснении или ограничениях
    combined_reasons = (claim.explanation + " " + " ".join(report.limitations)).casefold()
    assert "источники недоступны" in combined_reasons, (
        f"Ожидалось указание 'источники недоступны' в отчёте, получено: {combined_reasons}"
    )


def test_empty_search_results_gives_unresolved_not_sources_unavailable() -> None:
    """Когда провайдер успешно ответил, но ничего не нашёл — это обычное 'в источниках нет фрагмента'."""
    text = "Некое утверждение о редком событии."

    mock_atoms = [
        {"start": 0, "end": len(text), "text": text, "kind": "web_checkable", "importance": "high"},
    ]

    with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms):
        with patch.object(pipeline.Sources, "preload", return_value=[]):
            # Поиск успешен, но пуст (empty)
            with patch.object(nps.FreeRuNetSearch, "search", return_value=[]):
                report = pipeline.check(
                    text,
                    mode="education",
                    time_budget_seconds=5.0,
                )

    # При чистом "ничего не найдено" без сбоев провайдеров статус ok и нет пометки о недоступности
    assert report.status == "ok"
    claim = report.claims[0]
    assert claim.label == Label.clarify
    assert "источники недоступны" not in claim.explanation.casefold()


def test_search_outcome_types() -> None:
    """FreeRuNetSearch возвращает правильный тип исхода (ok, empty, provider_error)."""
    from unittest.mock import MagicMock
    mock_cache = MagicMock()
    mock_cache.get_search.return_value = None
    searcher = nps.FreeRuNetSearch(cache=mock_cache, allow_paid_fallback=False)
    searcher.searxng_url = ""

    # 1. Сбой провайдера DDGS -> provider_error
    with patch.object(searcher.ddgs, "search", side_effect=RuntimeError("DDGS failure")):
        with patch.object(searcher.cloud_rotator, "search", side_effect=RuntimeError("Cloud failure")):
            hits, outcome = searcher.search_with_outcome("тестовый запрос")
            assert hits == []
            assert outcome == "provider_error"

    # 2. Успешный, но пустой ответ -> empty
    with patch.object(searcher.ddgs, "search", return_value=[]):
        with patch.object(searcher.cloud_rotator, "search", return_value=("none", [])):
            hits, outcome = searcher.search_with_outcome("тестовый запрос")
            assert hits == []
            assert outcome == "empty"

    # 3. Успешный ответ с результатами -> ok
    dummy_res = [nps.SearchResult(title="Тест", url="https://example.com/test", snippet="описание")]
    with patch.object(searcher.ddgs, "search", return_value=dummy_res):
        hits, outcome = searcher.search_with_outcome("тестовый запрос")
        assert len(hits) == 1
        assert outcome == "ok"
