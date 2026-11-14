from __future__ import annotations

from bormochecker.extract import _fallback_extract, _parse


def test_heading_and_normative_rules_routing():
    # Текст написан специально для теста: отдельный заголовок и два правила с нормативной ссылкой
    text = (
        "Правила безопасности в компьютерном классе\n\n"
        "1. Запрещается прикасаться к токоведущим частям оборудования согласно инструкции по технике безопасности.\n"
        "2. При возникновении неисправностей учащиеся обязаны немедленно сообщить преподавателю в соответствии с регламентом школы."
    )

    # 1. Проверка детерминированного fallback-извлечения
    fallback_claims = _fallback_extract(text)

    # Заголовок не должен быть извлечён
    assert not any("Правила безопасности" in c["text"] for c in fallback_claims)

    # Должны быть извлечены оба правила и помечены как web_checkable
    assert len(fallback_claims) == 2
    assert all(c["kind"] == "web_checkable" for c in fallback_claims)
    assert any("Запрещается прикасаться" in c["text"] for c in fallback_claims)
    assert any("обязаны немедленно сообщить" in c["text"] for c in fallback_claims)

    # 2. Проверка парсинга результатов LLM (если модель вернула заголовок и классифицировала правила как editorial/opinion)
    raw_llm_items = [
        {
            "quote": "Правила безопасности в компьютерном классе",
            "claim": "Правила безопасности в компьютерном классе",
            "kind": "opinion",
        },
        {
            "quote": "Запрещается прикасаться к токоведущим частям оборудования согласно инструкции по технике безопасности",
            "claim": "Запрещается прикасаться к токоведущим частям согласно инструкции",
            "kind": "editorial",
        },
        {
            "quote": "При возникновении неисправностей учащиеся обязаны немедленно сообщить преподавателю в соответствии с регламентом школы",
            "claim": "Учащиеся обязаны сообщить учителю по регламенту",
            "kind": "opinion",
        },
    ]

    warnings: list[str] = []
    parsed_claims = _parse(raw_llm_items, text, warnings)

    # Заголовок не извлечён
    assert not any("Правила безопасности" in c.get("text", "") or "Правила безопасности" in c.get("quote", "") for c in parsed_claims)

    # Оба правила маршрутизированы в web_checkable, несмотря на сырые editorial/opinion
    assert len(parsed_claims) == 2
    assert all(c["kind"] == "web_checkable" for c in parsed_claims)
