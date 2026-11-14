from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from bormochecker.numerals import (
    Quantity,
    are_numbers_equal,
    extract_quantities,
    parse_cardinal,
    parse_number,
    parse_ordinal,
    resolve_target_years,
    words_to_num,
)


@pytest.mark.parametrize(
    "word,expected_val,expected_ord",
    [
        ("ноль", 0, False),
        ("один", 1, False),
        ("одного", 1, False),
        ("двум", 2, False),
        ("двумя", 2, False),
        ("три", 3, False),
        ("трёх", 3, False),
        ("тремя", 3, False),
        ("четырех", 4, False),
        ("пяти", 5, False),
        ("восьмью", 8, False),
        ("десяти", 10, False),
        ("двенадцатью", 12, False),
        ("двадцати", 20, False),
        ("двадцать пять", 25, False),
        ("сорока", 40, False),
        ("ста", 100, False),
        ("двухсот", 200, False),
        ("пятьсот", 500, False),
        ("тысячи", 1000, False),
        ("миллиона", 1000000, False),
        # Порядковые числительные
        ("первый", 1, True),
        ("второго", 2, True),
        ("третий", 3, True),
        ("третьему", 3, True),
        ("пятом", 5, True),
        ("десятым", 10, True),
        ("сотый", 100, True),
        ("тысячного", 1000, True),
    ],
)
def test_parse_number_20_plus_pairs(word: str, expected_val: int, expected_ord: bool):
    res = parse_number(word)
    assert res is not None
    val, is_ord = res
    assert val == expected_val
    assert is_ord == expected_ord


def test_cardinal_vs_ordinal_separation():
    # Количественные
    assert parse_cardinal("три") == 3
    assert parse_cardinal("трёх") == 3
    assert parse_cardinal("третий") is None  # Порядковое не является количеством
    assert words_to_num("двадцать пять") == 25

    # Порядковые
    assert parse_ordinal("третий") == 3
    assert parse_ordinal("третьего") == 3
    assert parse_ordinal("три") is None


def test_are_numbers_equal_comparison():
    # Количественные равны цифре
    assert are_numbers_equal("три", "3") is True
    assert are_numbers_equal("трех", 3) is True
    assert are_numbers_equal("двадцать пять", "25") is True
    assert are_numbers_equal("ста", 100) is True

    # Порядковые НЕ равны количественным
    assert are_numbers_equal("третий", "3") is False
    assert are_numbers_equal("третьего", 3) is False
    assert are_numbers_equal("первый", 1) is False

    # Порядковые равны между собой
    assert are_numbers_equal("третий", "третий") is True
    assert are_numbers_equal("третий", "третьему") is True


def test_extract_quantities_dimensions_and_matches():
    q_dur1 = extract_quantities("Продолжительность: 1 час 40 минут")
    q_dur2 = extract_quantities("Длительность составляет 1,5 часа")
    q_dur3 = extract_quantities("Идет полтора часа или 90 минут")
    assert any(q.dim == "DURATION" and q.value == Decimal("100") for q in q_dur1)
    assert any(q.dim == "DURATION" and q.value == Decimal("90") for q in q_dur2)
    assert [q for q in q_dur3 if q.dim == "DURATION"] == [
        Quantity("DURATION", Decimal("90"), unit="min", raw="полтора часа"),
        Quantity("DURATION", Decimal("90"), unit="min", raw="90 минут"),
    ]
    assert q_dur2[0].matches(q_dur3[0])
    assert not q_dur1[0].matches(q_dur2[0])

    # Время суток не должно попадать в DURATION или COUNT
    q_time = extract_quantities("Начало в 19 часов и в 19:00")
    assert not any(q.dim == "DURATION" for q in q_time)
    assert not any(q.dim == "COUNT" and q.value == Decimal("19") for q in q_time)

    # Телефоны
    p1 = extract_quantities("Телефон: 8-499-123-45-67")
    p2 = extract_quantities("Звоните +7 (499) 123-45-67")
    assert p1[0].dim == "PHONE" and p2[0].dim == "PHONE"
    assert p1[0].matches(p2[0])

    # Даты и диапазоны дат
    d_range = extract_quantities("Прием с 15 по 30 сентября")
    d_single = extract_quantities("Событие 5 октября")
    assert d_range[0] == Quantity("DATE_RANGE", Decimal("915"), Decimal("930"), raw="с 15 по 30 сентября")
    assert d_single[0] == Quantity("DATE", Decimal("1005"), raw="5 октября")

    # Деньги и масштабные множители (в т.ч. 2000 рублей не должно стать YEAR)
    m_list = extract_quantities("Билеты от 2000 рублей, грант 35 тысяч рублей, бюджет 2,5 млн")
    assert any(q.dim == "MONEY" and q.value == Decimal("2000") for q in m_list)
    assert any(q.dim == "MONEY" and q.value == Decimal("35000") for q in m_list)
    assert any(q.dim == "COUNT" and q.value == Decimal("2500000") for q in m_list)
    assert not any(q.dim == "YEAR" for q in m_list)

    # Проценты и компактные разрядные пробелы
    pct = extract_quantities("Доля составила 47,5% от 35 000 участников")
    assert any(q.dim == "PERCENT" and q.value == Decimal("47.5") for q in pct)
    assert any(q.dim == "COUNT" and q.value == Decimal("35000") for q in pct)


def test_resolve_target_years_relative():
    ref = date(2026, 11, 13)
    years_past, shifted_past = resolve_target_years("В прошлом году построили объект", ref)
    assert years_past == {2025}
    assert shifted_past is True

    years_future, shifted_future = resolve_target_years("В следующем году откроют станцию", ref)
    assert years_future == {2027}
    assert shifted_future is True

    years_curr, shifted_curr = resolve_target_years("В текущем году работы завершены", ref)
    assert years_curr == {2026}
    assert shifted_curr is False


def test_extract_quantities_mass_units():
    q_kg = extract_quantities("масса составила 969 кг")
    q_full = extract_quantities("она весила 1096 килограммов")
    q_ton = extract_quantities("урожай весом 1,5 тонны")
    assert any(q.dim == "MASS" and q.value == Decimal("969") for q in q_kg)
    assert any(q.dim == "MASS" and q.value == Decimal("1096") for q in q_full)
    assert any(q.dim == "MASS" and q.value == Decimal("1500") for q in q_ton)
    assert not q_kg[0].matches(q_full[0])
