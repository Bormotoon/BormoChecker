from __future__ import annotations

from bormochecker.extract import _parse, _span


def test_extract_nested_brackets_and_url_spans():
    text = "В документе есть утверждение с ((вложенными) скобками) и важное правило по адресу https://example.org/a(b) для школы."

    quote_brackets = "утверждение с ((вложенными) скобками)"
    quote_url = "правило по адресу https://example.org/a(b)"

    span_brackets = _span(text, quote_brackets)
    span_url = _span(text, quote_url)

    assert span_brackets is not None
    assert text[span_brackets[0] : span_brackets[1]] == quote_brackets

    assert span_url is not None
    assert text[span_url[0] : span_url[1]] == quote_url

    raw_items = [
        {"quote": quote_brackets, "claim": quote_brackets, "kind": "web_checkable"},
        {"quote": quote_url, "claim": quote_url, "kind": "web_checkable"},
        {"quote": "совершенно выдуманная цитата которой нет в тексте", "kind": "web_checkable"},
    ]

    warnings: list[str] = []
    claims = _parse(raw_items, text, warnings)

    assert len(claims) == 3
    # Проверка утверждения с вложенными скобками
    assert claims[0]["span"] is not None
    assert claims[0]["start"] is not None
    assert claims[0]["end"] is not None
    assert text[claims[0]["start"] : claims[0]["end"]] == quote_brackets

    # Проверка утверждения с URL со скобками
    assert claims[1]["span"] is not None
    assert claims[1]["start"] is not None
    assert claims[1]["end"] is not None
    assert text[claims[1]["start"] : claims[1]["end"]] == quote_url

    # Не найденное утверждение получает span=None
    assert claims[2]["span"] is None
    assert claims[2]["start"] is None
    assert claims[2]["end"] is None
