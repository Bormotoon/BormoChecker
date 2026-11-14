from __future__ import annotations

from bormochecker.derived import check_curriculum_hours_sum, check_presupposition_shift
from bormochecker.evaluate import _SUBSET_LIMITER_RE, _verify_contradiction


def test_subset_limiters_generic_rule():
    # Проверка (а): общее правило ограничений подмножества на неэталонных формулировках
    phrase = "во второй волне зачислено 50 студентов"
    assert _SUBSET_LIMITER_RE.search(phrase)

    claim = "Во второй волне зачислено 45 абитуриентов."
    source_quote = "Всего зачислено 300 абитуриентов за всё лето."

    # В источнике общая цифра без разбивки по волнам/этапам -> противоречие не доказано (clarify)
    res = _verify_contradiction(
        claim_text=claim,
        selected_quote=source_quote,
    )
    assert not res.is_valid
    assert res.downgraded_label.value == "clarify"


def test_check_curriculum_hours_sum_pymorphy_arbitrary_nouns():
    # Проверка (б): произвольные существительные через pymorphy3, которых не было в эталонном списке
    text_error = "В лабораторию поступили 15 компьютеров и 10 микроскопов, всего 20 приборов."
    findings = check_curriculum_hours_sum(text_error)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "refuted"
    assert "15 + 10 = 25, а не 20" in findings[0]["explanation"]

    text_correct = "В лабораторию поступили 15 компьютеров и 10 микроскопов, всего 25 приборов."
    assert len(check_curriculum_hours_sum(text_correct)) == 0


def test_presupposition_shift_adjacent_vs_distant():
    # Проверка (в): соседние предложения ловят сдвиг пресуппозиции
    adjacent_text = (
        "На строительной площадке рабочие уложили кабели, похожие на коммуникации.\n"
        "Какие именно коммуникации подведены к объекту, неизвестно."
    )
    findings_adj = check_presupposition_shift(adjacent_text)
    assert len(findings_adj) == 1
    assert findings_adj[0]["verdict"] == "clarify"

    # Не соседние предложения (разделены посторонним текстом) не должны связываться
    distant_text = (
        "На строительной площадке рабочие уложили кабели, похожие на коммуникации.\n"
        "Здесь планируется разбить сквер с липовой аллеей и скамейками для отдыха горожан.\n"
        "Проект благоустройства был разработан архитектурным бюро в прошлом сезоне.\n"
        "Какие именно коммуникации подведены к объекту, неизвестно."
    )
    findings_dist = check_presupposition_shift(distant_text)
    assert len(findings_dist) == 0
