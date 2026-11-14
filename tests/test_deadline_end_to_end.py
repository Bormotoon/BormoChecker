import time
from unittest.mock import patch

from bormochecker import pipeline
from bormochecker.contract import CheckMode
from bormochecker.deadline import Deadline


def test_deadline_object_methods():
    """T502 / [P01]: Методы Deadline: remaining, expired, timeout(cap)."""
    t0 = time.monotonic()
    d = Deadline(2.0, start_time=t0)
    assert not d.expired()
    assert 1.9 <= d.remaining() <= 2.0
    assert d.timeout(cap=10.0) <= 2.0
    assert d.timeout(cap=0.5) == 0.5

    # Истёкший дедлайн
    d_past = Deadline(0.0, start_time=t0 - 10.0)
    assert d_past.expired()
    assert d_past.remaining() == 0.0
    assert d_past.timeout(5.0) == 0.0


def test_check_budget_with_sleeping_stubs_returns_within_limit():
    """T502 / [P01]: При бюджете 2 с и заглушках, спящих 10 с, check() возвращается не позже 3 с со статусом partial."""
    sample_text = "Концерт известной группы состоится 1 октября в Зеленограде."

    mock_atoms = [
        {
            "start": 0,
            "end": len(sample_text),
            "text": sample_text,
            "kind": "web_checkable",
            "importance": "high",
            "entity_cluster": "concert",
            "query": "концерт 1 октября зеленоград",
        },
    ]

    called = False

    def sleeping_stub(*args, **kwargs):
        nonlocal called
        called = True
        # Зависший сетевой вызов (10 с)
        time.sleep(10.0)
        return []

    t_start = time.monotonic()
    with patch.object(pipeline.extract, "extract_claims", return_value=mock_atoms), \
         patch("bormochecker.direct_apis.RuWikipediaAPI.search_and_extract", side_effect=sleeping_stub):
        report = pipeline.check(
            sample_text,
            mode=CheckMode.education_searxng,
            time_budget_seconds=2.0,
        )
    duration = time.monotonic() - t_start

    assert called is True, "Заглушка DirectAPI должна была быть вызвана"
    # Проверка: завершилось не позже 3.0 секунд (бюджет 2.0 с, накладные расходы < 1 с)
    assert duration < 3.0, f"Проверка длилась слишком долго: {duration:.2f} с >= 3.0 с"
    assert report.status == "partial", f"Ожидался статус partial, получен {report.status}"
    assert len(report.claims) > 0
    # Проверяем, что непроверенные утверждения помечены clarify и имеют пояснение о времени
    assert all(c.label.value == "clarify" for c in report.claims)
    assert any("лимит" in lim.lower() or "времени" in lim.lower() for lim in report.limitations)
