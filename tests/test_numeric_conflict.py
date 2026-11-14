"""Тест для проверки обобщённой логики конфликта чисел в официальных источниках (T202 / [Q01])."""
from bormochecker.evaluate import _detect_official_numeric_conflict, _verify_contradiction
from bormochecker.contract import Label
from bormochecker.sources import Doc


def test_detect_official_numeric_conflict():
    claim_text = "Количество бюджетных мест в университете составило 850"
    doc1 = Doc(
        id="doc1",
        url="https://gov.example.com/doc1",
        title="Официальный приказ",
        text="В текущем году количество бюджетных мест в университете составляет 850 мест.",
        kind="official",
    )
    doc2 = Doc(
        id="doc2",
        url="https://gov.example.com/doc2",
        title="Официальный отчёт",
        text="По данным ведомства, количество бюджетных мест в университете составляет 820 мест.",
        kind="official",
    )
    candidates = [
        {"doc": doc1, "quote": doc1.text},
        {"doc": doc2, "quote": doc2.text},
    ]

    has_conflict, reason = _detect_official_numeric_conflict(claim_text, [doc1, doc2], candidates)
    assert has_conflict is True
    assert "850" in reason and "820" in reason
    assert "В официальных документах обнаружен конфликт данных" in reason


def test_verify_contradiction_downgrades_on_official_conflict():
    res = _verify_contradiction(
        claim_text="Количество бюджетных мест в университете составило 850",
        selected_quote="Количество бюджетных мест в университете составляет 820",
        has_official_conflict=True,
        conflict_reason="В официальных документах обнаружен конфликт данных: 850 против 820.",
    )
    assert not res.is_valid
    assert res.downgraded_label == Label.clarify
    assert "850 против 820" in res.reason
