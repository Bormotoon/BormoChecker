"""Тесты единого момента проверки reference_datetime (T207 / [Q06])."""
from datetime import datetime
from zoneinfo import ZoneInfo
from bormochecker.derived import check_weekday_dates
from bormochecker.evaluate import _extract_temporal_markers
from bormochecker.pipeline import _summary, check

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def test_temporal_markers_differ_by_reference_datetime():
    text = "В прошлом году отремонтировали 300 школ"
    markers_2026 = _extract_temporal_markers(text, reference_year=2026)
    markers_2028 = _extract_temporal_markers(text, reference_year=2028)

    assert markers_2026["target_year"] == 2025
    assert markers_2028["target_year"] == 2027
    assert markers_2026["target_year"] != markers_2028["target_year"]


def test_weekday_check_differs_by_reference_datetime():
    # 1 октября: в 2026 году это четверг (верно), а в 2025 году это среда (ошибка)
    phrase = "Торжественное открытие состоится 1 октября (это четверг)"

    dt_2026 = datetime(2026, 9, 1, 12, 0, tzinfo=MOSCOW_TZ)
    dt_2025 = datetime(2025, 9, 1, 12, 0, tzinfo=MOSCOW_TZ)

    findings_2026 = check_weekday_dates(phrase, reference_datetime=dt_2026)
    findings_2025 = check_weekday_dates(phrase, reference_datetime=dt_2025)

    assert len(findings_2026) == 1
    assert findings_2026[0]["verdict"] == "clarify"
    assert len(findings_2025) == 1
    assert findings_2025[0]["verdict"] == "refuted"
    assert "среда" in findings_2025[0]["explanation"]


def test_reference_datetime_recorded_in_report():
    dt = datetime(2026, 5, 20, 15, 30, tzinfo=MOSCOW_TZ)
    summary = _summary([], reference_datetime=dt)
    assert "20.05.2026" in summary

    report = check("", reference_datetime=dt)
    assert report.reference_datetime == dt
