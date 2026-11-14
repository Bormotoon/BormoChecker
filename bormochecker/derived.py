"""Детерминированная проверка арифметики внутри текста.

Единственный шаг, который не зависит ни от модели, ни от поиска: код пересчитывает
числа, которые дословно есть либо в самом утверждении, либо в принятых цитатах.
«303 из 344 названо 93 %» такой проверкой ловится, а поиском — нет.

`Decimal` без плавающей точки, никакой магии: `matches` — совпало, `contradicts` —
не совпало, `insufficient_data` — данных не хватает. Ошибки наружу не выходят.
Учитывает правило scope: сильный вывод возможен, только когда область предпосылок совпадает.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from zoneinfo import ZoneInfo

import yaml
from bormochecker.numerals import _NUMERAL_ROOTS, words_to_num
from bormochecker.paths import norms_path

from bormochecker.contract import Label

OPERATIONS = {"mean", "ratio", "percentage", "compare"}
SCOPE_KEYS = ("period", "territory", "population", "metric")


def _decimal(value: object) -> Decimal:
    return Decimal(str(value).strip().replace(" ", "").replace(",", "."))


def _fold(value: str) -> str:
    """Регистр и пробелы не мешают сверке области с текстом."""
    return " ".join(str(value or "").casefold().split())


def _display(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f") if normalized != normalized.to_integral() else str(normalized.quantize(Decimal(1)))


def _scope_is_verified(operands: list[dict], allowed: list[str]) -> tuple[bool, str]:
    """Предпосылки про одно и то же, и область названа словами из самого текста.

    Пустая область — тоже расхождение: «303 из 344» без указания, о чём речь, нельзя
    использовать как доказательство. Зато пустые необязательные поля (территория у
    каталога) допустимы, если остальные совпадают.
    """
    scopes = [operand.get("scope") for operand in operands]
    if not all(isinstance(scope, dict) for scope in scopes):
        return False, "область предпосылок не заполнена"
    signatures = {tuple(str(scope.get(key, "")).strip().casefold() for key in SCOPE_KEYS) for scope in scopes}
    if len(signatures) != 1:
        return False, "предпосылки относятся к разному периоду, территории или показателю"
    values = [value for value in next(iter(signatures)) if value]
    if not values:
        return False, "область предпосылок не указана"
    haystack = " ".join(_fold(text) for text in allowed)
    missing = [value for value in values if _fold(value) not in haystack]
    if missing:
        return False, f"область не подтверждена текстом: {', '.join(missing[:2])}"
    return True, ""


def _tolerance(requested: object, claimed: Decimal) -> Decimal:
    """Допуск по умолчанию — половина последнего знака заявленного результата.

    «303 из 344 — это 88 %» верно: 88,08 округляется до 88. Проверять 88,08 против 88 с
    точностью до сотых значило бы обвинять новость в округлении.
    """
    if requested is not None:
        return _decimal(requested)
    return Decimal(1).scaleb(claimed.as_tuple().exponent) / 2


@dataclass(frozen=True, slots=True)
class Derived:
    status: str  # matches | contradicts | insufficient_data
    expression: str = ""
    result: str = ""
    same_scope_verified: bool = False
    reason: str = ""


def number_occurs(value: object, text: str) -> bool:
    """Число из предпосылки дословно есть в тексте: «1 000», «1000» и «1 000,00» — одно."""
    try:
        number = _decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return False
    normalized = format(number.normalize(), "f")
    variants = {normalized, normalized.replace(".", ",")}
    if normalized.endswith(".0"):
        variants.add(normalized[:-2])
    if number == number.to_integral() and len(normalized) > 3:  # «1 000» с пробелом — тоже запись
        spaced = f"{int(number):,}".replace(",", " ")
        variants |= {spaced, spaced.replace(" ", " ")}
    return any(re.search(rf"(?<!\d){re.escape(variant)}(?!\d)", text or "") for variant in variants)


def evaluate(request: object, *, claim_text: str, quotes: list[str]) -> Derived:
    """Пересчитывает заявленную формулу. Смысл и область модель не угадывает."""
    if not isinstance(request, dict):
        return Derived("insufficient_data", reason="нет запроса на пересчёт")
    operation = str(request.get("operation") or "").strip()
    operands = request.get("operands")
    if operation not in OPERATIONS or not isinstance(operands, list) or not operands:
        return Derived("insufficient_data", reason="неизвестная операция или нет чисел")
    if not all(isinstance(operand, dict) for operand in operands):
        return Derived("insufficient_data", reason="операнд не объект")
    try:
        values = [_decimal(operand["value"]) for operand in operands]
        claimed = _decimal(request["claimed_result"])
        limit = _tolerance(request.get("tolerance"), claimed)
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return Derived("insufficient_data", reason="некорректное значение")
    if limit < 0:
        return Derived("insufficient_data", reason="допуск не может быть отрицательным")
    units = [str(operand.get("unit") or "").strip() for operand in operands]
    if any(not unit for unit in units):
        return Derived("insufficient_data", reason="у числа нет единицы")
    if operation in {"mean", "percentage", "compare"} and len(set(units)) != 1:
        return Derived("insufficient_data", reason="несовместимые единицы")
    if operation == "ratio" and not str(request.get("result_unit") or "").strip():
        return Derived("insufficient_data", reason="у отношения нет единицы результата")

    # каждая предпосылка обязана дословно быть в цитатах или в самом тексте новости
    allowed = [*quotes, claim_text]
    if any(not any(number_occurs(value, text) for text in allowed) for value in values):
        return Derived("insufficient_data", reason="предпосылки не найдены в цитатах или в тексте новости")
    if operation != "compare" and not number_occurs(request.get("claimed_result"), claim_text):
        # у compare результат — это знак сравнения, в тексте его нет по построению
        return Derived("insufficient_data", reason="заявленный результат не найден в тексте новости")

    try:
        with localcontext() as context:
            context.prec = 28
            if operation == "mean":
                result = sum(values, Decimal(0)) / Decimal(len(values))
                expression = f"({' + '.join(map(_display, values))}) / {len(values)}"
            elif operation == "ratio":
                if len(values) != 2 or values[1] == 0:
                    return Derived("insufficient_data", reason="деление на ноль")
                result = values[0] / values[1]
                expression = f"{_display(values[0])} / {_display(values[1])}"
            elif operation == "percentage":
                if len(values) != 2 or values[1] == 0:
                    return Derived("insufficient_data", reason="деление на ноль")
                result = values[0] / values[1] * Decimal(100)
                expression = f"{_display(values[0])} / {_display(values[1])} * 100"
            else:
                if len(values) != 2:
                    return Derived("insufficient_data", reason="сравнению нужны два числа")
                result = Decimal(int(values[0].compare(values[1])))
                expression = f"compare({_display(values[0])}, {_display(values[1])})"
    except (InvalidOperation, ZeroDivisionError):
        return Derived("insufficient_data", reason="вычисление невозможно")

    verified, reason = _scope_is_verified(operands, allowed)
    if not verified:  # расчёт показываем, но вердикт по нему — нельзя: область предпосылок не та
        return Derived("insufficient_data", expression=expression, result=_display(result), reason=reason)
    status = "matches" if abs(result - claimed) <= limit else "contradicts"
    return Derived(status, expression, _display(result), same_scope_verified=True)


_MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_WEEKDAYS_RU = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскресень": 6,
}
_WEEKDAY_NAMES_NOM = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def check_weekday_dates(
    text: str,
    reference_datetime: datetime.datetime | None = None,
) -> list[dict]:
    """Проверяет совпадение даты и дня недели в конструкциях вида '1 октября (это четверг)'
    или 'в пятницу, 25 сентября'."""
    if reference_datetime is not None:
        year_to_use = reference_datetime.year
    else:
        year_to_use = datetime.datetime.now(ZoneInfo("Europe/Moscow")).year

    findings: list[dict] = []
    pattern_paren = re.compile(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"(?:\s+(\d{4})\s*(?:г\.?|года)?)?\s*\((?:это\s+)?([а-яё]+)\)",
        re.IGNORECASE,
    )
    pattern_lead = re.compile(
        r"\bв\s+([а-яё]+),\s+(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"(?:\s+(\d{4})\s*(?:г\.?|года)?)?",
        re.IGNORECASE,
    )

    for m in pattern_paren.finditer(text):
        day = int(m.group(1))
        month_str = m.group(2).casefold()
        has_year = bool(m.group(3))
        year = int(m.group(3)) if m.group(3) else year_to_use
        claimed_wd_str = m.group(4).casefold()

        month = next((v for k, v in _MONTHS_RU.items() if month_str.startswith(k)), None)
        claimed_wd = next((v for k, v in _WEEKDAYS_RU.items() if claimed_wd_str.startswith(k)), None)
        if month is None or claimed_wd is None:
            continue
        try:
            actual_wd = datetime.date(year, month, day).weekday()
        except ValueError:
            continue
        actual_name = _WEEKDAY_NAMES_NOM[actual_wd]
        matched_phrase = m.group(0)
        if actual_wd != claimed_wd:
            replaced_phrase = matched_phrase.replace(m.group(4), actual_name)
            findings.append({
                "type": "weekday_mismatch",
                "span": (m.start(), m.end()),
                "quote": matched_phrase,
                "verdict": "refuted",
                "explanation": f"По календарю {day} {month_str} {year} года — это {actual_name}, а не {claimed_wd_str}.",
                "suggestion": replaced_phrase,
            })
        else:
            if has_year:
                findings.append({
                    "type": "weekday_verified",
                    "span": (m.start(), m.end()),
                    "quote": matched_phrase,
                    "verdict": "confirmed",
                    "explanation": f"По календарю {day} {month_str} {year} года — это действительно {actual_name}.",
                    "suggestion": None,
                })
            else:
                findings.append({
                    "type": "weekday_verified",
                    "span": (m.start(), m.end()),
                    "quote": matched_phrase,
                    "verdict": "clarify",
                    "explanation": f"По календарю {year} года (год определён по контексту) {day} {month_str} — это {actual_name}.",
                    "suggestion": None,
                })

    for m in pattern_lead.finditer(text):
        claimed_wd_str = m.group(1).casefold()
        day = int(m.group(2))
        month_str = m.group(3).casefold()
        has_year = bool(m.group(4))
        year = int(m.group(4)) if m.group(4) else year_to_use

        month = next((v for k, v in _MONTHS_RU.items() if month_str.startswith(k)), None)
        claimed_wd = next((v for k, v in _WEEKDAYS_RU.items() if claimed_wd_str.startswith(k)), None)
        if month is None or claimed_wd is None:
            continue
        try:
            actual_wd = datetime.date(year, month, day).weekday()
        except ValueError:
            continue
        actual_name = _WEEKDAY_NAMES_NOM[actual_wd]
        matched_phrase = m.group(0)
        if actual_wd != claimed_wd:
            replaced_phrase = matched_phrase.replace(m.group(1), actual_name)
            findings.append({
                "type": "weekday_mismatch",
                "span": (m.start(), m.end()),
                "quote": matched_phrase,
                "verdict": "refuted",
                "explanation": f"По календарю {day} {month_str} {year} года — это {actual_name}, а не {claimed_wd_str}.",
                "suggestion": replaced_phrase,
            })
        else:
            if has_year:
                findings.append({
                    "type": "weekday_verified",
                    "span": (m.start(), m.end()),
                    "quote": matched_phrase,
                    "verdict": "confirmed",
                    "explanation": f"По календарю {day} {month_str} {year} года — это действительно {actual_name}.",
                    "suggestion": None,
                })
            else:
                findings.append({
                    "type": "weekday_verified",
                    "span": (m.start(), m.end()),
                    "quote": matched_phrase,
                    "verdict": "clarify",
                    "explanation": f"По календарю {year} года (год определён по контексту) {day} {month_str} — это {actual_name}.",
                    "suggestion": None,
                })

    return findings


try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except ImportError:
    _MORPH = None


def _has_noun(phrase: str) -> bool:
    """Проверяет через pymorphy3 наличие хотя бы одного существительного во фразе."""
    if not phrase:
        return False
    if not _MORPH:
        return True
    tokens = re.findall(r"[а-яёa-z]+", phrase.casefold())
    for t in tokens:
        for p in _MORPH.parse(t):
            if p.tag.POS == "NOUN":
                return True
    return False


def check_curriculum_hours_sum(text: str) -> list[dict]:
    """Проверяет предложения, где перечисляются слагаемые объектов/часов/квот/мест с итоговой суммой."""
    findings: list[dict] = []
    pattern = re.compile(
        r"\b(\d+)\s+((?:[а-яёa-z_\-]+\s+){0,3}[а-яёa-z_\-]+)\s+и\s+(\d+)\s+((?:[а-яёa-z_\-]+\s+){0,3}[а-яёa-z_\-]+)[^\d\n]+(?:всего|итого|суммарно|вместе|составляет|равно)\s*(?:до\s+)?(\d+)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        noun_phrase1 = m.group(2)
        noun_phrase2 = m.group(4)
        if not (_has_noun(noun_phrase1) and _has_noun(noun_phrase2)):
            continue
        a = int(m.group(1))
        b = int(m.group(3))
        stated_total = int(m.group(5))
        real_total = a + b
        if real_total != stated_total:
            quote = m.group(0)
            findings.append({
                "span": (m.start(), m.end()),
                "quote": quote,
                "verdict": "refuted",
                "explanation": f"Арифметическая ошибка: {a} + {b} = {real_total}, а не {stated_total}.",
                "suggestion": re.sub(rf"\b{stated_total}\b", str(real_total), quote),
            })
    return findings


@lru_cache(maxsize=1)
def load_education_norms() -> list[dict]:
    path = norms_path()
    if not path.is_file():
        return []
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        return content if isinstance(content, list) else []
    except Exception:
        return []


def check_known_education_orders(
    text: str,
    reference_datetime: datetime.datetime | None = None,
) -> list[dict]:
    """Сверяет упоминания базовых приказов и стандартов с нормативным эталоном из norms.yaml.

    Норма применяется только когда:
    1. Область действия (scope) совпадает с контекстом утверждения.
    2. reference_datetime попадает в срок действия нормы (effective_from .. effective_to).
    Вне срока действия или при несовпадении области норма не даёт refuted (при сомнениях clarify).
    """
    findings: list[dict] = []
    norms = load_education_norms()
    if not norms:
        return findings

    if reference_datetime is not None:
        ref_date = reference_datetime.date()
    else:
        ref_date = datetime.datetime.now(ZoneInfo("Europe/Moscow")).date()

    t = text.casefold()

    for norm in norms:
        scope = norm.get("scope", {})
        scope_keywords = scope.get("keywords", [])
        scope_grades = scope.get("grades", [])

        # Проверка ключевых слов области действия
        if scope_keywords and not any(kw.casefold() in t for kw in scope_keywords):
            continue

        # Проверка противоречия с нормой и точного диапазона символов
        patterns = norm.get("refuted_patterns", [])
        matched_obj = None
        for pat in patterns:
            m_pat = re.search(pat, text, re.IGNORECASE)
            if m_pat:
                matched_obj = m_pat
                break

        # Проверка классов области действия (если применимо)
        grade_matched = True
        if scope_grades:
            grade_matched = False
            for g in scope_grades:
                if re.search(rf"\b{g}\s*класс", t) or f"{g}-й класс" in t or f"{g} класс" in t:
                    grade_matched = True
                    break
                if g in (6, 7, 8) and re.search(r"[6-8][\s–-]+[6-8]\s*класс", t):
                    grade_matched = True
                    break
                if g in (8, 9) and re.search(r"[89][\s–-]+[89]\s*класс", t):
                    grade_matched = True
                    break

        if not grade_matched:
            # Если норма прямо названа по номеру приказа, но применена к чужому классу -> clarify
            order_nums = re.findall(r"№\s*(\d+)", norm.get("title", ""))
            has_explicit_order = any(re.search(rf"(?:№\s*|приказ\w*\s+){num}\b", t) for num in order_nums)
            if has_explicit_order and matched_obj is not None:
                s_start = text.rfind(".", 0, matched_obj.start())
                s_start = 0 if s_start == -1 else s_start + 1
                s_end = text.find(".", matched_obj.end())
                s_end = len(text) if s_end == -1 else s_end + 1
                while s_start < s_end and text[s_start].isspace():
                    s_start += 1
                title = norm.get("title", "норматив")
                findings.append({
                    "span": (s_start, s_end),
                    "quote": text[s_start:s_end],
                    "verdict": "clarify",
                    "explanation": f"Область действия норматива ({title}) не совпадает с указанными классами. Требует уточнения.",
                    "suggestion": None,
                })
            continue

        # Проверка даты действия нормы
        effective_from = datetime.date.fromisoformat(norm["effective_from"])
        effective_to = datetime.date.fromisoformat(norm.get("effective_to") or "2099-12-31")

        is_effective = (effective_from <= ref_date <= effective_to)

        if matched_obj is not None:
            s_start = text.rfind(".", 0, matched_obj.start())
            s_start = 0 if s_start == -1 else s_start + 1
            s_end = text.find(".", matched_obj.end())
            s_end = len(text) if s_end == -1 else s_end + 1
            while s_start < s_end and text[s_start].isspace():
                s_start += 1
            span_tuple = (s_start, s_end)
            quote_str = text[s_start:s_end]
            title = norm.get("title", "норматив")
            source_url = norm.get("source_url", "")
            value = norm.get("value", "")
            url_part = f" ({source_url})" if source_url else ""
            if is_effective:
                findings.append({
                    "span": span_tuple,
                    "quote": quote_str,
                    "verdict": "refuted",
                    "explanation": f"Согласно {title}{url_part}: {value}",
                    "suggestion": norm.get("suggestion"),
                })
            else:
                findings.append({
                    "span": span_tuple,
                    "quote": quote_str,
                    "verdict": "clarify",
                    "explanation": (
                        f"Норматив {title}{url_part} не действовал на момент проверки {ref_date:%d.%m.%Y} "
                        f"(срок действия: {effective_from:%d.%m.%Y} – {effective_to:%d.%m.%Y}). Требует уточнения редакции."
                    ),
                    "suggestion": None,
                })

    return findings


def check_percentage_arithmetic(text: str) -> list[dict]:
    """Сверяет процентные расчеты в предложениях текста вида '303 из 344 — это 93%' или '19% (9 из 36)'."""
    findings: list[dict] = []
    # 1. 303 из 344 — это 93%
    pattern1 = re.compile(
        r"((\d+(?:[\.,]\d+)?)\s*(?:из|от)\s*(\d+(?:[\.,]\d+)?))[^\n.!?]*?(?:—|-|это|составляет|равно)?\s*((\d+(?:[\.,]\d+)?)\s*%)",
        re.IGNORECASE,
    )
    for m in pattern1.finditer(text):
        try:
            premise_quote = m.group(1).strip()
            part = Decimal(m.group(2).replace(",", "."))
            total = Decimal(m.group(3).replace(",", "."))
            error_quote = m.group(4).strip()
            claimed = Decimal(m.group(5).replace(",", "."))
            if total > 0:
                calc = (part / total) * Decimal(100)
                diff = abs(calc - claimed)
                if diff > Decimal("0.6"):
                    findings.append({
                        "span": (m.start(4), m.end(4)),
                        "quote": m.group(0),
                        "premise_quote": premise_quote,
                        "error_quote": error_quote,
                        "part": m.group(2),
                        "total": m.group(3),
                        "claimed": m.group(5),
                        "verdict": "refuted",
                        "explanation": f"Арифметическая ошибка: {part} из {total} = {calc:.1f}%, а не {claimed}%.",
                        "suggestion": m.group(0).replace(f"{m.group(5)}%", f"{calc:.1f}%"),
                    })
                else:
                    findings.append({
                        "span": (m.start(4), m.end(4)),
                        "quote": m.group(0),
                        "premise_quote": premise_quote,
                        "error_quote": error_quote,
                        "part": m.group(2),
                        "total": m.group(3),
                        "claimed": m.group(5),
                        "verdict": "confirmed",
                        "explanation": f"Арифметика верна: {part} из {total} = {calc:.1f}%.",
                        "suggestion": None,
                    })
        except (InvalidOperation, ValueError):
            pass

    # 2. [более/менее] 19% ... (9 из 36)
    pattern2 = re.compile(
        r"(более|менее|до)?\s*((\d+(?:[\.,]\d+)?)\s*%)[^\n.!?]*?\(?\s*((\d+(?:[\.,]\d+)?)\s*(?:из|от)\s*(\d+(?:[\.,]\d+)?))\s*\)?",
        re.IGNORECASE,
    )
    for m in pattern2.finditer(text):
        try:
            prefix = (m.group(1) or "").strip().lower()
            error_quote = m.group(2).strip()
            claimed = Decimal(m.group(3).replace(",", "."))
            premise_quote = m.group(4).strip()
            part = Decimal(m.group(5).replace(",", "."))
            total = Decimal(m.group(6).replace(",", "."))
            if total > 0:
                calc = (part / total) * Decimal(100)
                if prefix == "более":
                    ok = calc >= claimed
                elif prefix in ("менее", "до"):
                    ok = calc <= claimed
                else:
                    ok = abs(calc - claimed) <= Decimal("0.6")
                if not ok:
                    findings.append({
                        "span": (m.start(2), m.end(2)),
                        "quote": m.group(0),
                        "premise_quote": premise_quote,
                        "error_quote": error_quote,
                        "part": m.group(5),
                        "total": m.group(6),
                        "claimed": m.group(3),
                        "verdict": "refuted",
                        "explanation": f"Арифметическая ошибка: {part} из {total} = {calc:.1f}%, что не согласуется с «{m.group(0)}».",
                        "suggestion": None,
                    })
                else:
                    findings.append({
                        "span": (m.start(2), m.end(2)),
                        "quote": m.group(0),
                        "premise_quote": premise_quote,
                        "error_quote": error_quote,
                        "part": m.group(5),
                        "total": m.group(6),
                        "claimed": m.group(3),
                        "verdict": "confirmed",
                        "explanation": f"Арифметика верна: {part} из {total} = {calc:.1f}%.",
                        "suggestion": None,
                    })
        except (InvalidOperation, ValueError):
            pass

    return findings


def check_average_ratio_arithmetic(text: str) -> list[dict]:
    """Сверяет средние величины строго в пределах одного или двух соседних предложений одного абзаца."""
    findings: list[dict] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        para_clean = para.strip()
        if not para_clean:
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", para_clean)
        windows = []
        for i in range(len(sentences)):
            windows.append(sentences[i])
            if i + 1 < len(sentences):
                windows.append(f"{sentences[i]} {sentences[i+1]}")

        for window in windows:
            for m_avg in re.finditer(
                r"(?:средн\w*\s+стоимост\w*|в\s+среднем)[^\d\n]*(\d+(?:[\.,]\d+)?)\s*(млн|тыс|рубл)?",
                window,
                re.IGNORECASE,
            ):
                try:
                    claimed_val = Decimal(m_avg.group(1).replace(",", "."))
                    m_total = re.search(r"(\d+(?:[\.,]\d+)?)\s*(млн|тыс|млрд)?\s*рубл", window, re.IGNORECASE)
                    m_count = None
                    for cand in re.finditer(r"\b(\d+)\s+([а-яёa-z]{3,})\b", window, re.IGNORECASE):
                        w_cf = cand.group(2).casefold()
                        if w_cf.startswith(("млн", "тыс", "млрд", "рубл", "год", "лет", "процент")):
                            continue
                        if _has_noun(w_cf):
                            m_count = cand
                            break
                    if m_total and m_count:
                        t_val = Decimal(m_total.group(1).replace(",", "."))
                        c_val = Decimal(m_count.group(1))
                        unit_total = (m_total.group(2) or "").lower()
                        unit_avg = (m_avg.group(2) or "").lower()
                        if unit_total == "млн" and unit_avg == "тыс":
                            t_val *= Decimal("1000")
                        elif unit_total == "млрд" and unit_avg == "млн":
                            t_val *= Decimal("1000")

                        if c_val > 0 and t_val != claimed_val:
                            calc = t_val / c_val
                            if abs(calc - claimed_val) > Decimal("0.1"):
                                findings.append({
                                    "span": (m_avg.start(), m_avg.end()),
                                    "quote": m_avg.group(0),
                                    "verdict": "refuted",
                                    "explanation": f"Арифметическая ошибка в среднем: {t_val} / {c_val} = {calc:.1f}, а утверждается {claimed_val}.",
                                    "suggestion": m_avg.group(0).replace(m_avg.group(1), f"{calc:.1f}"),
                                })
                            else:
                                findings.append({
                                    "span": (m_avg.start(), m_avg.end()),
                                    "quote": m_avg.group(0),
                                    "verdict": "confirmed",
                                    "explanation": f"Арифметика верна: {t_val} / {c_val} = {calc:.1f}.",
                                    "suggestion": None,
                                })
                except (InvalidOperation, ValueError):
                    pass
    return findings


def check_mean_as_range_error(text: str) -> list[dict]:
    """Ловит логическую ошибку: среднее значение — точечная величина и не может быть широким интервалом 'от X до Y'.

    Не обвиняет, если речь идёт о диапазоне средних по группам/факультетам, доверительном интервале
    или если диапазон относится к другому показателю (проходной/конкурсный балл).
    """
    findings: list[dict] = []
    pattern = re.compile(
        r"((?:средн(?:ий|ие|его|ему|им|их)\s+(?:конкурсн\w+\s+)?(?:балл\w*|значен\w*|показател\w*|результат\w*))"
        r"([^.!?;\n]{0,140}?\bот\s+(\d+(?:[\.,]\d+)?)\s+до\s+(\d+(?:[\.,]\d+)?)(?:\s*(?:балл\w*|%|процент\w*|рубл\w*|₽|человек\w*))?))",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        full_match = m.group(1).strip()
        middle = m.group(2)
        low_str = m.group(3)
        high_str = m.group(4)
        low = Decimal(low_str.replace(",", "."))
        high = Decimal(high_str.replace(",", "."))

        # Проверка 1: есть ли у среднего своё точечное значение (например, "средний балл составил 75, а ... от 150 до 220")
        ot_m = re.search(r"\bот\s+", middle)
        pre_ot = middle[:ot_m.start()] if ot_m else middle
        has_separate_point_value = bool(re.search(r"\b\d+(?:[\.,]\d+)?\b", pre_ot))

        # Проверка 2: относится ли диапазон к другому показателю между средним и "от X до Y"
        has_other_metric = bool(
            re.search(
                r"\b(?:проходн\w+|конкурсн\w+|минимальн\w+|максимальн\w+|порогов\w+|разброс\w*)\s+(?:балл\w*|значен\w*|показател\w*|число)?",
                middle,
                re.IGNORECASE,
            )
        )

        if has_separate_point_value or has_other_metric:
            # Два разных показателя — не ошибка приписывания диапазона среднему
            continue

        # Проверка 3: разбивка по группам/факультетам или доверительный интервал
        has_group_or_interval = bool(
            re.search(
                r"\b(?:по\s+(?:разным|отдельным|всем|факультет\w*|направлен\w*|школ\w*|групп\w*|класс\w*|регион\w*|вуз\w*|кафедр\w*|профил\w*|предмет\w*)|"
                r"в\s+зависимости\s+от|доверительн\w*\s+интервал\w*|с\s+разбросом)\b",
                full_match,
                re.IGNORECASE,
            )
        )

        if has_group_or_interval:
            findings.append({
                "type": "mean_as_range_error",
                "span": (m.start(), m.end()),
                "quote": full_match,
                "claimed": f"от {low_str} до {high_str}",
                "actual": "диапазон средних значений по группам/интервал",
                "verdict": "clarify",
                "explanation": (
                    f"Указан диапазон значений («от {low_str} до {high_str}») с разбивкой по категориям "
                    f"или доверительным интервалом. Требует уточнения контекста."
                ),
                "suggestion": None,
            })
            continue

        if high > low and (high - low) >= Decimal("10"):
            findings.append({
                "type": "mean_as_range_error",
                "span": (m.start(), m.end()),
                "quote": full_match,
                "claimed": f"от {low_str} до {high_str}",
                "actual": "диапазон баллов",
                "verdict": "refuted",
                "explanation": (
                    f"Логическая ошибка формулировки: среднее значение является точечной величиной и не может "
                    f"быть диапазоном вида «от {low_str} до {high_str}». Речь идёт о диапазоне баллов."
                ),
                "suggestion": re.sub(
                    r"средн(?:ий|ие|его|ему|им|их)\s+балл(\w*)",
                    r"конкурсные балл\1",
                    full_match,
                    count=1,
                    flags=re.IGNORECASE,
                ),
            })
        elif high > low:
            findings.append({
                "type": "mean_as_range_error",
                "span": (m.start(), m.end()),
                "quote": full_match,
                "claimed": f"от {low_str} до {high_str}",
                "actual": "узкий интервал",
                "verdict": "clarify",
                "explanation": (
                    f"Среднее значение представлено узким интервалом «от {low_str} до {high_str}». "
                    f"Требует уточнения первоисточника."
                ),
                "suggestion": None,
            })
    return findings


_SCALAR_STAT_HEAD_RE = re.compile(
    r"\b(средн(?:ий|ие|его|ему|им|их|яя|юю|ей|ее)\s+(?:конкурсн\w+\s+|проходн\w+\s+|экзаменационн\w+\s+)?"
    r"(?:балл\w*|возраст\w*|показател\w*|размер\w*|уров\w*|результат\w*))",
    re.IGNORECASE,
)

_VARIATION_EXEMPTION_RE = re.compile(
    r"\b(?:в\s+среднем|около|примерно|варьир\w+|колебл\w+|колебал\w+|"
    r"в\s+зависимости\s+от|по\s+разн\w+\s+(?:направлен\w+|предмет\w+|программ\w+|регион\w+|год\w*)|"
    r"от\s+минимальн\w+\s+до\s+максимальн\w+)\b",
    re.IGNORECASE,
)


def check_scalar_statistic_as_range(text: str) -> list[dict]:
    """Ловит математическую ошибку типа: точечное среднее значение задано широким интервалом 'от A до B'
    при отсутствии маркеров варьирования по разным выборкам.
    """
    findings: list[dict] = []
    for sent_match in re.finditer(r"[^.!?\n]+[.!?]?", text):
        sent = sent_match.group(0)
        head_m = _SCALAR_STAT_HEAD_RE.search(sent)
        if not head_m:
            continue
        if _VARIATION_EXEMPTION_RE.search(sent):
            continue
        for rng_m in re.finditer(
            r"(?:составил\w*|равен|равн\w+|оказал\w*|[—–-])\s*[^.!?;\n]{0,40}?\bот\s+(\d+(?:[\.,]\d+)?)\s+до\s+(\d+(?:[\.,]\d+)?)(?:\s*(?:балл\w*|%|процент\w*|рубл\w*|₽|человек\w*))?",
            sent,
            re.IGNORECASE,
        ):
            low = Decimal(rng_m.group(1).replace(",", "."))
            high = Decimal(rng_m.group(2).replace(",", "."))
            if high <= low:
                continue
            span_diff = high - low
            midpoint = (high + low) / Decimal(2)
            rel_spread = span_diff / midpoint if midpoint > 0 else Decimal(0)
            if span_diff >= Decimal("10") and rel_spread > Decimal("0.35"):
                s_start = sent_match.start() + min(head_m.start(), rng_m.start())
                s_end = sent_match.start() + rng_m.end()
                quote = text[s_start:s_end].strip(" ,.;:—–-")
                findings.append({
                    "type": "scalar_statistic_as_range",
                    "verdict": "refuted",
                    "span": (s_start, s_end),
                    "quote": quote,
                    "error_quote": quote,
                    "premise_quote": "",
                    "claimed": f"от {rng_m.group(1)} до {rng_m.group(2)}",
                    "actual": "точечное среднее либо диапазон баллов",
                    "explanation": (
                        f"Математическая ошибка: среднее значение — это одно число (точечный показатель), "
                        f"оно не может быть широким интервалом «от {rng_m.group(1)} до {rng_m.group(2)}». "
                        f"Вероятно, имелся в виду диапазон конкурсных/проходных значений."
                    ),
                    "suggestion": re.sub(
                        r"средн(?:ий|ие|его|ему|им|их|яя|юю|ей|ее)\s+",
                        "конкурсные ",
                        quote,
                        count=1,
                        flags=re.IGNORECASE,
                    ),
                })
    return findings


def _scan_delta_and_parts(text: str) -> list[dict]:
    """Сканирует приросты (с A до B на C / P%) и суммы слагаемых подкатегорий."""
    items: list[dict] = []
    pattern_delta = re.compile(
        r"с\s+(\d+(?:[\.,]\d+)?)\s+до\s+(\d+(?:[\.,]\d+)?)[^\n.!?]*?\bна\s+(\d+(?:[\.,]\d+)?|[а-яёa-z\-]+)\s*([а-яёa-z]+)?(?:[^\n.!?]*?(менее|более)?\s*чем\s*на\s*(\d+(?:[\.,]\d+)?)\s*%)?",
        re.IGNORECASE,
    )
    for m in pattern_delta.finditer(text):
        try:
            a = Decimal(m.group(1).replace(",", "."))
            b = Decimal(m.group(2).replace(",", "."))
            raw_c = m.group(3).strip()
            parsed_c = words_to_num(raw_c)
            if parsed_c is not None:
                c = Decimal(parsed_c)
            else:
                root_c = _NUMERAL_ROOTS.get(raw_c.casefold().replace("ё", "е"))
                c = root_c if root_c is not None else Decimal(raw_c.replace(",", "."))
            diff = b - a
            is_diff_ok = (diff == c)
            pct_ok = True
            if m.group(6) is not None:
                claimed_pct = Decimal(m.group(6).replace(",", "."))
                calc_pct = (diff / a) * Decimal(100)
                comp = (m.group(5) or "").lower()
                if comp == "менее":
                    pct_ok = calc_pct <= claimed_pct
                elif comp == "более":
                    pct_ok = calc_pct >= claimed_pct
                else:
                    pct_ok = abs(calc_pct - claimed_pct) <= Decimal("0.5")

            if is_diff_ok and pct_ok:
                items.append({
                    "span": (m.start(), m.end()),
                    "quote": m.group(0),
                    "consistent": True,
                })
            else:
                items.append({
                    "span": (m.start(), m.end()),
                    "quote": m.group(0),
                    "consistent": False,
                    "verdict": "refuted",
                    "explanation": f"Ошибка расчёта прироста: с {a} до {b} — это изменение на {diff}, а не {c}.",
                    "derived_verified": False,
                })
        except Exception:
            pass

    pattern_parts = re.compile(
        r"всего[^\d\n]{0,30}(\d+)[^\n.!?]*?из\s+них[^\d\n]{0,30}(\d+)[^\n.!?]*?(?:и|а)[^\d\n]{0,30}(\d+)",
        re.IGNORECASE,
    )
    for m in pattern_parts.finditer(text):
        try:
            total = int(m.group(1))
            p1 = int(m.group(2))
            p2 = int(m.group(3))
            if p1 + p2 == total:
                items.append({
                    "span": (m.start(), m.end()),
                    "quote": m.group(0),
                    "consistent": True,
                })
            else:
                items.append({
                    "span": (m.start(), m.end()),
                    "quote": m.group(0),
                    "consistent": False,
                    "verdict": "refuted",
                    "explanation": f"Ошибка суммы слагаемых: {p1} + {p2} = {p1+p2}, а заявлено {total}.",
                    "derived_verified": False,
                })
        except Exception:
            pass

    return items


def check_delta_and_parts_consistency(text: str) -> list[dict]:
    """Проверяет приросты и суммы слагаемых; возвращает находки только при арифметической ошибке."""
    return [
        {k: v for k, v in item.items() if k != "consistent"}
        for item in _scan_delta_and_parts(text)
        if not item["consistent"]
    ]


def check_presupposition_shift(text: str) -> list[dict]:
    """Ловит смысловую пресуппозицию: гипотеза ('похожие на X') -> факт ('какие именно X ..., неизвестно').
    Поиск ограничен предложением утверждения и соседними предложениями.
    """
    findings: list[dict] = []
    hypothetical_re = re.compile(
        r"(\b(?:похож\w*|напоминающ\w*|предположительн\w*|видим\w*)\s+(?:на\s+)?(?:[а-яёa-z\-]+\s+){0,2}([а-яёa-z\-]{4,}))",
        re.IGNORECASE,
    )
    fact_presupposition_re = re.compile(
        r"(какие\s+именно\s+([а-яёa-z\-]{4,})[^.!?\n]*неизвестн\w*)",
        re.IGNORECASE,
    )

    sentence_spans = []
    for m in re.finditer(r"[^.!?…\n]+[.!?…]?", text):
        raw = m.group()
        if raw.strip():
            sentence_spans.append((m.start(), m.end(), raw))

    for idx, (s_start, _, s_text) in enumerate(sentence_spans):
        m_fact = fact_presupposition_re.search(s_text)
        if not m_fact:
            continue
        fact_noun = m_fact.group(2)
        if not _has_noun(fact_noun):
            continue

        adjacent_indices = [i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(sentence_spans)]
        m_hypo = None
        for adj_idx in adjacent_indices:
            for found in hypothetical_re.finditer(sentence_spans[adj_idx][2]):
                if _has_noun(found.group(2)):
                    m_hypo = found
                    break
            if m_hypo:
                break

        if m_hypo:
            span_start = s_start + m_fact.start()
            span_end = s_start + m_fact.end()
            findings.append({
                "type": "presupposition_shift",
                "span": (span_start, span_end),
                "quote": m_fact.group(0),
                "verdict": "clarify",
                "explanation": (
                    f"Смысловая неточность формулировки: соседнее предложение лишь предполагает сходство "
                    f"(«{m_hypo.group(0)}»), тогда как эта фраза утверждает наличие объекта как установленный факт."
                ),
            })
    return findings


def audit_derived_claims(
    text: str,
    claims: list[dict],
    reference_datetime: datetime.datetime | None = None,
) -> list[dict]:
    """Детерминированный аудит утверждений на CPU (календарь, суммы, проценты, средние, нормативные приказы).

    Обновляет метки переданных claims.
    """
    weekday_findings = check_weekday_dates(text, reference_datetime=reference_datetime)
    curriculum_findings = check_curriculum_hours_sum(text)
    order_findings = check_known_education_orders(text, reference_datetime=reference_datetime)
    pct_findings = check_percentage_arithmetic(text)
    avg_findings = check_average_ratio_arithmetic(text)
    range_findings = check_mean_as_range_error(text)
    scalar_range_findings = check_scalar_statistic_as_range(text)
    scalar_spans = [sf["span"] for sf in scalar_range_findings if sf.get("span")]
    range_findings = [
        rf for rf in range_findings
        if not rf.get("span") or not any(
            max(rf["span"][0], ss[0]) < min(rf["span"][1], ss[1]) for ss in scalar_spans
        )
    ]
    delta_items = _scan_delta_and_parts(text)
    delta_findings = [
        {k: v for k, v in item.items() if k != "consistent"}
        for item in delta_items
        if not item["consistent"]
    ]
    consistent_delta_spans = [item["span"] for item in delta_items if item["consistent"]]
    presupposition_findings = check_presupposition_shift(text)

    for claim in claims:
        c_start, c_end = claim.get("start"), claim.get("end")
        c_text = claim.get("text", "")
        for ds, de in consistent_delta_spans:
            if (c_start is not None and c_end is not None and max(c_start, ds) < min(c_end, de)) or (
                text[ds:de] in c_text
            ):
                claim["in_text_arithmetic_consistent"] = True
                break

    all_findings = [
        *weekday_findings,
        *curriculum_findings,
        *order_findings,
        *pct_findings,
        *avg_findings,
        *range_findings,
        *scalar_range_findings,
        *delta_findings,
        *presupposition_findings,
    ]

    claims_to_add: list[dict] = []

    for claim in claims:
        c_text = claim.get("text", "")
        start = claim.get("start")
        end = claim.get("end")

        for f in all_findings:
            matched = False

            # Специальная обработка процентных расчётов с посылкой и выводом:
            if f.get("error_quote") and f.get("premise_quote"):
                err_q = f["error_quote"]
                prem_q = f["premise_quote"]
                if err_q in c_text and prem_q in c_text:
                    # Атом объединил посылку и процент ("303 из 344 — это 93%" или "303 из 344 — это 88%")
                    # Разрезаем: посылку защищаем как посылку (Label.clarify), а процент получает вердикт вычисления
                    f_span = f.get("span")
                    p_start, p_end = None, None
                    if start is not None and end is not None and f_span and start <= f_span[0] < f_span[1] <= end:
                        p_start, p_end = f_span
                    elif start is not None and end is not None:
                        idx = text.find(err_q, start)
                        if idx != -1 and idx + len(err_q) <= end:
                            p_start, p_end = idx, idx + len(err_q)

                    if p_start is not None and p_end is not None:
                        premise_end = p_start
                        while premise_end > start and text[premise_end - 1] in " —–-:,;.":
                            premise_end -= 1

                        premise_part = text[start:premise_end].strip() or prem_q
                        err_part = text[p_start:p_end].strip() or err_q
                        tail_part = text[p_end:end].strip(" ,.;:—–-")

                        claim["text"] = premise_part
                        claim["start"] = start
                        claim["end"] = premise_end
                        claim["is_arithmetic_premise"] = True
                        claim["label"] = Label.clarify
                        claim["explanation"] = f"Исходная посылка расчёта: {prem_q}."

                        is_refuted = (f.get("verdict") == "refuted")
                        claims_to_add.append({
                            "text": err_part,
                            "start": p_start,
                            "end": p_end,
                            "kind": claim.get("kind", "web_checkable"),
                            "type": "numeric",
                            "importance": "high",
                            "entity_cluster": claim.get("entity_cluster", "arithmetic"),
                            "query": "",
                            "label": Label.error if is_refuted else Label.confirmed,
                            "explanation": f["explanation"],
                            "suggestion": f.get("suggestion"),
                            "derived_verified": not is_refuted,
                        })

                        if tail_part:
                            claims_to_add.append({
                                "text": tail_part,
                                "start": p_end,
                                "end": end,
                                "kind": claim.get("kind", "web_checkable"),
                                "type": "numeric",
                                "importance": "medium",
                                "entity_cluster": claim.get("entity_cluster", "default-cluster"),
                                "query": "",
                                "label": Label.clarify,
                                "explanation": "Сведения требуют независимой проверки.",
                                "suggestion": None,
                            })
                        continue
                    else:
                        m_sep = re.search(r"(\s*(?:—|-|это)\s*" + re.escape(err_q) + r"[^\n.,;]*|\s*\(" + re.escape(err_q) + r"[^\n.,;]*\))", c_text)
                        if m_sep:
                            sep_start = m_sep.start()
                            premise_part = c_text[:sep_start].strip(" ,.;:—–-")
                            err_part = m_sep.group(0).strip(" ,.;:—–-")
                            claim["text"] = premise_part
                            claim["is_arithmetic_premise"] = True
                            claim["label"] = Label.clarify
                            claim["explanation"] = f"Исходная посылка расчёта: {prem_q}."
                            is_refuted = (f.get("verdict") == "refuted")
                            claims_to_add.append({
                                "text": err_part,
                                "start": start,
                                "end": end,
                                "kind": claim.get("kind", "web_checkable"),
                                "type": "numeric",
                                "importance": "high",
                                "entity_cluster": claim.get("entity_cluster", "arithmetic"),
                                "query": "",
                                "label": Label.error if is_refuted else Label.confirmed,
                                "explanation": f["explanation"],
                                "suggestion": f.get("suggestion"),
                                "derived_verified": not is_refuted,
                            })
                            continue
                elif err_q in c_text or (f.get("claimed") and f"{f['claimed']}%" in c_text):
                    matched = True
                elif prem_q in c_text and err_q not in c_text:
                    claim["is_arithmetic_premise"] = True
                    continue

            # Проверяем пересечение по диапазону символов (если позиции известны)
            if not matched and start is not None and end is not None and f.get("span"):
                f_start, f_end = f["span"]
                if max(start, f_start) < min(end, f_end):
                    if f.get("verdict") == "refuted" and (end - start) > (f_end - f_start) + 20:
                        # Атом значительно шире самого ошибочного процента/числа:
                        # Сужаем ошибку строго до f_span, а остальное сохраняем без обвинения
                        err_text = text[f_start:f_end].strip()
                        lead_text = text[start:f_start].strip(" ,.;:—–-")
                        trail_text = text[f_end:end].strip(" ,.;:—–-")

                        claim["text"] = lead_text
                        claim["end"] = f_start
                        claim["label"] = Label.clarify
                        claim["is_arithmetic_premise"] = True
                        claim["explanation"] = "Исходная посылка расчёта: противоречие не доказано."

                        claims_to_add.append({
                            "text": err_text,
                            "start": f_start,
                            "end": f_end,
                            "kind": claim.get("kind", "web_checkable"),
                            "type": "numeric",
                            "importance": "high",
                            "entity_cluster": claim.get("entity_cluster", "arithmetic"),
                            "query": "",
                            "label": Label.error,
                            "explanation": f["explanation"],
                            "suggestion": f.get("suggestion"),
                            "derived_verified": False,
                        })

                        if trail_text:
                            claims_to_add.append({
                                "text": trail_text,
                                "start": f_end,
                                "end": end,
                                "kind": claim.get("kind", "web_checkable"),
                                "type": "numeric",
                                "importance": "medium",
                                "entity_cluster": claim.get("entity_cluster", "default-cluster"),
                                "query": "",
                                "label": Label.clarify,
                                "explanation": "Сведения требуют независимой проверки.",
                                "suggestion": None,
                            })
                        continue
                    matched = True

            # Проверяем вхождение цитаты
            if not matched and f.get("quote"):
                if f["quote"] in c_text or c_text in f["quote"]:
                    matched = True

            # Проверяем совпадение числа процента для коротких атомов в том же предложении
            if not matched and f.get("claimed"):
                claimed_pct = f"{f['claimed']}%"
                if claimed_pct in c_text or (f["claimed"] in c_text and "%" in c_text):
                    if f.get("span") and start is not None:
                        if abs(start - f["span"][0]) < 200:
                            matched = True

            if matched:
                if f.get("type") == "weekday_verified":
                    claim["calendar_weekday_verified"] = True
                if f.get("verdict") == "refuted":
                    claim["label"] = Label.error
                    claim["explanation"] = f["explanation"]
                    claim["suggestion"] = f.get("suggestion")
                    claim["derived_verified"] = False
                    break
                elif f.get("verdict") == "confirmed":
                    claim["label"] = Label.confirmed
                    claim["explanation"] = f["explanation"]
                    claim["derived_verified"] = True
                    break
                elif f.get("verdict") == "clarify":
                    claim["label"] = Label.clarify
                    claim["explanation"] = f["explanation"]
                    break

    if claims_to_add:
        claims.extend(claims_to_add)

    # Если детерминированный аудит на CPU выявил доказанную ошибку (refuted),
    # но модель не выделила этот фрагмент в отдельное утверждение,
    # добавляем его как самостоятельный Claim со статусом Label.error
    for f in all_findings:
        if f.get("verdict") == "refuted":
            f_span = f.get("span")
            if f_span and f_span[1] > f_span[0] and (f_span[1] - f_span[0]) < len(text):
                has_exact_error = any(
                    c.get("label") == Label.error
                    and c.get("start") is not None
                    and max(c["start"], f_span[0]) < min(c["end"], f_span[1])
                    for c in claims
                )
                if not has_exact_error:
                    err_text = f.get("error_quote") or f.get("quote") or text[f_span[0]:f_span[1]]
                    claims.append({
                        "text": err_text,
                        "start": f_span[0],
                        "end": f_span[1],
                        "kind": "web_checkable",
                        "type": "numeric",
                        "importance": "high",
                        "entity_cluster": "arithmetic",
                        "query": "",
                        "label": Label.error,
                        "explanation": f["explanation"],
                        "suggestion": f.get("suggestion"),
                        "derived_verified": False,
                    })

    return claims

