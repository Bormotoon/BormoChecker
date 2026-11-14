from __future__ import annotations

from bormochecker.evaluate import _SENTENCE_RE, _spans, split_sentences


def test_sentence_splitting_12_cases():
    test_cases = [
        "В 12.03.2026 года в школе прошел праздничный концерт.",
        "В 2020/21. учебном году были введены новшества для всех классов.",
        "Великий русский поэт А. С. Пушкин родился в Москве.",
        "Известный писатель И.И. Иванов выступил перед школьниками.",
        "В г. Москва открылась новая современная школа.",
        "Школа расположена на ул. Ленина около парка.",
        "В библиотеке есть книги, учебники, тетради и т. д. для занятий.",
        "Обед в столовой стоит ровно 150 руб. за полную порцию.",
        "В олимпиаде приняли участие более 5 тыс. школьников со всей страны.",
        "Школа им. Ломоносова заняла первое место в рейтинге.",
        "Средний балл по математике составил 4.75 за четверть.",
        "Информация опубликована на https://example.org/news в полном объеме.",
    ]

    combined = " ".join(test_cases)
    result = split_sentences(combined)

    assert len(result) == len(test_cases)
    for expected, actual in zip(test_cases, result):
        assert expected == actual


def test_sentence_re_finditer_compatibility():
    text = "Первое предложение на ул. Мира. Второе предложение."
    matches = list(_SENTENCE_RE.finditer(text))
    assert len(matches) == 2
    assert matches[0].group() == "Первое предложение на ул. Мира."
    assert matches[1].group() == "Второе предложение."

    spans = _spans(text)
    assert len(spans) == 2
    assert text[spans[0][0] : spans[0][1]] == "Первое предложение на ул. Мира."
    assert text[spans[1][0] : spans[1][1]] == "Второе предложение."
