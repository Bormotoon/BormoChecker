"""Тест параллельной оценки утверждений с ограничением семафором (T503)."""

import threading
import time
from unittest.mock import patch

from bormochecker import pipeline
from bormochecker.contract import Label


def test_one_hanging_claim_does_not_block_remaining_claims() -> None:
    """Одно «зависшее» утверждение из пяти не мешает остальным четырём завершиться до дедлайна."""
    text = "Первое утверждение. Второе утверждение. Третье утверждение. Четвертое утверждение. Пятое утверждение."

    # Мокаем извлечение утверждений: ровно 5 атомов
    mock_atoms = [
        {"start": 0, "end": 19, "text": "Первое утверждение.", "kind": "factual", "importance": "high"},
        {"start": 20, "end": 39, "text": "Второе утверждение.", "kind": "factual", "importance": "high"},
        {"start": 40, "end": 60, "text": "Третье утверждение.", "kind": "factual", "importance": "high"},
        {"start": 61, "end": 83, "text": "Четвертое утверждение.", "kind": "factual", "importance": "high"},
        {"start": 84, "end": 102, "text": "Пятое утверждение.", "kind": "factual", "importance": "high"},
    ]

    def mock_eval_claim(claim_text, docs, meter, notes, **kwargs):
        if "Первое" in claim_text:
            # Зависшее утверждение: спит дольше бюджета
            time.sleep(5.0)
            return ("supported", "Первое подтверждено с опозданием", [], None)
        return ("supported", f"{claim_text} подтверждено", [], None)

    t0 = time.monotonic()
    with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms):
        with patch.object(pipeline.evaluate, "evaluate_claim", side_effect=mock_eval_claim):
            with patch.object(pipeline.Sources, "gather_for", return_value=[]):
                with patch.object(pipeline.Sources, "preload", return_value=[]):
                    report = pipeline.check(
                        text,
                        time_budget_seconds=1.2,
                        eval_concurrency=3,
                    )
    elapsed = time.monotonic() - t0

    # Проверка времени: общее время близко к дедлайну (1.2 с) и намного меньше 5 с
    assert elapsed < 2.5, f"Оценка заняла {elapsed:.2f} с, что превышает ожидаемый лимит"

    # Проверка, что остальные 4 утверждения успешно завершились до дедлайна
    confirmed_claims = [c for c in report.claims if c.label == Label.confirmed]
    assert len(confirmed_claims) == 4, f"Ожидалось 4 подтверждённых утверждения, получено {len(confirmed_claims)}"

    # Порядок утверждений сохранён
    assert len(report.claims) == 5
    for i, c in enumerate(report.claims):
        assert c.start == mock_atoms[i]["start"]


def test_eval_concurrency_bounded_by_semaphore() -> None:
    """Семафор параллелизма строго ограничивает число одновременно оцениваемых утверждений."""
    text = "Утверждение 1. Утверждение 2. Утверждение 3. Утверждение 4. Утверждение 5."

    mock_atoms = [
        {"start": 0, "end": 14, "text": "Утверждение 1.", "kind": "factual", "importance": "high"},
        {"start": 15, "end": 29, "text": "Утверждение 2.", "kind": "factual", "importance": "high"},
        {"start": 30, "end": 44, "text": "Утверждение 3.", "kind": "factual", "importance": "high"},
        {"start": 45, "end": 59, "text": "Утверждение 4.", "kind": "factual", "importance": "high"},
        {"start": 60, "end": 74, "text": "Утверждение 5.", "kind": "factual", "importance": "high"},
    ]

    active_count = 0
    max_active = 0
    lock = threading.Lock()

    def mock_eval_claim(claim_text, docs, meter, notes, **kwargs):
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        time.sleep(0.05)
        with lock:
            active_count -= 1
        return ("supported", f"{claim_text} подтверждено", [], None)

    with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms):
        with patch.object(pipeline.evaluate, "evaluate_claim", side_effect=mock_eval_claim):
            with patch.object(pipeline.Sources, "gather_for", return_value=[]):
                with patch.object(pipeline.Sources, "preload", return_value=[]):
                    report = pipeline.check(
                        text,
                        time_budget_seconds=5.0,
                        eval_concurrency=2,
                    )

    assert max_active == 2, f"Максимальный параллелизм был {max_active}, ожидалось 2"
    assert len([c for c in report.claims if c.label == Label.confirmed]) == 5
