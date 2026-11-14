from __future__ import annotations

from bormochecker.extract import _span, absorb_redundant_subspans
from bormochecker.spans import find_span


def test_find_span_bridges_parenthetical_and_url():
    text = (
        "Выставка откроется 5 мая (это вторник) в 18:00 "
        "в Доме культуры (Центральная улица, дом 10 (https://example.org/dk))."
    )
    quote = "Выставка откроется 5 мая в 18:00"
    span = _span(text, quote)
    assert span is not None
    assert text[span[0]:span[1]] == "Выставка откроется 5 мая (это вторник) в 18:00"
    assert find_span(text, quote) == span


def test_absorb_redundant_subspans_filters_headings_and_merges_nested():
    text = "Подробнее👇\n\nЗанятия проходят по субботам в главном корпусе университета."
    claims = [
        {"quote": "Подробнее👇", "text": "Подробнее👇", "start": 0, "end": 11, "span": (0, 11), "kind": "web_checkable"},
        {
            "quote": "Занятия проходят по субботам в главном корпусе университета.",
            "text": "Занятия проходят по субботам в главном корпусе университета.",
            "start": 13,
            "end": len(text),
            "span": (13, len(text)),
            "kind": "web_checkable",
        },
        {
            "quote": "по субботам",
            "text": "по субботам",
            "start": 30,
            "end": 41,
            "span": (30, 41),
            "kind": "web_checkable",
        },
    ]
    res = absorb_redundant_subspans(claims, text)
    assert len(res) == 1
    assert res[0]["start"] == 13
