"""Разбор числительных словами (количественных и порядковых от 0 до 999 и тысяча/миллион) во всех падежах."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _MORPH = None

# Словарь количественных числительных во всех падежах (0..999, тысяча, миллион)
CARDINALS: dict[str, int] = {
    # 0..9
    "ноль": 0, "нуля": 0, "нулю": 0, "нулем": 0, "нуле": 0, "нули": 0, "нулей": 0,
    "ноля": 0, "нолю": 0, "нолем": 0, "ноле": 0,
    "один": 1, "одна": 1, "одно": 1, "одного": 1, "одному": 1, "одним": 1, "одной": 1, "одну": 1, "одном": 1, "одни": 1, "одних": 1, "одними": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2, "двумя": 2,
    "три": 3, "трех": 3, "трёх": 3, "трем": 3, "трём": 3, "тремя": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "четырем": 4, "четырём": 4, "четырьмя": 4,
    "пять": 5, "пяти": 5, "пятью": 5,
    "шесть": 6, "шести": 6, "шестью": 6,
    "семь": 7, "семи": 7, "семью": 7,
    "восемь": 8, "восьми": 8, "восемью": 8, "восьмью": 8,
    "девять": 9, "девяти": 9, "девятью": 9,
    # 10..19
    "десять": 10, "десяти": 10, "десятью": 10,
    "одиннадцать": 11, "одиннадцати": 11, "одиннадцатью": 11,
    "двенадцать": 12, "двенадцати": 12, "двенадцатью": 12,
    "тринадцать": 13, "тринадцати": 13, "тринадцатью": 13,
    "четырнадцать": 14, "четырнадцати": 14, "четырнадцатью": 14,
    "пятнадцать": 15, "пятнадцати": 15, "пятнадцатью": 15,
    "шестнадцать": 16, "шестнадцати": 16, "шестнадцатью": 16,
    "семнадцать": 17, "семнадцати": 17, "семнадцатью": 17,
    "восемнадцать": 18, "восемнадцати": 18, "восемнадцатью": 18,
    "девятнадцать": 19, "девятнадцати": 19, "девятнадцатью": 19,
    # 20..90
    "двадцать": 20, "двадцати": 20, "двадцатью": 20,
    "тридцать": 30, "тридцати": 30, "тридцатью": 30,
    "сорок": 40, "сорока": 40,
    "пятьдесят": 50, "пятидесяти": 50, "пятьюдесятью": 50,
    "шестьдесят": 60, "шестидесяти": 60, "шестьюдесятью": 60,
    "семьдесят": 70, "семидесяти": 70, "семьюдесятью": 70,
    "восемьдесят": 80, "восьмидесяти": 80, "восемьюдесятью": 80, "восьмьюдесятью": 80,
    "девяносто": 90, "девяноста": 90,
    # 100..900
    "сто": 100, "ста": 100,
    "двести": 200, "двухсот": 200, "двумстам": 200, "двумястами": 200, "двухстах": 200,
    "триста": 300, "трехсот": 300, "трёхсот": 300, "тремстам": 300, "трёмстам": 300, "тремястами": 300, "трехстах": 300, "трёхстах": 300,
    "четыреста": 400, "четырехсот": 400, "четырёхсот": 400, "четыремстам": 400, "четырёмстам": 400, "четырьмястами": 400, "четырехстах": 400, "четырёхстах": 400,
    "пятьсот": 500, "пятисот": 500, "пятистам": 500, "пятьюстами": 500, "пятистах": 500,
    "шестьсот": 600, "шестисот": 600, "шестистам": 600, "шестьюстами": 600, "шестистах": 600,
    "семьсот": 700, "семисот": 700, "семистам": 700, "семьюстами": 700, "семистах": 700,
    "восемьсот": 800, "восьмисот": 800, "восьмистам": 800, "восемьюстами": 800, "восьмьюстами": 800, "восьмистах": 800,
    "девятьсот": 900, "девятисот": 900, "девятистам": 900, "девятьюстами": 900, "девятистах": 900,
    # 1000, 1000000
    "тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тысяче": 1000, "тысячу": 1000, "тысячей": 1000, "тысячею": 1000, "тысячам": 1000, "тысячами": 1000, "тысячах": 1000,
    "миллион": 1000000, "миллиона": 1000000, "миллиону": 1000000, "миллионом": 1000000, "миллионе": 1000000,
    "миллионы": 1000000, "миллионов": 1000000, "миллионам": 1000000, "миллионами": 1000000, "миллионах": 1000000,
}

# Словарь порядковых числительных во всех падежах
ORDINALS: dict[str, int] = {
    # 1..10
    "первый": 1, "первая": 1, "первое": 1, "первые": 1, "первого": 1, "первой": 1, "первому": 1, "первым": 1, "первом": 1, "первых": 1, "первыми": 1, "первую": 1,
    "второй": 2, "вторая": 2, "второе": 2, "вторые": 2, "второго": 2, "второму": 2, "вторым": 2, "втором": 2, "вторых": 2, "вторыми": 2, "вторую": 2,
    "третий": 3, "третья": 3, "третье": 3, "третьи": 3, "третьего": 3, "третьей": 3, "третьему": 3, "третьим": 3, "третьем": 3, "третьих": 3, "третьими": 3, "третью": 3,
    "четвертый": 4, "четвёртый": 4, "четвертая": 4, "четвёртая": 4, "четвертое": 4, "четвёртое": 4, "четвертые": 4, "четвёртые": 4, "четвертого": 4, "четвёртого": 4, "четвертой": 4, "четвёртой": 4, "четвертому": 4, "четвёртому": 4, "четвертым": 4, "четвёртым": 4, "четвертом": 4, "четвёртом": 4, "четвертых": 4, "четвёртых": 4, "четвертыми": 4, "четвёртыми": 4, "четвертую": 4, "четвёртую": 4,
    "пятый": 5, "пятая": 5, "пятое": 5, "пятые": 5, "пятого": 5, "пятой": 5, "пятому": 5, "пятым": 5, "пятом": 5, "пятых": 5, "пятыми": 5, "пятую": 5,
    "шестой": 6, "шестая": 6, "шестое": 6, "шестые": 6, "шестого": 6, "шестому": 6, "шестым": 6, "шестом": 6, "шестых": 6, "шестыми": 6, "шестую": 6,
    "седьмой": 7, "седьмая": 7, "седьмое": 7, "седьмые": 7, "седьмого": 7, "седьмому": 7, "седьмым": 7, "седьмом": 7, "седьмых": 7, "седьмыми": 7, "седьмую": 7,
    "восьмой": 8, "восьмая": 8, "восьмое": 8, "восьмые": 8, "восьмого": 8, "восьмому": 8, "восьмым": 8, "восьмом": 8, "восьмых": 8, "восьмыми": 8, "восьмую": 8,
    "девятый": 9, "девятая": 9, "девятое": 9, "девятые": 9, "девятого": 9, "девятой": 9, "девятому": 9, "девятым": 9, "девятом": 9, "девятых": 9, "девятыми": 9, "девятую": 9,
    "десятый": 10, "десятая": 10, "десятое": 10, "десятые": 10, "десятого": 10, "десятой": 10, "десятому": 10, "десятым": 10, "десятом": 10, "десятых": 10, "десятыми": 10, "десятую": 10,
    # 11..19
    "одиннадцатый": 11, "одиннадцатая": 11, "одиннадцатое": 11, "одиннадцатого": 11,
    "двенадцатый": 12, "двенадцатая": 12, "двенадцатое": 12, "двенадцатого": 12,
    "тринадцатый": 13, "тринадцатая": 13, "тринадцатое": 13, "тринадцатого": 13,
    "четырнадцатый": 14, "четырнадцатая": 14, "четырнадцатое": 14, "четырнадцатого": 14,
    "пятнадцатый": 15, "пятнадцатая": 15, "пятнадцатое": 15, "пятнадцатого": 15,
    "шестнадцатый": 16, "шестнадцатая": 16, "шестнадцатое": 16, "шестнадцатого": 16,
    "семнадцатый": 17, "семнадцатая": 17, "семнадцатое": 17, "семнадцатого": 17,
    "восемнадцатый": 18, "восемнадцатая": 18, "восемнадцатое": 18, "восемнадцатого": 18,
    "девятнадцатый": 19, "девятнадцатая": 19, "девятнадцатое": 19, "девятнадцатого": 19,
    # 20, 30, 40, 50, 60, 70, 80, 90, 100, 1000, 1000000
    "двадцатый": 20, "двадцатая": 20, "двадцатое": 20, "двадцатого": 20, "двадцатой": 20, "двадцатому": 20, "двадцатым": 20, "двадцатом": 20,
    "тридцатый": 30, "тридцатая": 30, "тридцатое": 30, "тридцатого": 30,
    "сороковой": 40, "сороковая": 40, "сороковое": 40, "сорокового": 40,
    "пятидесятый": 50, "шестидесятый": 60, "семидесятый": 70, "восьмидесятый": 80, "девяностый": 90,
    "сотый": 100, "сотая": 100, "сотое": 100, "сотого": 100, "сотые": 100, "сотых": 100,
    "двухсотый": 200, "трехсотый": 300, "трёхсотый": 300, "четырехсотый": 400, "четырёхсотый": 400,
    "пятисотый": 500, "шестисотый": 600, "семисотый": 700, "восьмисотый": 800, "девятисотый": 900,
    "тысячный": 1000, "тысячная": 1000, "тысячное": 1000, "тысячного": 1000,
    "миллионный": 1000000, "миллионная": 1000000, "миллионное": 1000000, "миллионного": 1000000,
}


def parse_number(text: str) -> tuple[int, bool] | None:
    """Разбирает числительное (одно или несколько слов). Возвращает (значение, is_ordinal) или None."""
    if not text:
        return None
    raw = text.strip().casefold().replace("ё", "е")
    # Если это обычное число цифрами: "3" -> (3, False)
    if raw.isdigit():
        return int(raw), False

    words = raw.split()
    if not words:
        return None

    total = 0
    current = 0
    is_ord = False

    for w in words:
        w_ord = False
        val = None

        if w in ORDINALS:
            w_ord = True
            val = ORDINALS[w]
        elif w in CARDINALS:
            val = CARDINALS[w]
        elif _MORPH is not None:
            # Попытка нормализации через pymorphy3
            try:
                parsed = _MORPH.parse(w)
                if parsed:
                    lemma = parsed[0].normal_form.casefold().replace("ё", "е")
                    if lemma in ORDINALS:
                        w_ord = True
                        val = ORDINALS[lemma]
                    elif lemma in CARDINALS:
                        val = CARDINALS[lemma]
            except Exception:
                pass

        if val is None:
            return None

        if w_ord:
            is_ord = True

        if val in (1000, 1000000):
            mult = current if current != 0 else 1
            total += mult * val
            current = 0
        else:
            current += val

    return total + current, is_ord


def parse_cardinal(text: str) -> int | None:
    """Возвращает числовое значение для количественных числительных. Для порядковых возвращает None."""
    parsed = parse_number(text)
    if parsed is not None:
        val, is_ord = parsed
        if not is_ord:
            return val
    return None


def parse_ordinal(text: str) -> int | None:
    """Возвращает числовое значение для порядковых числительных. Для количественных возвращает None."""
    parsed = parse_number(text)
    if parsed is not None:
        val, is_ord = parsed
        if is_ord:
            return val
    return None


def words_to_num(text: str) -> int | None:
    """Совместимый интерфейс word_to_num: возвращает число для количественных слов."""
    return parse_cardinal(text)


def are_numbers_equal(val1: Any, val2: Any) -> bool:
    """Сравнивает два числовых значения (включая записанные словами). Порядковые не считаются количествами."""
    def _extract(v: Any) -> tuple[Decimal | None, bool]:
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v)), False
        s = str(v).strip().casefold()
        if re.fullmatch(r"-?\d+(?:[\.,]\d+)?", s):
            return Decimal(s.replace(",", ".")), False
        parsed = parse_number(s)
        if parsed is not None:
            val, is_ord = parsed
            return Decimal(val), is_ord
        return None, False

    num1, is_ord1 = _extract(val1)
    num2, is_ord2 = _extract(val2)

    if num1 is None or num2 is None:
        return False

    # Порядковые не считать количествами
    if is_ord1 != is_ord2:
        return False

    return num1 == num2


Dimension = Literal[
    "DURATION",    # все интервалы времени приводятся к минутам (90 мин == 1,5 часа)
    "DATE",        # конкретная календарная дата (month, day)
    "DATE_RANGE",  # период/срок (start_month, start_day, end_month, end_day)
    "YEAR",        # календарный год (2025, 2026, 2027)
    "PERCENT",     # процентная доля (47%, 88.1%)
    "MONEY",       # денежная сумма в рублях
    "MASS",        # масса в килограммах (кг, килограмм, т, тонна)
    "PHONE",       # 10-значный нормализованный телефон РФ
    "COUNT",       # прочие именованные и безразмерные числа
]

_NUMERAL_ROOTS: dict[str, Decimal] = {
    "ноль": Decimal("0"), "нулевой": Decimal("0"),
    "один": Decimal("1"), "одна": Decimal("1"), "одно": Decimal("1"), "первый": Decimal("1"), "единица": Decimal("1"),
    "два": Decimal("2"), "две": Decimal("2"), "второй": Decimal("2"), "двое": Decimal("2"), "пара": Decimal("2"), "вдвое": Decimal("2"),
    "три": Decimal("3"), "третий": Decimal("3"), "трое": Decimal("3"), "втрое": Decimal("3"),
    "четыре": Decimal("4"), "четвертый": Decimal("4"), "четверо": Decimal("4"),
    "пять": Decimal("5"), "пятый": Decimal("5"), "пятеро": Decimal("5"),
    "шесть": Decimal("6"), "шестой": Decimal("6"),
    "семь": Decimal("7"), "седьмой": Decimal("7"),
    "восемь": Decimal("8"), "восьмой": Decimal("8"),
    "девять": Decimal("9"), "девятый": Decimal("9"),
    "десять": Decimal("10"), "десятый": Decimal("10"),
    "одиннадцать": Decimal("11"), "двенадцать": Decimal("12"),
    "тринадцать": Decimal("13"), "четырнадцать": Decimal("14"),
    "пятнадцать": Decimal("15"), "шестнадцать": Decimal("16"),
    "семнадцать": Decimal("17"), "восемнадцать": Decimal("18"),
    "девятнадцать": Decimal("19"), "двадцать": Decimal("20"),
    "тридцать": Decimal("30"), "сорок": Decimal("40"),
    "пятьдесят": Decimal("50"), "шестьдесят": Decimal("60"),
    "семьдесят": Decimal("70"), "восемьдесят": Decimal("80"),
    "девяносто": Decimal("90"), "сто": Decimal("100"),
    "полтора": Decimal("1.5"), "полторы": Decimal("1.5"),
    "половина": Decimal("0.5"), "четверть": Decimal("0.25"),
}

_MONTH_STEMS: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


@dataclass(frozen=True, slots=True)
class Quantity:
    dim: Dimension
    value: Decimal
    secondary_value: Decimal | None = None
    unit: str = ""
    raw: str = ""

    def matches(self, other: Quantity, rel_tol: Decimal = Decimal("0.01")) -> bool:
        if self.dim != other.dim:
            return False
        if self.dim in ("DATE", "DATE_RANGE", "YEAR", "PHONE"):
            return self.value == other.value and self.secondary_value == other.secondary_value
        if self.secondary_value is not None or other.secondary_value is not None:
            return self.value == other.value and self.secondary_value == other.secondary_value
        if self.value == other.value:
            return True
        denom = max(abs(self.value), abs(other.value), Decimal("1"))
        return (abs(self.value - other.value) / denom) <= rel_tol


def resolve_target_years(text: str, reference_date: date | datetime | None = None) -> tuple[set[int], bool]:
    """Возвращает (множество целевых лет утверждения, флаг_относительного_прошлого_или_будущего).
    Работает строго относительно reference_date (без хардкода текущего года).
    """
    ref_year = reference_date.year if isinstance(reference_date, (date, datetime)) else 2026
    t = (text or "").casefold().replace("ё", "е")
    years = {int(m.group(1)) for m in re.finditer(r"\b(19\d\d|20\d\d)\b", t)}
    is_relative_shifted = False

    if re.search(r"\b(?:в\s+прошл\w+|в\s+минувш\w+|в\s+предыдущ\w+)\s+году\b|\bгодом\s+ранее\b|\bв\s+прошл\w+\s+сезон\w*\b", t):
        years.add(ref_year - 1)
        is_relative_shifted = True
    if re.search(r"\b(?:в\s+следующ\w+|в\s+будущ\w+)\s+году\b", t):
        years.add(ref_year + 1)
        is_relative_shifted = True
    if re.search(r"\b(?:в\s+этом|в\s+текущ\w+|в\s+нынешн\w+)\s+году\b", t):
        years.add(ref_year)
    return years, is_relative_shifted


def extract_quantities(text: str, reference_date: date | datetime | None = None) -> list[Quantity]:
    """Извлекает канонические физические величины, даты, сроки, телефоны и числа с множителями."""
    if not text:
        return []
    t = text.casefold().replace("ё", "е")
    # Склеиваем разрядные пробелы внутри чисел: "35 000" -> "35000", но не трогаем переносы строк
    t_compact = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", t)
    out: list[Quantity] = []
    covered_spans: list[tuple[int, int]] = []

    def _mark(s: int, e: int) -> bool:
        if any(cs <= s < ce or cs < e <= ce for cs, ce in covered_spans):
            return False
        covered_spans.append((s, e))
        return True

    # 1. Телефоны РФ: 8-499-734-31-71 / +7 (499) 734-31-71
    for m in re.finditer(r"(?:\+7|8)[\s\-\(\)]*(\d{3})[\s\-\(\)]*(\d{3})[\s\-]*(\d{2})[\s\-]*(\d{2})\b", t_compact):
        if _mark(m.start(), m.end()):
            digits = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}"
            out.append(Quantity("PHONE", Decimal(digits), raw=m.group(0)))

    # 2. Продолжительность (DURATION -> в минутах): "1 час 40 минут", "1,5 часа", "полтора часа", "90 минут"
    for m in re.finditer(r"\b(\d+)\s*ч(?:ас(?:а|ов)?)?\.?\s*(\d+)\s*мин(?:ут(?:ы)?)?\b", t_compact):
        if _mark(m.start(), m.end()):
            mins = int(m.group(1)) * 60 + int(m.group(2))
            out.append(Quantity("DURATION", Decimal(mins), unit="min", raw=m.group(0)))
    for m in re.finditer(r"\b(\d+(?:[\.,]\d+)?)\s*ч(?:ас(?:а|ов)?)?\b(?!\s*\d+\s*мин)", t_compact):
        # Исключаем время суток "в 19 часов"
        prefix = t_compact[max(0, m.start() - 4):m.start()]
        if "в " in prefix:
            _mark(m.start(), m.end())
            continue
        if _mark(m.start(), m.end()):
            val = Decimal(m.group(1).replace(",", "."))
            mins = (val * Decimal(60)).quantize(Decimal("1"))
            out.append(Quantity("DURATION", mins, unit="min", raw=m.group(0)))
    for m in re.finditer(r"\bполтор[аы]\s+часа\b", t_compact):
        if _mark(m.start(), m.end()):
            out.append(Quantity("DURATION", Decimal(90), unit="min", raw=m.group(0)))
    for m in re.finditer(r"\b(\d+)\s*мин(?:ут(?:а|ы)?)?\b", t_compact):
        if _mark(m.start(), m.end()):
            out.append(Quantity("DURATION", Decimal(m.group(1)), unit="min", raw=m.group(0)))

    # 3. Диапазоны дат и одиночные даты: "с 15 по 30 сентября", "15–30 сентября", "1 октября"
    month_pat = (
        r"(январ[яеию]|феврал[яеию]|март[аеу]|апрел[яеию]|ма[яеиюй]|июн[яеию]|"
        r"июл[яеию]|август[аеу]|сентябр[яеию]|октябр[яеию]|ноябр[яеию]|декабр[яеию])"
    )
    for m in re.finditer(rf"\b(?:с\s+)?(\d{{1,2}})\s*(?:по|до|[-–—])\s*(\d{{1,2}})\s+{month_pat}\b", t_compact):
        if _mark(m.start(), m.end()):
            m_num = next((v for k, v in _MONTH_STEMS.items() if m.group(3).startswith(k)), 0)
            d1 = Decimal(m_num * 100 + int(m.group(1)))
            d2 = Decimal(m_num * 100 + int(m.group(2)))
            out.append(Quantity("DATE_RANGE", d1, secondary_value=d2, raw=m.group(0)))

    for m in re.finditer(rf"\b(\d{{1,2}})\s+{month_pat}\b", t_compact):
        if _mark(m.start(), m.end()):
            m_num = next((v for k, v in _MONTH_STEMS.items() if m.group(2).startswith(k)), 0)
            d_val = Decimal(m_num * 100 + int(m.group(1)))
            out.append(Quantity("DATE", d_val, raw=m.group(0)))

    # 4. Проценты (PERCENT)
    for m in re.finditer(r"\b(\d+(?:[\.,]\d+)?)\s*(?:%|процент\w*)", t_compact):
        if _mark(m.start(), m.end()):
            out.append(Quantity("PERCENT", Decimal(m.group(1).replace(",", ".")), unit="%", raw=m.group(0)))

    # 4a. Масса (MASS -> в килограммах): "969 кг", "1096 килограммов", "1,5 тонны"
    for m in re.finditer(r"\b(\d+(?:[\.,]\d+)?)\s*(кг|килограмм\w*|тонн\w*|т\b(?!\.))", t_compact):
        if _mark(m.start(), m.end()):
            val = Decimal(m.group(1).replace(",", "."))
            if m.group(2).startswith("т"):
                val *= Decimal(1000)
            out.append(Quantity("MASS", val, unit="kg", raw=m.group(0)))

    # 5. Числа с масштабными множителями ("35 тысяч", "2,5 млн") и денежные суммы (до YEAR, чтобы "2000 рублей" не стало годом)
    for m in re.finditer(
        r"\b(\d+(?:[\.,]\d+)?)\s*(тыс(?:яч[аиуе]?)?\.?|млн|миллион\w*|млрд|миллиард\w*)?\s*(рубл\w*|руб\.?|₽|р\.)?",
        t_compact,
    ):
        if not m.group(2) and not m.group(3):
            continue
        if _mark(m.start(), m.end()):
            val = Decimal(m.group(1).replace(",", "."))
            mult_raw = (m.group(2) or "").lower()
            if mult_raw.startswith("тыс"):
                val *= Decimal(1000)
            elif mult_raw.startswith(("млн", "миллион")):
                val *= Decimal(1_000_000)
            elif mult_raw.startswith(("млрд", "миллиард")):
                val *= Decimal(1_000_000_000)
            dim: Dimension = "MONEY" if m.group(3) else "COUNT"
            out.append(Quantity(dim, val, raw=m.group(0)))

    # 6. Годы (YEAR)
    explicit_years: set[int] = set()
    covered_year_candidates: set[int] = set()
    for m in re.finditer(r"\b(19\d\d|20\d\d)\b", t_compact):
        y = int(m.group(1))
        if _mark(m.start(), m.end()):
            explicit_years.add(y)
            out.append(Quantity("YEAR", Decimal(y), raw=m.group(1)))
        else:
            covered_year_candidates.add(y)
    target_years, _ = resolve_target_years(t_compact, reference_date)
    for y in sorted(target_years - explicit_years - covered_year_candidates):
        out.append(Quantity("YEAR", Decimal(y), raw=str(y)))

    # 7. Время суток "19:00" исключаем из обычных COUNT
    for m in re.finditer(r"\b\d{1,2}:\d{2}\b", t_compact):
        _mark(m.start(), m.end())

    # 8. Обычные числа (COUNT), исключая маркеры списков вида "5)" и подпункты вида "17.6.5" / "11.02.16"
    for m in re.finditer(r"(?<!\d\.)\b(\d+(?:[\.,]\d+)?)\b(?!\s*\)|\.\d)", t_compact):
        if not _mark(m.start(), m.end()):
            continue
        try:
            val = Decimal(m.group(1).replace(",", "."))
            out.append(Quantity("COUNT", val, raw=m.group(0)))
        except InvalidOperation:
            pass

    return out


def is_typo_or_minor_numeric_diff(q_claim: Quantity, q_source: Quantity) -> bool:
    """Определяет, отличаются ли два числа одной размерности незначительно (опечатка в 1 цифру,
    перестановка соседних цифр или расхождение <= 15%), например 966 vs 926 или 90 мин vs 100 мин.
    """
    if q_claim.dim != q_source.dim or q_claim.value == q_source.value:
        return False
    s1 = str(q_claim.value.normalize())
    s2 = str(q_source.value.normalize())
    # 1. Одинаковая длина >= 2 цифр и расстояние Хэмминга == 1 (например, "966" и "926")
    if len(s1) == len(s2) and len(s1) >= 2:
        hamming = sum(1 for a, b in zip(s1, s2) if a != b)
        if hamming == 1:
            return True
        # Или транспозиция двух соседних цифр (например, "47" и "74")
        if hamming == 2:
            diffs = [i for i, (a, b) in enumerate(zip(s1, s2)) if a != b]
            if len(diffs) == 2 and diffs[1] - diffs[0] == 1:
                i = diffs[0]
                if s1[i] == s2[i + 1] and s1[i + 1] == s2[i]:
                    return True
    # 2. Небольшое относительное отклонение (до 15%) для величин >= 10
    denom = max(abs(q_claim.value), abs(q_source.value), Decimal("1"))
    rel_diff = abs(q_claim.value - q_source.value) / denom
    return denom >= Decimal("10") and rel_diff <= Decimal("0.15")
