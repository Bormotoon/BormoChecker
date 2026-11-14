from __future__ import annotations

from bormochecker.evaluate import CONFIRMATION_CHECK_PROMPT


def test_rendered_confirmation_prompt_contains_title_url_and_rules():
    title = "Приказ Минпросвещения России № 123"
    url = "https://edsoo.ru/order-123.pdf"
    rendered = CONFIRMATION_CHECK_PROMPT.format(
        note="2026-11-11",
        claim="В школах введен единый график каникул.",
        source_title=title,
        source_url=url,
        quotes="- «Рекомендован примерный график для 1-4 классов.»",
    )

    # Название документа и URL
    assert title in rendered
    assert url in rendered

    # Явное правило: частный источник может подтвердить общее утверждение только в своей части -> clarify
    assert "частный источник может подтвердить общее утверждение только в своей части" in rendered.casefold()
    assert "clarify" in rendered.casefold()

    # Запретить подтверждение по названию без текста
    assert "подтверждение по названию без текста" in rendered.casefold()
