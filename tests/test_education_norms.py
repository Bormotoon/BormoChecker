"""Тесты применения образовательных норм с областью действия и датами (T208 / [Q07])."""
from datetime import datetime
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from bormochecker.derived import check_known_education_orders

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def test_norm_applied_inside_effective_period_and_scope():
    # Внутри срока действия (2026 г.) и внутри области (7 класс, обществознание)
    text = "В 7 классе школьники продолжают изучать обществознание."
    ref_dt = datetime(2026, 9, 26, 12, 0, tzinfo=MOSCOW_TZ)
    findings = check_known_education_orders(text, reference_datetime=ref_dt)

    assert len(findings) == 1
    assert findings[0]["verdict"] == "refuted"
    explanation = findings[0]["explanation"]
    assert "Приказ Минпросвещения России № 729" in explanation
    urls = re.findall(r"https?://[^\s)>\"']+", explanation)
    assert any(
        urlsplit(u).hostname == "publication.pravo.gov.ru" and "0001202404090001" in u
        for u in urls
    )


def test_norm_not_refuted_outside_effective_period():
    # Вне срока действия (май 2023 г. — до вступления приказа 729 в силу 01.09.2024)
    text = "В 7 классе школьники продолжают изучать обществознание."
    ref_dt = datetime(2023, 5, 1, 12, 0, tzinfo=MOSCOW_TZ)
    findings = check_known_education_orders(text, reference_datetime=ref_dt)

    assert len(findings) == 1
    assert findings[0]["verdict"] == "clarify"
    assert "не действовал на момент проверки" in findings[0]["explanation"]


def test_norm_not_applied_outside_scope():
    # Вне области (10 класс — норма приказа 729 касается только 6-8 классов)
    text = "В 10 классе школьники углубленно изучают обществознание."
    ref_dt = datetime(2026, 9, 26, 12, 0, tzinfo=MOSCOW_TZ)
    findings = check_known_education_orders(text, reference_datetime=ref_dt)

    assert len(findings) == 0
