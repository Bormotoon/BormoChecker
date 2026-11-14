import datetime
from zoneinfo import ZoneInfo
from bormochecker.contract import Label
from bormochecker.derived import audit_derived_claims, check_weekday_dates

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def test_weekday_dates_confirmed_with_explicit_year():
    # 1 октября 2026 года — четверг
    text = "Конференция откроется 1 октября 2026 года (это четверг)."
    findings = check_weekday_dates(text)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "confirmed"
    assert "четверг" in findings[0]["explanation"]

    claims = [{"text": text, "start": 0, "end": len(text), "label": Label.clarify}]
    audit_derived_claims(text, claims)
    assert claims[0]["label"] == Label.confirmed
    assert claims[0].get("derived_verified") is True


def test_weekday_dates_refuted_with_explicit_year():
    # 1 октября 2026 года — четверг, а не пятница
    text = "Конференция откроется 1 октября 2026 года (это пятница)."
    findings = check_weekday_dates(text)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "refuted"
    assert "четверг" in findings[0]["explanation"]

    f_start, f_end = findings[0]["span"]
    quote = text[f_start:f_end]
    claims = [{"text": quote, "start": f_start, "end": f_end, "label": Label.clarify}]
    audit_derived_claims(text, claims)
    assert claims[0]["label"] == Label.error
    assert claims[0].get("derived_verified") is False


def test_weekday_dates_clarify_without_year():
    # Без указания года берется reference_datetime -> clarify
    text = "В четверг, 1 октября, начнётся регистрация."
    ref_dt = datetime.datetime(2026, 9, 15, tzinfo=MOSCOW_TZ)
    findings = check_weekday_dates(text, reference_datetime=ref_dt)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "clarify"

    claims = [{"text": text, "start": 0, "end": len(text), "label": Label.clarify}]
    audit_derived_claims(text, claims, reference_datetime=ref_dt)
    assert claims[0]["label"] == Label.clarify
    assert claims[0].get("calendar_weekday_verified") is True
