from __future__ import annotations

from datetime import date

from bormochecker.contract import Label
from bormochecker.evaluate import (
    find_candidates,
    verify_confirmation_cpu,
    verify_contradiction_cpu,
)
from bormochecker.pipeline import quote_matches_claim
from bormochecker.sources import Doc


def test_verify_confirmation_cpu_year_modality_and_quantities():
    ref = date(2026, 11, 13)

    # Несовпадение года -> clarify
    res_year = verify_confirmation_cpu(
        "В 2027 году отремонтируют 120 школ.",
        "В 2026 году отремонтировали 120 школ.",
        reference_datetime=ref,
    )
    assert not res_year.is_valid
    assert res_year.downgraded_label == Label.clarify

    # Расхождение модальности (концепция vs построили) -> clarify
    res_mod = verify_confirmation_cpu(
        "Новый корпус построили в центре города.",
        "Согласована концепция нового корпуса в центре города.",
        reference_datetime=ref,
    )
    assert not res_mod.is_valid
    assert res_mod.downgraded_label == Label.clarify

    # Канонизация величин (1,5 часа == 90 минут, 35 тысяч == 35 000) -> confirmed
    res_ok = verify_confirmation_cpu(
        "Лекция длится 1,5 часа, грант составил 35 тысяч рублей.",
        "Продолжительность лекции 90 минут, сумма гранта — 35 000 рублей.",
        reference_datetime=ref,
    )
    assert res_ok.is_valid
    assert res_ok.downgraded_label == Label.confirmed

    # Разные значения одинаковой размерности -> clarify
    res_diff = verify_confirmation_cpu(
        "Спектакль идет 1,5 часа.",
        "Продолжительность спектакля: 1 час 40 минут.",
        reference_datetime=ref,
    )
    assert not res_diff.is_valid
    assert res_diff.downgraded_label == Label.clarify


def test_verify_contradiction_cpu_guards():
    ref = date(2026, 11, 13)

    # Защита арифметической посылки
    res_prem = verify_contradiction_cpu("Поступило 120 из 200 заявок.", "Всего 200 заявок.", is_arithmetic_premise=True)
    assert not res_prem.is_valid
    assert res_prem.downgraded_label == Label.clarify

    # Разные годы -> clarify
    res_yr = verify_contradiction_cpu(
        "В прошлом году открыли 15 центров.",
        "В 2026 году открыли 20 центров.",
        reference_datetime=ref,
    )
    assert not res_yr.is_valid
    assert res_yr.downgraded_label == Label.clarify

    # Квантор подмножества -> clarify
    res_sub = verify_contradiction_cpu(
        "На первом этапе набрали 50 человек.",
        "Всего на программу зачислили 150 человек.",
        reference_datetime=ref,
    )
    assert not res_sub.is_valid
    assert res_sub.downgraded_label == Label.clarify

    # Прямое противоречие в том же периоде -> error
    res_ok = verify_contradiction_cpu(
        "Продолжительность встречи составляет 1,5 часа.",
        "Продолжительность встречи: 1 час 40 минут.",
        reference_datetime=ref,
    )
    assert res_ok.is_valid
    assert res_ok.downgraded_label == Label.error

    # Разговорное округление до тонны при точном весе 969 кг -> clarify (не FA)
    res_ton = verify_contradiction_cpu(
        "На ярмарке показали плод весом в тонну.",
        "Рекордный плод весил 969 кг.",
        reference_datetime=ref,
    )
    assert not res_ton.is_valid
    assert res_ton.downgraded_label == Label.clarify


def test_find_candidates_window_title_and_quantity_conflict():
    doc = Doc(
        id="poster1",
        url="https://example.org/event",
        title="Симфонический концерт в Филармонии",
        kind="official",
        text="Программа вечера.\nПродолжительность:\n1 час 40 минут\nБилеты от 1500 до 4000 рублей.",
    )
    cands = find_candidates("Концерт в Филармонии длится 1,5 часа.", [doc])
    assert cands, "Ожидался кандидат по окну контекста и конфликту DURATION"
    assert "1 час 40 минут" in cands[0]["quote"]

    # Дедупликация не должна склеивать строки одинакового шаблона с разными числами
    doc_years = Doc(
        id="stats",
        url="https://example.org/stats",
        title="Отчет",
        kind="official",
        text=(
            "В 2025 году университет выпустил ровно 100 дипломированных специалистов.\n"
            "Данные приведены по итогам осенней аттестационной комиссии.\n"
            "Все показатели проверены и утверждены ученым советом.\n"
            "Дополнительная сверка проведена в январе.\n"
            "Методика подсчета опубликована на сайте.\n"
            "Архивные сведения доступны по запросу.\n"
            "В 2026 году университет выпустил ровно 120 дипломированных специалистов."
        ),
    )
    cands_years = find_candidates("Выпущено 120 специалистов в 2026 году.", [doc_years])
    assert len(cands_years) >= 2


def test_quote_matches_claim_with_quantities():
    assert quote_matches_claim("Стоимость билетов от 1500 до 4000 руб.", "Билеты от 1500 до 4000 рублей", doc_title="Афиша")
    assert not quote_matches_claim("Стоимость билетов от 1500 до 3000 руб.", "Билеты от 1500 до 4000 рублей")
