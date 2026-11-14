"""Доказательства и вердикт по одному утверждению.

1. Код находит в загруженных документах предложения-кандидаты (лексически).
2. Модель выбирает кандидатов ПО НОМЕРУ и ставит вердикт — сама цитату не пишет,
   поэтому выдумать цитату не может.
3. Гарды после модели: вердикт без доказательства, опровержение без противоречия или
   не подтверждённое вторым вопросом — всё сводится к unresolved. Ложное обвинение
   хуже пропуска: на замерах подготовки ложных подтверждений 0, поймано 8–9 ошибок из 12.
"""

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from zoneinfo import ZoneInfo

from bormochecker import llm
from bormochecker.contract import Label
from bormochecker.costs import Meter
from bormochecker.numerals import (
    Quantity,
    extract_quantities,
    is_typo_or_minor_numeric_diff,
    parse_cardinal,
    resolve_target_years,
)
from bormochecker.sources import Doc

VERDICTS = {"supported", "partially_supported", "contradicted", "unresolved"}
EVIDENCE_REQUIRED = {"supported", "partially_supported", "contradicted"}
RELATIONS = {"supports", "partially_supports", "contradicts"}
MAX_CANDIDATES = 8
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
SHORT_FRAGMENT = 40
CONTEXT_LINES = 2
OTHER_NUMBER_BONUS = 0.3
SAME_ORDER_BONUS = 0.8
TEMPORAL_KEYWORD_BONUS = 0.5
# «до 30 сентября» против «с 12 по 27 сентября»: тот же месяц, другое число — кандидат на
# опровержение сильнее однотемных фраз, в которых слов общих больше, а дат нет
_DATE_RE = re.compile(r"(\d{1,2})\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
                      re.IGNORECASE)

PROMPT = """Ты — фактчекер. Проверь утверждение ТОЛЬКО по фрагментам источников ниже.
Своим знаниям не доверяй: чего нет во фрагментах — того нет.
Сегодня {today}. Относительные даты утверждения («в прошлом году», «в субботу»)
считай от этой даты: «в прошлом году» при сегодняшнем {year} — это {last_year}.

Утверждение: «{claim}»

Фрагменты:
{candidates}

ПРАВИЛА ОЦЕНКИ ВЕРДИКТОВ:

1. contradicted (ОПРОВЕРГНУТО):
   Фрагмент источника говорит о ТОМ ЖЕ событии/объекте/проекте, но называет ДРУГОЕ значение (число, дата, цена, этажность, юбилей, вес, срок):
   - в утверждении «15 этажей», а в источнике «12 этажей» → contradicted!
   - в утверждении «20 марта», а в источнике «22 марта» → contradicted!
   - в утверждении «500 рублей», а в источнике «700 рублей» → contradicted!
   - в утверждении «50-летие», а в источнике «40-летие» → contradicted!
   - в утверждении «300 экспонатов», а в источнике «около 200 экспонатов» → contradicted!
   - в утверждении «до 20 ноября», а в источнике «по 15 ноября» → contradicted!
   - в утверждении «2 часа», а в источнике «3 часа» → contradicted!
   ПРИОРИТЕТ: если ХОТЯ БЫ ОДНА конкретная деталь (дата, цена, число, этаж) опровергнута источником — вердикт ВСЕГДА contradicted (опровержение искаженной детали важнее неполноты остальных деталей)!
   Если в источнике для того же предмета названо ДРУГОЕ число или дата — это ПРЯМОЕ ОПРОВЕРЖЕНИЕ (contradicted), а НЕ «деталь не найдена» (unresolved)!

2. supported (ПОДТВЕРЖДЕНО):
   Ключевые факты, числа, даты, имена полностью подтверждаются источником.
   «около 200» = «200», «45 минут» = «45-минутные», «12 апреля 2026» = «в воскресенье 12-го»; указание конкретного зала или помещения внутри того же здания подтверждает то же место, а не ограничивает его. Если источник подтверждает всё сказанное в утверждении и лишь добавляет дополнительные подробности (дату, время, участников), отсутствующие в самом утверждении — ставь supported, а не partially_supported.

3. partially_supported (ЧАСТИЧНО ПОДТВЕРЖДЕНО):
   - Расхождение модальности/статуса: в источнике «планируется/согласована концепция/проект», а в утверждении «построят/построили/принят закон».
   - Слишком широкое обобщение области: если в источнике число (например, 10 тыс. заявок или 80% мест) относится только к одной категории или уровню обучения, а в статье оно явно приписано всей организации или общему итогу целиком словами «общее количество», «всего», «всех заявлений» или «по всем направлениям», ставь partially_supported и укажи в reason, что цифру необходимо ограничить соответствующей категорией; если таких обобщающих слов в утверждении нет и источник подтверждает указанный факт или рост — ставь supported.
   - Конфликт в официальных источниках: если в официальных документах названы разные числа по одному показателю, ставь partially_supported с указанием обеих версий.

4. unresolved (НЕ УСТАНОВЛЕНО):
   Во фрагментах ВООБЩЕ НЕТ упоминания данного показателя (например, фрагмент описывает архитектуру, но этажность вообще не названа), либо данных объективно недостаточно. Не нашли ≠ опровергли.

5. suggestion — безопасная правка формулировки, только если вердикт contradicted или clarify, иначе null.
   Новое значение — только то, что стоит во фрагменте источника.

6. Многозначные слова:
   Для многозначных слов (глаголов и существительных с несколькими значениями) значение определяй строго по ближайшему контексту; если контекст не позволяет однозначно установить точное значение или допускает двусмысленность — ставь вердикт unresolved (или clarify).

Верни ТОЛЬКО JSON без markdown:
{{"comparison_note": "краткая сверка дат, чисел, кванторов", "verdict": "...",
 "evidence": [{{"n": 1, "relation": "supports|partially_supports|contradicts"}}],
 "evidence_strength": "low|medium|high", "reason": "одно-два предложения", "limitations": ["..."],
 "suggestion": "правка или null"}}"""

CONFIRMATION_CHECK_PROMPT = """Проверь, действительно ли цитата из источника ПОЛНОСТЬЮ подтверждает утверждение без домыслов.
Момент проверки: {note}
Утверждение из текста: «{claim}»
Документ-источник: {source_title}
URL источника: {source_url}
Цитата из источника: «{quotes}»

Строгие правила подтверждения:
1. Частный источник может подтвердить общее утверждение только в своей части; в этом случае метка clarify с указанием, что подтверждено частично.
2. Запрещено подтверждение по названию без текста: доказательство должно содержаться непосредственно в цитате источника.
3. Для многозначных слов значение определяется строго по ближайшему контексту; если контекст допускает двусмысленность, требуется метка clarify.

Ответь строго по пунктам:
1. same_entity_and_event: Цитата относится к тому же самому объекту, событию, организации и месту?
2. exact_numbers_and_dates: Все ли числа, цены, даты, сроки и условия из утверждения прямо подтверждены цитатой (без подмены 2026 и 2027 года, без подмены цены для одного человека и цены для группы/пары)?
3. modality_matches: Совпадает ли степень совершённости? (Если в источнике только «согласована концепция / проект / планируется», а в утверждении «построили» или безусловное «построят» — это НЕ полное подтверждение, ставь false!).
4. scope_matches: Не расширяет ли утверждение область факта? Частный источник может подтвердить общее утверждение только в своей части (если подтверждение частное — ставь false)."""

CONFIRMATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "factcheck_confirmation_check",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "same_entity_and_event": {"type": "boolean"},
                "exact_numbers_and_dates": {"type": "boolean"},
                "modality_matches": {"type": "boolean"},
                "scope_matches": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": [
                "same_entity_and_event",
                "exact_numbers_and_dates",
                "modality_matches",
                "scope_matches",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}

CONTRADICTION_CHECK_V2 = """Утверждение из статьи: «{claim}»
Контекст/момент проверки: {note}
Фрагменты источников:
{fragments}

Мы собираемся обвинить автора статьи в фактической ОШИБКЕ (🔴). Ложное обвинение недопустимо!
Проверь 4 строгих условия:
1. same_subject: В утверждении и фрагменте речь идёт об одном и том же объекте, событии и показателе? (Если в статье речь про одну часть комплекса, а в источнике про другую, или в статье про конкретное мероприятие, а в источнике про деятельность в целом — это РАЗНЫЕ предметы -> false, если об одном -> true).
2. same_time_and_subset: Совпадает ли временной период и подмножество? (Если в статье сказано «в прошлом году», а в источнике цифры этого года; или в статье «на первом этапе приняли 50 заявок», а в источнике «всего поступило 200 заявок» — это НЕ противоречие -> false, если период и предмет совпадают -> true).
3. mutually_exclusive: Исключают ли данные источника и статьи друг друга физически/математически? (Например: «2 часа» vs «3 часа», «10 мая» vs «12 мая», «12 этажей» vs «24 этажа» для одного и того же события/здания -> true, иначе false).
4. not_just_missing: Фрагмент источника содержит конкретные факты/числа, опровергающие утверждение (а не просто умалчивает о них)? (Если факты приведены -> true, если в источнике факт просто отсутствует -> false).

Только если данные действительно взаимно исключают друг друга — ошибка доказана."""

CONTRADICTION_SCHEMA_V2 = {
    "type": "json_schema",
    "json_schema": {
        "name": "factcheck_contradiction_check_v2",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "same_subject": {"type": "boolean"},
                "same_time_and_subset": {"type": "boolean"},
                "mutually_exclusive": {"type": "boolean"},
                "not_just_missing": {"type": "boolean"},
                "why": {"type": "string"},
            },
            "required": ["same_subject", "same_time_and_subset", "mutually_exclusive", "not_just_missing", "why"],
            "additionalProperties": False,
        },
    },
}

try:


    import pymorphy3
    _morph = pymorphy3.MorphAnalyzer()
except Exception:
    _morph = None

_SENTENCE_ABBREVS = re.compile(
    r"\b(?:г|ул|руб|тыс|млн|млрд|им|д|стр|корп|обл|просп|пер|п|ст|т|д)\.$",
    re.IGNORECASE,
)
_INITIALS_RE = re.compile(r"\b[А-ЯЁA-Z]\.$")
_FRACTION_PERIOD_RE = re.compile(r"\b\d+/\d+\.$")
_URL_SENTENCE_RE = re.compile(r"(?:https?://\S+|[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}/\S*)$")


def split_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Разбивает текст на предложения, не разрезая по точкам внутри дат, дробей, инициалов, сокращений, десятичных чисел и URL."""
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    pattern = re.compile(r"([.!?…]+)(?:\s+|$)")
    for match in pattern.finditer(text):
        punct = match.group(1)
        end_idx = match.end(1)
        prefix = text[start:end_idx].rstrip()

        if punct == ".":
            # Не резать внутри дат или десятичных чисел: следующий символ цифра или слэш
            if match.end(1) < len(text) and text[match.end(1)] in "0123456789/":
                continue
            # Не резать внутри URL
            if _URL_SENTENCE_RE.search(prefix):
                continue
            # Не резать на инициалах (например "А.", "С.")
            if _INITIALS_RE.search(prefix):
                continue
            # Не резать на дробях и счёте ("2020/21." внутри предложения)
            if _FRACTION_PERIOD_RE.search(prefix):
                rest = text[match.end():]
                if rest and rest[0].islower():
                    continue
            # Не резать на сокращениях ("г.", "ул.", "т. д.", "руб.", "тыс.", "им.")
            if _SENTENCE_ABBREVS.search(prefix):
                rest = text[match.end():]
                if rest and (rest[0].islower() or (prefix.endswith("т. д.") and rest[0].islower())):
                    continue
                last_word = prefix.split()[-1].lower() if prefix.split() else ""
                if last_word in ("г.", "ул.", "им.", "д.", "просп.", "пер.", "п.", "ст."):
                    continue

        sent_text = text[start:end_idx].strip()
        if sent_text:
            s_offset = text.find(sent_text, start)
            spans.append((s_offset, s_offset + len(sent_text)))
        start = match.end()

    if start < len(text):
        rest = text[start:].strip()
        if rest:
            s_offset = text.find(rest, start)
            spans.append((s_offset, s_offset + len(rest)))
    return spans


def split_sentences(text: str) -> list[str]:
    """Возвращает список предложений из текста."""
    return [text[s:e] for s, e in split_sentence_spans(text)]


class _SentenceMatch:
    def __init__(self, text: str, start: int, end: int):
        self._text = text
        self._start = start
        self._end = end

    def start(self, group: int = 0) -> int:
        return self._start

    def end(self, group: int = 0) -> int:
        return self._end

    def group(self, group: int = 0) -> str:
        return self._text[self._start : self._end]


class _SentenceSplitter:
    def finditer(self, text: str):
        for start, end in split_sentence_spans(text):
            yield _SentenceMatch(text, start, end)


_SENTENCE_RE = _SentenceSplitter()
_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# Словарь нормализации частых форм для надежного морфологического разбора
_FALLBACK_LEMMA_MAP = {
    "учителям": "учитель",
    "учителя": "учитель",
    "учителей": "учитель",
    "учителями": "учитель",
    "школах": "школа",
    "школы": "школа",
    "школам": "школа",
    "школами": "школа",
    "оклады": "оклад",
    "окладов": "оклад",
    "окладам": "оклад",
    "уроков": "урок",
    "урокам": "урок",
    "уроками": "урок",
    "ученикам": "ученик",
    "учеников": "ученик",
}


_RU_ENDINGS_RE = re.compile(
    r"(?:иями|ыями|ями|ами|ого|его|ому|ему|ыми|ими|ях|ах|ов|ев|ей|ой|ий|ый|ая|яя|ое|ее|ые|ие|"
    r"ам|ям|ом|ем|а|я|о|е|ы|и|у|ю|ь|ъ)$",
    re.IGNORECASE,
)


@lru_cache(maxsize=32768)
def _lemma(word: str) -> str:
    w = word.casefold().replace("ё", "е")
    if _morph is not None and not w.isdigit():
        try:
            return _morph.parse(w)[0].normal_form.replace("ё", "е")
        except Exception:
            pass
    if w in _FALLBACK_LEMMA_MAP:
        return _FALLBACK_LEMMA_MAP[w]
    if len(w) <= 3 or w.isdigit():
        return w
    cleaned = _RU_ENDINGS_RE.sub("", w)
    return cleaned if len(cleaned) >= 2 else w


def _normalize_temporal_and_numeric(text: str) -> str:
    """Приводит часы/минуты и дробные выражения ('1,5 часа', 'полтора часа', '1 час 40 минут')
    к каноническим минутам и сохраняет связь одноразрядных чисел с единицами измерения."""
    extra: list[str] = []
    t = text.casefold().replace("ё", "е")
    if re.search(r"\bполтор[аы]\s+час", t):
        extra.append("90 минут 1_5_часа час")
    for m in re.finditer(r"(\d+)[,.](\d+)\s*час", t):
        mins = round(float(f"{m.group(1)}.{m.group(2)}") * 60)
        extra.append(f"{mins} минут {m.group(1)}_{m.group(2)}_часа час")
    for m in re.finditer(r"(\d+)\s*ч(?:ас[аов]?)?\s*(\d+)\s*мин", t):
        mins = int(m.group(1)) * 60 + int(m.group(2))
        extra.append(f"{mins} минут {m.group(1)}_часа час")
    for m in re.finditer(
        r"\b(\d)\s*(час[аов]?|клас+[аов]?|вар(?:иант)?[аов]?|груп+[аы]?|балл[аов]?|мин(?:ут[аы]?)?)\b",
        t,
    ):
        extra.append(f"{m.group(1)}_{_lemma(m.group(2))} {_lemma(m.group(2))}")
    return f"{text} {' '.join(extra)}" if extra else text


def _extract_cardinal_numbers(text: str) -> set[str]:
    """Извлекает все количественные числа (цифрами и словами) из текста (T216)."""
    nums = set(re.findall(r"\b\d+(?:[\.,]\d+)?\b", text))
    words = _WORD_RE.findall(text.casefold().replace("ё", "е"))
    for w in words:
        if not w.isdigit():
            val = parse_cardinal(w)
            if val is not None:
                nums.add(str(val))
    for i in range(len(words) - 1):
        if not words[i].isdigit() and not words[i + 1].isdigit():
            val2 = parse_cardinal(f"{words[i]} {words[i + 1]}")
            if val2 is not None:
                nums.add(str(val2))
    return nums


def _stems(text: str) -> set[str]:
    enriched = _normalize_temporal_and_numeric(text)
    tokens = _WORD_RE.findall(enriched.casefold().replace("ё", "е"))
    result: set[str] = set()
    for w in tokens:
        if w.isdigit():
            if len(w) >= 2:
                result.add(w)
        else:
            card = parse_cardinal(w)
            if card is not None:
                result.add(str(card))
            if len(w) >= 3:
                result.add(_lemma(w))
    # Дополнительно сохраняем составные маркеры вида '1_час', '9_класс'
    for m in re.finditer(r"\b\d+_[а-яa-z0-9_]+\b", enriched.casefold()):
        result.add(m.group(0))
    return result


def _resolve_source_quote(raw_quote: str, claim_text: str, candidate_text: str) -> str:
    """Гарантирует, что exact_source_quote является реальной подстрокой из candidate_text,
    а не скопированным текстом проверяемого утверждения (фикс бага L4)."""
    q = (raw_quote or "").strip()
    cand = (candidate_text or "").strip()
    if not cand:
        return ""
    # Если цитата дословно есть в источнике и не совпадает один в один с ошибочным утверждением
    if q and q.casefold() in cand.casefold() and q.casefold() != claim_text.strip().casefold():
        if len(cand) <= 360 and not extract_quantities(q) and extract_quantities(cand):
            return cand
        idx = cand.casefold().find(q.casefold())
        return cand[idx : idx + len(q)]
    if len(cand) <= 360:
        return cand
    # Иначе выбираем предложение/строку из candidate_text с максимальным пересечением лемм и чисел с claim_text
    claim_stems = _stems(claim_text)
    best_seg = ""
    best_score = -1
    for seg in re.split(r"(?<=[.!?])\s+|\n+", cand):
        seg_clean = seg.strip()
        if len(seg_clean) < 5:
            continue
        score = len(claim_stems & _stems(seg_clean))
        if score > best_score:
            best_score = score
            best_seg = seg_clean
    return best_seg or cand[:220]


def _annotate_quote_with_page(doc: Doc, quote: str) -> str:
    """Указывает страницу документа в цитате-доказательстве, если в тексте есть маркеры [page: N]."""
    if not quote or not getattr(doc, "text", ""):
        return quote
    doc_text = doc.text
    idx = doc_text.find(quote)
    if idx == -1:
        idx = doc_text.casefold().find(quote.casefold())
    if idx != -1:
        prefix = doc_text[:idx]
        matches = list(re.finditer(r"\[page:\s*(\d+)\]", prefix))
        if matches:
            page_num = matches[-1].group(1)
            marker = f"стр. {page_num}"
            if marker not in quote and f"page: {page_num}" not in quote:
                return f"{quote} (стр. {page_num})"
    return quote


def _dates(text: str) -> set[tuple[str, str]]:
    return {(day.lstrip("0"), month.casefold()[:3].replace("мая", "май")) for day, month in _DATE_RE.findall(text)}


def _extract_temporal_markers(text: str, reference_year: int | None = None) -> dict[str, Any]:
    """Извлекает даты, диапазоны дат, относительные годы и продолжительность."""
    ref_y = reference_year if reference_year is not None else datetime.now(MOSCOW_TZ).year
    t = text.casefold().replace("ё", "е")
    res: dict[str, Any] = {
        "dates": _dates(text),
        "target_year": None,
        "is_relative_past": False,
        "has_date_range": False,
        "has_duration": False,
    }

    if re.search(r"\b(?:в\s+прошлом\s+году|годом\s+ранее|в\s+минувшем\s+году|ранее)\b", t):
        res["target_year"] = ref_y - 1
        res["is_relative_past"] = True
    elif re.search(r"\b(?:в\s+этом\s+году|в\s+нынешнем\s+году|в\s+текущем\s+году)\b", t):
        res["target_year"] = ref_y
    elif re.search(r"\b(?:в\s+следующем\s+году|в\s+будущем\s+году)\b", t):
        res["target_year"] = ref_y + 1

    years = re.findall(r"\b(20\d\d)\b", text)
    if years:
        res["years"] = {int(y) for y in years}
        if not res["target_year"]:
            res["target_year"] = int(years[0])
    else:
        res["years"] = set()

    if re.search(r"\b(?:с\s+\d{1,2}\s+(?:по|до)\s+\d{1,2}|\d{1,2}[\s–-]+\d{1,2}\s+(?:янв|фев|мар|апр|май|мая|июн|июл|авг|сен|окт|ноя|дек)|до\s+\d{1,2}\s+(?:янв|фев|мар|апр|май|мая|июн|июл|авг|сен|окт|ноя|дек)|к\s+\d{1,2}\s+(?:янв|фев|мар|апр|май|мая|июн|июл|авг|сен|окт|ноя|дек))\b", t):
        res["has_date_range"] = True

    if re.search(r"\b(?:\d+[,.]\d+\s*час|полтор\w*\s+час|\d+\s*мин|\d+\s*час\w*\s*\d+\s*мин)\b", t):
        res["has_duration"] = True

    return res


@dataclass(frozen=True, slots=True)
class GuardResult:
    is_valid: bool
    downgraded_label: Label
    reason: str

    @property
    def is_genuine_confirmation(self) -> bool:
        return self.is_valid

    @property
    def is_genuine_contradiction(self) -> bool:
        return self.is_valid


_NEGATION_RE = re.compile(
    r"\b(?:не|нет|нельзя|никогда|никто|ничто|никак\w*|никаких|отсутств\w*|запрещ\w*|отмен\w*|лишен\w*)\b",
    re.IGNORECASE,
)


def _has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text or ""))


_BENIGN_NEG_PHRASES_RE = re.compile(
    r"\bне\s+(?:меша\w+|случайн\w+|редк\w+|тольк\w+|менее|более|просто|говоря|считая|смотря|зависим\w+)\b",
    re.IGNORECASE,
)


_VERB_ENDINGS_RE = re.compile(
    r"(?:ться|ться|тися|ться|ется|ются|ится|атся|ятся|утся|ться|ть|ти|"
    r"ала|ало|али|ал|ила|ило|или|ил|ела|ело|ели|ел|ула|уло|ули|ул|"
    r"лся|лась|лось|лись|ет|ют|ит|ат|ят|ут|ана|ано|аны|ан|ена|ено|ены|ен)$"
)


def _extract_verb_polarities(text: str) -> dict[str, bool]:
    """Возвращает словарь {лемма_глагола: True_если_под_отрицанием} для смысловых глаголов и предикативов."""
    clean = _BENIGN_NEG_PHRASES_RE.sub(" ", (text or "").casefold().replace("ё", "е"))
    tokens = re.findall(r"[а-яa-z]+", clean)
    neg_markers = {"не", "нет", "нельзя", "запрещено", "запрещается", "никогда", "никто"}
    polarities: dict[str, bool] = {}
    for idx, tok in enumerate(tokens):
        if len(tok) < 4 or tok in neg_markers:
            continue
        lemma = _lemma(tok)
        is_verb = False
        if _morph is not None:
            try:
                p = _morph.parse(tok)
                if p and p[0].tag.POS in ("VERB", "INFN", "PRTS", "PRED"):
                    is_verb = True
            except Exception:
                pass
        else:
            m = _VERB_ENDINGS_RE.search(tok)
            if m and m.start() >= 3:
                is_verb = True
                lemma = tok[: m.start()]
        if not is_verb:
            continue
        window_left = tokens[max(0, idx - 2) : idx]
        is_negated = any(w in neg_markers for w in window_left)
        polarities[lemma] = is_negated
    return polarities


def has_predicate_negation_conflict(claim_text: str, quote_text: str) -> bool:
    """Конфликт полярности есть ТОЛЬКО тогда, когда один и тот же глагол-предикат
    присутствует в утверждении и цитате с противоположным знаком отрицания (например, 'закрыли' vs 'не закрыли').
    """
    c_pol = _extract_verb_polarities(claim_text)
    q_pol = _extract_verb_polarities(quote_text)
    shared_verbs = set(c_pol.keys()) & set(q_pol.keys())
    if not shared_verbs:
        return False
    return any(c_pol[v] != q_pol[v] for v in shared_verbs)


def _verify_confirmation(
    claim_text: str,
    selected_quote: str,
    today_year: int | None = None,
) -> GuardResult:
    year = today_year if today_year is not None else datetime.now(MOSCOW_TZ).year
    if has_predicate_negation_conflict(claim_text, selected_quote):
        return GuardResult(
            is_valid=False,
            downgraded_label=Label.clarify,
            reason="Полярность утверждения (наличие/отсутствие отрицания) не совпадает с цитатой источника.",
        )
    claim_nums = _extract_cardinal_numbers(claim_text)
    quote_nums = _extract_cardinal_numbers(selected_quote)
    temp_markers = _extract_temporal_markers(claim_text, year)
    target_year = temp_markers.get("target_year")
    if target_year:
        quote_years = {int(y) for y in re.findall(r"\b(20\d\d)\b", selected_quote)}
        if quote_years and target_year not in quote_years:
            return GuardResult(
                is_valid=False,
                downgraded_label=Label.clarify,
                reason=f"Год в утверждении ({target_year}) не совпадает с подтверждающим фрагментом ({quote_years}).",
            )
    sig_claim_nums = {n for n in claim_nums if len(n) >= 2 or (n.isdigit() and int(n) not in (0, 1))}
    sig_quote_nums = {n for n in quote_nums if len(n) >= 2 or (n.isdigit() and int(n) not in (0, 1))}
    if sig_claim_nums and not (sig_claim_nums & sig_quote_nums):
        return GuardResult(
            is_valid=False,
            downgraded_label=Label.clarify,
            reason="Конкретные числа/показатели утверждения отсутствуют в выбранных цитатах источника.",
        )
    return GuardResult(is_valid=True, downgraded_label=Label.confirmed, reason="Подтверждено")


_SUBSET_LIMITER_RE = re.compile(
    r"\b(?:на\s+[а-яёa-z0-9_-]+\s+этапе|"
    r"в(?:о)?\s+[а-яёa-z0-9_-]+\s+(?:туре|раунде|потоке|периоде|волне)|"
    r"по\s+[а-яёa-z0-9_-]+\s+квоте|"
    r"с(?:о)?\s+[а-яёa-z0-9_-]+\s+(?:попытки|раза)|"
    r"к\s+\d{1,2}\s+[а-яё]+)\b",
    re.IGNORECASE,
)


def _verify_contradiction(
    claim_text: str,
    selected_quote: str,
    note: str = "",
    today_year: int | None = None,
    is_arithmetic_premise: bool = False,
    has_official_conflict: bool = False,
    conflict_reason: str = "",
) -> GuardResult:
    year = today_year if today_year is not None else datetime.now(MOSCOW_TZ).year
    temp_markers = _extract_temporal_markers(claim_text, year)
    if temp_markers.get("is_relative_past"):
        contra_quote_cf = selected_quote.casefold()
        if not any(w in contra_quote_cf for w in ("прошлом году", "годом ранее", "ранее", str(year - 1))):
            return GuardResult(
                is_valid=False,
                downgraded_label=Label.clarify,
                reason="В источнике приведены данные за текущий период, данных за прошлый год для сверки в источнике нет.",
            )

    if _SUBSET_LIMITER_RE.search(claim_text):
        contra_quote_cf = selected_quote.casefold()
        if not _SUBSET_LIMITER_RE.search(contra_quote_cf):
            return GuardResult(
                is_valid=False,
                downgraded_label=Label.clarify,
                reason="В источнике приведена общая цифра без разбивки по этапам/квотам: противоречие не доказано.",
            )

    if is_arithmetic_premise:
        return GuardResult(
            is_valid=False,
            downgraded_label=Label.clarify,
            reason="Утверждение является исходной посылкой расчёта: противоречие не доказано.",
        )

    if has_official_conflict:
        return GuardResult(
            is_valid=False,
            downgraded_label=Label.clarify,
            reason=conflict_reason or "В официальных документах обнаружен конфликт данных: разные числа в официальных источниках.",
        )

    return GuardResult(is_valid=True, downgraded_label=Label.error, reason="Опровергнуто")


_SUBSET_OR_STAGE_MODIFIERS = (
    "с первого раза",
    "на первом этапе",
    "в первом туре",
    "предварительн",
    "по отдельной квоте",
    "по целевой квоте",
    "очно-заочн",
    "заочн",
    "стилобат",
)

_PLAN_MODALITY_MARKERS = (
    "согласована концепция",
    "архитектурную концепцию",
    "концепци",
    "проект",
    "планируется построить",
    "планируют построить",
    "планируется",
    "планируют",
    "предлагается",
    "обсуждается",
    "намерены",
    "может появиться",
)

_COMPLETED_PAST_VERBS = (
    "построили",
    "построят",
    "возвели",
    "возведут",
    "создали",
    "создадут",
    "открыли",
    "откроют",
    "завершили",
    "ввели в эксплуатацию",
    "приняли закон",
    "утвердили окончательно",
)

_NOVELTY_CLAIM_RE = re.compile(
    r"\b(?:нов\w+\s+(?:альбом\w*|сингл\w*|трек\w*|релиз\w*|песн\w*|книг\w*|фильм\w*|программ\w*)|"
    r"недавно\s+(?:выпустил\w*|вышел\w*|вышла|вышли|состоял\w*))\b",
    re.IGNORECASE,
)

_RELATIVE_YEAR_CLAIM_RE = re.compile(
    r"\b(?:в\s+этом\s+году|в\s+текущ\w+\s+году|в\s+нынешн\w+\s+году|годом\s+ранее|в\s+прошл\w+\s+году)\b",
    re.IGNORECASE,
)


def verify_confirmation_cpu(
    claim_text: str,
    selected_quote: str,
    doc_title: str = "",
    reference_datetime: date | datetime | None = None,
    *,
    is_derived_verified: bool = False,
    calendar_weekday_verified: bool = False,
    doc_text: str = "",
) -> GuardResult:
    """Мгновенный (0.1 мс) детерминированный CPU-барьер против False Confirmations (Алгоритм 2 и 5)."""
    full_evidence = f"{doc_title}. {selected_quote}".strip() if doc_title else selected_quote
    if not selected_quote.strip():
        return GuardResult(False, Label.clarify, "Отсутствует подтверждающая цитата из текста документа.")

    c_low = claim_text.casefold().replace("ё", "е")
    q_low = full_evidence.casefold().replace("ё", "е")

    c_quants = extract_quantities(claim_text, reference_datetime)
    q_quants = extract_quantities(full_evidence, reference_datetime)
    doc_lead = " ".join((doc_text or "")[:350].split())
    lead_quants = extract_quantities(f"{doc_title} {doc_lead}".strip(), reference_datetime) if doc_lead else []
    for dm in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.((?:19|20)\d\d)\b", doc_lead):
        lead_quants.append(
            Quantity(dim="DATE", value=Decimal(int(dm.group(2)) * 100 + int(dm.group(1))), unit="", raw=dm.group(0))
        )

    def _quant_confirmed(cq: Quantity) -> bool:
        if any(cq.matches(dq) for dq in q_quants):
            return True
        return cq.dim == "DATE" and any(cq.matches(ldq) for ldq in lead_quants)

    sig_c_quants = [
        cq for cq in c_quants
        if cq.dim != "YEAR" and not (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1)))
    ]
    all_sig_quants_match = bool(sig_c_quants) and all(_quant_confirmed(cq) for cq in sig_c_quants)

    # 1. Проверка совпадения целевого года (данные за 2026 год не подтверждают план на 2027 или прошлый год)
    claim_years, claim_rel_shifted = resolve_target_years(claim_text, reference_datetime)
    quote_years, _ = resolve_target_years(full_evidence, reference_datetime)
    explicit_quote_years = {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", q_low)}
    lead_explicit_years = {
        int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", f"{doc_title} {doc_lead}")
    } if doc_lead else set()
    if not explicit_quote_years and lead_explicit_years and re.search(r"\b(?:в\s+этом\s+году|нынешн\w*\s+год\w*)\b", q_low):
        quote_years = lead_explicit_years
    ref_year = reference_datetime.year if isinstance(reference_datetime, (date, datetime)) else 2026
    explicit_claim_years = bool(re.search(r"\b(19\d\d|20\d\d)\b", c_low))
    doc_years, _ = resolve_target_years(f"{full_evidence} {doc_text}".strip(), reference_datetime)

    if claim_years:
        rel_campaign_exempt = (
            not explicit_claim_years
            and bool(_RELATIVE_YEAR_CLAIM_RE.search(c_low))
            and all_sig_quants_match
            and (bool(claim_years & doc_years) or ref_year in doc_years or not doc_years)
        )
        if not rel_campaign_exempt:
            if quote_years and not (claim_years & quote_years):
                return GuardResult(
                    False,
                    Label.clarify,
                    f"Несовпадение периода: в утверждении речь о {sorted(claim_years)}, а в источнике данные за {sorted(quote_years)}.",
                )
            if claim_rel_shifted and not quote_years and (ref_year not in claim_years):
                return GuardResult(
                    False,
                    Label.clarify,
                    f"В утверждении указан иной временной период ({sorted(claim_years)}), не подтверждённый в цитате.",
                )

    # 1a. Защита от устаревшего "новый / недавно": если в цитате указан только прошлый год (< ref_year)
    if _NOVELTY_CLAIM_RE.search(c_low) and quote_years and max(quote_years) < ref_year:
        return GuardResult(
            False,
            Label.clarify,
            f"Маркер новизны в утверждении требует уточнения: в источнике указан только прошлый период ({sorted(quote_years)}).",
        )

    # 2. Проверка модальности (План/концепция в источнике vs совершённый факт/будущее в статье)
    if any(v in c_low for v in _COMPLETED_PAST_VERBS) and any(m in q_low for m in _PLAN_MODALITY_MARKERS):
        if not any(m in c_low for m in _PLAN_MODALITY_MARKERS):
            return GuardResult(
                False,
                Label.clarify,
                "Расхождение модальности: в источнике речь о концепции/проекте/планах, а в утверждении — о свершившемся факте.",
            )

    # 3. Проверка всех значимых чисел, цен, процентов и дат с учётом канонизации (35 тысяч == 35 000, 1,5 ч == 90 мин)
    if not (is_derived_verified or calendar_weekday_verified):
        for cq in sig_c_quants:
            if not _quant_confirmed(cq):
                return GuardResult(
                    False,
                    Label.clarify,
                    f"Числовой или календарный показатель «{cq.raw}» не найден в подтверждающей цитате источника.",
                )

    return GuardResult(True, Label.confirmed, "Все числа, даты, период и модальность подтверждены источником.")


def verify_contradiction_cpu(
    claim_text: str,
    selected_quote: str,
    doc_title: str = "",
    reference_datetime: date | datetime | None = None,
    *,
    is_arithmetic_premise: bool = False,
) -> GuardResult:
    """Мгновенный CPU-барьер против False Accusations."""
    full_evidence = f"{doc_title}. {selected_quote}".strip() if doc_title else selected_quote
    c_low = claim_text.casefold().replace("ё", "е")
    q_low = full_evidence.casefold().replace("ё", "е")

    # 1. Защита арифметической посылки ("303 из 344" при ошибке в соседнем проценте)
    if is_arithmetic_premise:
        return GuardResult(
            False,
            Label.clarify,
            "Исходная посылка расчёта требует отдельного уточнения (ошибка содержится в вычисленном проценте).",
        )

    # 2. Защита от ложного обвинения при сравнении разных лет
    claim_years, claim_rel_shifted = resolve_target_years(claim_text, reference_datetime)
    quote_years, _ = resolve_target_years(full_evidence, reference_datetime)
    ref_year = reference_datetime.year if isinstance(reference_datetime, (date, datetime)) else 2026

    if claim_years:
        if quote_years and not (claim_years & quote_years):
            return GuardResult(
                False,
                Label.clarify,
                f"В источнике приведены данные за {sorted(quote_years)} год, сведений за {sorted(claim_years)} год для опровержения нет.",
            )
        if claim_rel_shifted and not quote_years and (ref_year not in claim_years):
            if not re.search(r"\b(?:в\s+прошл\w+|в\s+минувш\w+|годом\s+ранее|в\s+следующ\w+)\b", q_low):
                return GuardResult(
                    False,
                    Label.clarify,
                    "В источнике приведены данные за текущий период, данных за указанный в утверждении период для опровержения нет.",
                )

    # 3. Защита кванторов этапа, части здания и подмножества
    for mod in _SUBSET_OR_STAGE_MODIFIERS:
        if (mod in c_low and mod not in q_low) or (mod in q_low and mod not in c_low):
            return GuardResult(
                False,
                Label.clarify,
                f"Разная область или часть показателя («{mod}» присутствует только в одном из сравниваемых фрагментов).",
            )

    # 4. Защита от ложного обвинения при разговорном округлении единицы ("весом в тонну" vs "969 кг")
    if re.search(r"\bтонн[ауы]?\b", c_low) and not re.search(r"\d+(?:[\.,]\d+)?\s*тонн", c_low):
        for kg_m in re.finditer(r"\b(\d+(?:[\.,]\d+)?)\s*кг\b", q_low):
            kg_val = Decimal(kg_m.group(1).replace(",", "."))
            if Decimal("950") <= kg_val <= Decimal("1050"):
                return GuardResult(
                    False,
                    Label.clarify,
                    "В утверждении использовано разговорное округление до тонны, что не противоречит точному весу в источнике.",
                )

    # 5. Защита бесценового атома от ошибочного переноса конфликта цены из соседнего атома
    c_quants = extract_quantities(claim_text, reference_datetime)
    q_quants = extract_quantities(selected_quote, reference_datetime)
    c_money = [q for q in c_quants if q.dim == "MONEY"]
    q_money = [q for q in q_quants if q.dim == "MONEY"]
    if q_money and not c_money:
        has_other_numeric_conflict = any(
            cq.dim == dq.dim
            and cq.dim not in ("YEAR", "MONEY")
            and not (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1)))
            and not cq.matches(dq)
            and not any(cq.matches(other) for other in q_quants)
            for cq in c_quants
            for dq in q_quants
        )
        if not has_other_numeric_conflict:
            return GuardResult(
                False,
                Label.clarify,
                "В источнике приведена денежная сумма, однако в проверяемом фрагменте цена не указана — противоречие не относится к этому атому.",
            )

    # 6. Защита от ложного обвинения, если точечная цена/значение попадает в общий диапазон [A, B] источника
    c_has_range = bool(re.search(r"(?:от\s+)?\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?\s*(?:до|[-–—])\s*\d+", c_low))
    range_matches = (
        []
        if c_has_range
        else list(
            re.finditer(
                r"(?:от\s+)?(\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?:до|[-–—])\s*(\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?:рубл\w*|руб\.?|₽|р\.|балл\w*|чел\w*|%)",
                q_low,
            )
        )
    )
    if range_matches:
        for rm in range_matches:
            try:
                lo = Decimal(re.sub(r"[\s\u00a0]", "", rm.group(1)).replace(",", "."))
                hi = Decimal(re.sub(r"[\s\u00a0]", "", rm.group(2)).replace(",", "."))
            except Exception:
                continue
            if lo > hi:
                lo, hi = hi, lo
            for cq in c_quants:
                if cq.dim in ("MONEY", "COUNT", "PERCENT") and lo <= cq.value <= hi:
                    return GuardResult(
                        False,
                        Label.clarify,
                        "Указанное в утверждении значение попадает в общий диапазон из источника; противоречие не доказано.",
                    )

    # 7. Защита от смешения метрик: "очные бюджетные места" против общего числа мест без обоих признаков
    if "очн" in c_low and "бюджет" in c_low and ("очн" not in q_low or "бюджет" not in q_low):
        return GuardResult(
            False,
            Label.clarify,
            "В утверждении речь об очных бюджетных местах, а в источнике приведены данные без обоих признаков (очная форма и бюджет).",
        )

    # 8. Защита от обвинения при расхождении только модальности (концепция/план против свершившегося/будущего факта)
    has_quant_conflict = any(
        cq.dim == dq.dim
        and cq.dim not in ("YEAR", "PHONE")
        and not (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1)))
        and not cq.matches(dq)
        and not any(cq.matches(other) for other in q_quants)
        for cq in c_quants
        for dq in q_quants
    )
    if (
        not has_quant_conflict
        and any(v in c_low for v in _COMPLETED_PAST_VERBS)
        and not any(m in c_low for m in _PLAN_MODALITY_MARKERS)
        and any(m in q_low for m in _PLAN_MODALITY_MARKERS)
    ):
        return GuardResult(
            False,
            Label.clarify,
            "Расхождение модальности (концепция/план против свершившегося факта) требует уточнения, а не опровержения.",
        )

    # 9. Защита от ложного обвинения при сравнении утверждения о конкретном событии с общей практикой ("в своей практике")
    sel_low = (selected_quote or "").casefold().replace("ё", "е")
    if (
        not has_quant_conflict
        and "в своей практике" in sel_low
        and "практик" not in c_low
        and not re.search(r"\b(?:на\s+выставк|в\s+экспозици)", sel_low)
    ):
        return GuardResult(
            False,
            Label.clarify,
            "В источнике описана общая практика автора, а не состав работ конкретной выставки.",
        )

    return GuardResult(True, Label.error, "Противоречие прошло проверку области и периода.")


def _spans(text: str) -> list[tuple[int, int]]:
    return split_sentence_spans(text)


def _expand_table_lines(text: str) -> str:
    """Дублирует заголовки широких таблиц в каждую строку для find_candidates()."""
    lines = text.splitlines()
    header_cols: list[str] = []
    in_table = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                header_cols = cols
                in_table = True
                new_lines.append(line)
            elif all(set(c) <= {"-", ":", " "} for c in cols):
                new_lines.append(line)
            else:
                pairs = [f"{header_cols[i]}: {col}" for i, col in enumerate(cols) if i < len(header_cols) and col]
                enriched = f"{line} ({', '.join(pairs)})" if pairs else line
                new_lines.append(enriched)
        else:
            in_table = False
            header_cols = []
            new_lines.append(line)
    return "\n".join(new_lines)


def find_candidates(
    claim_text: str,
    documents: list[Doc],
    reference_datetime: date | datetime | None = None,
) -> list[dict]:
    """Оконно-размерностный отбор кандидатов (Алгоритм 4).

    Сначала формирует окно контекста вокруг строки, наследует тему из заголовка документа
    и сравнивает канонические физические величины через extract_quantities().
    """
    claim_stems = _stems(claim_text)
    claim_numbers = set(re.findall(r"\d+", claim_text))
    big_numbers = {n for n in claim_numbers if len(n) >= 3 and not re.fullmatch(r"(19|20)\d\d", n)}
    claim_dates = _dates(claim_text)
    claim_quants = extract_quantities(claim_text, reference_datetime)
    claim_has_temporal = bool(re.search(
        r"\b(?:срок\w*|дат\w*|период\w*|продолжительност\w*|врем\w*|начал\w*|окончан\w*|заверш\w*|продлит\w*|состоит\w*|работает\s+до|час\w*|минут\w*)\b",
        claim_text.casefold().replace("ё", "е"),
    ))

    scored = []
    for doc in documents:
        doc_title_stems = _stems(getattr(doc, "title", "") or "")
        title_shared = {w for w in (claim_stems & doc_title_stems) if not (w.isdigit() and len(w) == 1)}
        expanded_text = _expand_table_lines(doc.text)
        spans = _spans(expanded_text)
        for i, (start, end) in enumerate(spans):
            core = expanded_text[start:end]
            win_start = spans[max(0, i - CONTEXT_LINES)][0]
            win_end = spans[min(len(spans) - 1, i + CONTEXT_LINES)][1]
            window_quote = expanded_text[win_start:win_end]

            core_stems = _stems(core)
            win_stems = _stems(window_quote) | title_shared
            same_order = any(len(n) == len(c) for n in re.findall(r"\d{3,}", core) for c in big_numbers)
            shared_core = {w for w in claim_stems & core_stems if not (w.isdigit() and len(w) == 1)}
            shared_win = {w for w in claim_stems & win_stems if not (w.isdigit() and len(w) == 1)}

            doc_quants = extract_quantities(window_quote, reference_datetime)
            quant_match = any(
                cq.matches(dq)
                for cq in claim_quants
                if cq.dim != "YEAR" and not (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1)))
                for dq in doc_quants
            )
            same_dim_conflict = bool(shared_win) and any(
                (
                    (
                        cq.dim == dq.dim
                        and cq.dim in ("DURATION", "DATE", "DATE_RANGE", "PERCENT", "MONEY", "MASS", "COUNT")
                        and not (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1)))
                        and not cq.matches(dq)
                    )
                    or (
                        cq.dim == "DATE"
                        and dq.dim == "DATE_RANGE"
                        and dq.secondary_value is not None
                        and not (dq.value <= cq.value <= dq.secondary_value)
                    )
                )
                and not any(
                    cq.matches(other)
                    or (cq.dim == "DATE" and other.dim == "DATE_RANGE" and other.secondary_value is not None and other.value <= cq.value <= other.secondary_value)
                    for other in doc_quants
                )
                for cq in claim_quants
                for dq in doc_quants
            )

            if not shared_core and not same_order and not quant_match and not same_dim_conflict:
                continue

            if end - start < SHORT_FRAGMENT or not shared_core:
                cand_start, cand_end = win_start, win_end
            else:
                cand_start, cand_end = start, end
            quote = expanded_text[cand_start:cand_end]
            numbers = set(re.findall(r"\d+", quote))
            doc_kind = getattr(doc, "kind", "")
            source_prior = 2.0 if doc_kind in ("user_attachment", "attachment") else 0.0
            specific_nums = {
                n for n in (claim_numbers & numbers)
                if len(n) >= 3 and not re.fullmatch(r"(?:19|20)\d\d", n)
            }
            score = (
                len(claim_stems & (_stems(quote) | title_shared)) / max(1, len(claim_stems))
                + len(claim_numbers & numbers)
                + 1.5 * len(specific_nums)
                + source_prior
            )
            if quant_match:
                score += 1.5
            elif same_dim_conflict:
                score += 1.2

            dates = _dates(quote)
            other_date = bool(dates - claim_dates) and {m for _, m in dates} & {m for _, m in claim_dates}
            if claim_numbers and numbers - claim_numbers:
                strong = (same_order and not claim_numbers & numbers) or (other_date and not dates & claim_dates)
                score += SAME_ORDER_BONUS if strong else OTHER_NUMBER_BONUS
            if claim_has_temporal and re.search(
                r"\b(?:срок\w*|дат\w*|период\w*|продолжительност\w*|врем\w*|начал\w*|окончан\w*|заверш\w*|продлит\w*|состоит\w*|работает\s+до|час\w*|минут\w*)\b",
                quote.casefold().replace("ё", "е"),
            ):
                score += TEMPORAL_KEYWORD_BONUS

            core_nums = tuple(sorted(re.findall(r"\d+", core)))
            signature = (frozenset(w for w in core_stems if not w.isdigit()), core_nums)
            scored.append(
                (score, signature, {"doc": doc, "quote": quote, "start": cand_start, "end": cand_end, "score": score})
            )
    scored.sort(
        key=lambda triple: (
            -triple[0],
            0 if getattr(triple[2]["doc"], "kind", "") in ("user_attachment", "attachment")
            else (1 if getattr(triple[2]["doc"], "kind", "") == "official" else 2),
            triple[2]["doc"].url,
            triple[2]["start"],
        )
    )
    seen, taken, result = set(), [], []
    for _, signature, c in scored:
        if signature in seen or any(d is c["doc"] and s < c["end"] and c["start"] < e for d, s, e in taken):
            continue
        seen.add(signature)
        taken.append((c["doc"], c["start"], c["end"]))
        result.append(c)
    return result[:MAX_CANDIDATES]


def _build_eval_schema(num_candidates: int) -> dict[str, Any]:
    allowed_n = list(range(0, num_candidates + 1))
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "factcheck_claim_evaluation",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "comparison_note", "verdict", "exact_source_quote", "explanation", "suggestion"],
                "properties": {
                    "n": {
                        "type": "integer",
                        "enum": allowed_n,
                        "description": "Номер лучшего фрагмента [1..N] или 0, если подходящего фрагмента нет.",
                    },
                    "comparison_note": {
                        "type": "string",
                        "minLength": 5,
                        "description": "Короткая сверка (до 20 слов) ПЕРЕД вердиктом: числа/время в тексте vs в источнике, даты (до/после), модальность (проект/закон), кванторы (все/некоторые).",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["supported", "partially_supported", "contradicted", "unresolved"],
                    },
                    "exact_source_quote": {
                        "type": "string",
                        "description": "Дословная подстрока ТОЛЬКО из выбранного фрагмента [n] источника. Запрещено копировать сюда УТВЕРЖДЕНИЕ.",
                    },
                    "explanation": {"type": "string", "minLength": 5},
                    "suggestion": {"type": ["string", "null"]},
                },
            },
        },
    }


EVALUATION_SCHEMA = _build_eval_schema(MAX_CANDIDATES)


def _ask(
    meter: Meter,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
    schema: dict | None = None,
    seed: int | None = None,
    deadline: Any | None = None,
) -> dict:
    eval_seed = seed if seed is not None else int(os.environ.get("FACTCHECK_EVAL_SEED", "42"))
    raw = llm.parse_json(
        llm.chat(
            meter,
            [
                {"role": "system", "content": "You are a factual verification API. Return strictly valid JSON conforming to the schema without preamble or commentary."},
                {"role": "user", "content": prompt},
            ],
            light=True,
            temperature=0.0,
            seed=eval_seed,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=schema,
            deadline=deadline,
        )
    )
    return raw if isinstance(raw, dict) else {}


_IDENTIFIER_BEFORE_RE = re.compile(
    r"(?:№|n\b|номер\w*|приказ\w*|указ\w*|постановлени\w*|распоряжени\w*|закон\w*|корпус\w*|корп\.?|"
    r"микрорайон\w*|мкр\.?|дом\w*|д\.|квартал\w*|улиц\w*|ул\.|маршрут\w*|кабинет\w*|кв\.|"
    r"аудитор\w*|этаж\w*|стр\.|лини\w*|п\.|пункт\w*|ст\.|стать\w*|после|на\s+базе)\s*$",
    re.IGNORECASE,
)
_IDENTIFIER_AFTER_RE = re.compile(
    r"^\s*(?:-\s*\w+\s+)?(?:(?:и|или)\s+\d+\s+)?(?:микрорайон\w*|мкр\.?|корпус\w*|корп\.?|квартал\w*|этаж\w*|класс\w*|кл\.?)\b",
    re.IGNORECASE,
)

_MONEY_RANGE_RE = re.compile(
    r"от\s+(\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?:до|[-–—])\s*(\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?:рубл\w*|руб\.?|₽|р\.)",
    re.IGNORECASE,
)


def _extract_money_ranges(text: str) -> list[tuple[Decimal, Decimal]]:
    out: list[tuple[Decimal, Decimal]] = []
    for m in _MONEY_RANGE_RE.finditer(text or ""):
        try:
            lo = Decimal(re.sub(r"[\s\u00a0]", "", m.group(1)).replace(",", "."))
            hi = Decimal(re.sub(r"[\s\u00a0]", "", m.group(2)).replace(",", "."))
            out.append((min(lo, hi), max(lo, hi)))
        except Exception:
            continue
    return out


def _extract_floor_numbers(text: str) -> set[int]:
    """Извлекает только числа этажности (N этажей / N-этажный), не смешивая с площадью."""
    return {
        int(m.group(1))
        for m in re.finditer(r"(?<!\d)(\d{1,3})(?=(?:\s*(?:и|,)\s*\d{1,3})*\s*[-\s]*этаж)", (text or "").casefold())
    }


def _dim_covered_in_quants(cq: Quantity, q_dim: list[Quantity]) -> bool:
    """Проверяет, покрыта ли величина cq (включая DATE внутри DATE_RANGE) списком q_dim."""
    return any(
        cq.matches(other)
        or (
            cq.dim == "DATE"
            and other.dim == "DATE_RANGE"
            and other.secondary_value is not None
            and other.value <= cq.value <= other.secondary_value
        )
        for other in q_dim
    )


def _has_hard_dimensional_conflict(
    claim_text: str,
    quote_text: str,
    ref_dt: date | datetime | None = None,
) -> bool:
    """Проверяет прямой числовой конфликт по жёстким размерностям (DATE, DATE_RANGE, MONEY, MASS, DURATION, этажность)."""
    c_floors, q_floors = _extract_floor_numbers(claim_text), _extract_floor_numbers(quote_text)
    if c_floors and q_floors and (c_floors - q_floors) and (q_floors - c_floors):
        return True
    c_quants = extract_quantities(claim_text, ref_dt)
    q_quants = extract_quantities(quote_text, ref_dt)
    c_dim = [q for q in c_quants if q.dim in ("DURATION", "DATE", "DATE_RANGE", "MONEY", "MASS")]
    q_dim = [q for q in q_quants if q.dim in ("DURATION", "DATE", "DATE_RANGE", "MONEY", "MASS")]
    if any(
        cq.dim == dq.dim
        and not _is_incompatible_duration_units(cq, dq)
        and not cq.matches(dq)
        and not _dim_covered_in_quants(cq, q_dim)
        for cq in c_dim
        for dq in q_dim
    ):
        return True
    if any(
        cq.dim == "DATE"
        and dq.dim == "DATE_RANGE"
        and dq.secondary_value is not None
        and not (dq.value <= cq.value <= dq.secondary_value)
        and not _dim_covered_in_quants(cq, q_dim)
        for cq in c_dim
        for dq in q_dim
    ):
        return True
    c_ranges = _extract_money_ranges(claim_text)
    q_ranges = _extract_money_ranges(quote_text)
    if c_ranges and q_ranges and not any(cr == qr for cr in c_ranges for qr in q_ranges):
        return True
    return False


def _is_identifier_quantity(text: str, q: Quantity) -> bool:
    """Возвращает True, если COUNT-величина обозначает номер объекта/приказа/корпуса/кода специальности."""
    if q.dim != "COUNT" or not q.raw:
        return False
    low = (text or "").casefold().replace("ё", "е")
    low_compact = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", low)
    raw_low = q.raw.casefold().replace("ё", "е").strip()
    if re.search(rf"(?<!\d){re.escape(raw_low)}\.\d+|\d+\.{re.escape(raw_low)}(?!\d)", low_compact):
        return True
    matches = list(re.finditer(rf"(?<!\d){re.escape(raw_low)}(?!\d)", low_compact))
    if not matches:
        return False
    for m in matches:
        before = low_compact[max(0, m.start() - 32):m.start()]
        after = low_compact[m.end():min(len(low_compact), m.end() + 24)]
        if not (_IDENTIFIER_BEFORE_RE.search(before) or _IDENTIFIER_AFTER_RE.search(after)):
            return False
    return True


_PERCENT_FILLER_WORDS = (
    "процент", "процента", "процентов", "доля", "часть",
    "менее", "более", "меньше", "больше", "около", "почти", "всего",
    "примерно", "выше", "ниже", "составил", "составила", "составили",
    "составляет", "составляют", "вырос", "выросла", "выросло", "выросли",
    "увеличился", "увеличилась", "увеличилось", "уменьшился", "уменьшилась", "уменьшилось",
)


def _is_incompatible_duration_units(cq: Quantity, dq: Quantity) -> bool:
    """Исключает конфликт DURATION между минутами и часами, если значения отличаются более чем в 2 раза."""
    if cq.dim != "DURATION" or dq.dim != "DURATION":
        return False
    raw_a = (cq.raw or "").casefold()
    raw_b = (dq.raw or "").casefold()
    a_min = "мин" in raw_a and "час" not in raw_a and "ч." not in raw_a
    b_min = "мин" in raw_b and "час" not in raw_b and "ч." not in raw_b
    a_hr = ("час" in raw_a or re.search(r"\d\s*ч\b", raw_a) is not None) and "мин" not in raw_a
    b_hr = ("час" in raw_b or re.search(r"\d\s*ч\b", raw_b) is not None) and "мин" not in raw_b
    if (a_min and b_hr) or (b_min and a_hr):
        lo, hi = min(cq.value, dq.value), max(cq.value, dq.value)
        return hi > 2 * max(Decimal(1), lo)
    return False


def _doc_identity_key(doc: Any) -> str:
    """Канонический ключ документа (нормализует URL без www/хвостового слеша/фрагмента)."""
    url = (getattr(doc, "url", "") or "").strip()
    if url:
        p = urlparse(url)
        host = (p.netloc or "").casefold().removeprefix("www.")
        path = (p.path or "").rstrip("/")
        if host or path:
            return f"{host}{path}{f'?{p.query}' if p.query else ''}"
    return str(getattr(doc, "id", ""))


def _percent_clause_lemmas(text: str, raw: str) -> set[str]:
    """Леммы существительных/контекста в локальной клаузе одного предложения вокруг значения PERCENT."""
    if not text or not raw:
        return set()
    raw_pat = re.compile(rf"(?<!\d){re.escape(raw.casefold())}(?!\d)")
    sent = text
    for s, e in split_sentence_spans(text):
        chunk = text[s:e]
        if raw_pat.search(chunk.casefold()):
            sent = chunk
            break
    clauses = re.split(r"[,;—–()\n]+", sent)
    clause = next((cl for cl in clauses if raw_pat.search(cl.casefold())), sent)
    fillers = {_lemma(w) for w in _PERCENT_FILLER_WORDS}
    return {
        lem for w in re.findall(r"[а-яёa-z]{4,}", clause.casefold())
        if (lem := _lemma(w)) not in fillers
    }


def _is_compatible_conflict_pair(cq: Quantity, dq: Quantity) -> bool:
    """Проверяет совместимость размерностей и масштаба двух конфликтующих величин."""
    if cq.dim != dq.dim:
        return False
    if cq.dim in ("YEAR", "PHONE"):
        return False
    if _is_incompatible_duration_units(cq, dq):
        return False
    if cq.dim == "COUNT":
        if cq.value in (Decimal(0), Decimal(1)) or dq.value in (Decimal(0), Decimal(1)):
            return False
        if max(cq.value, dq.value) > 2 * max(Decimal(1), min(cq.value, dq.value)):
            return False
    return not cq.matches(dq)


def _detect_official_numeric_conflict(
    claim_text: str,
    documents: list,
    candidates: list,
    reference_datetime: date | datetime | None = None,
) -> tuple[bool, str]:
    c_quants = [
        q for q in extract_quantities(claim_text, reference_datetime)
        if q.dim not in ("YEAR", "PHONE")
        and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
        and not _is_identifier_quantity(claim_text, q)
    ]
    if not c_quants:
        return False, ""

    claim_words = {_lemma(w) for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", claim_text.casefold())}
    claim_years, _ = resolve_target_years(claim_text, reference_datetime)

    # Собираем фрагменты по каждому независимому документу (url/id)
    doc_fragments: dict[str, tuple[Any, list[str]]] = {}
    for c in candidates:
        doc = c.get("doc")
        if doc is None or _is_unmoderated_ugc(doc):
            continue
        doc_key = _doc_identity_key(doc)
        quote = c.get("quote", "")
        if not quote:
            continue
        entry = doc_fragments.setdefault(doc_key, (doc, []))
        if quote not in entry[1]:
            entry[1].append(quote)

    for doc in documents:
        if _is_unmoderated_ugc(doc) or not getattr(doc, "text", ""):
            continue
        doc_key = _doc_identity_key(doc)
        entry = doc_fragments.setdefault(doc_key, (doc, []))
        for s, e in split_sentence_spans(doc.text):
            sent = doc.text[s:e]
            if sent and sent not in entry[1]:
                entry[1].append(sent)

    if len(doc_fragments) < 2:
        return False, ""

    for cq in c_quants:
        confirming_docs: set[str] = set()
        for doc_key, (doc, quotes) in doc_fragments.items():
            for quote in quotes:
                quote_words = {_lemma(w) for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", quote.casefold())}
                if len(claim_words & quote_words) < 2:
                    continue
                q_years, _ = resolve_target_years(quote, reference_datetime)
                if claim_years and q_years and not (claim_years & q_years):
                    continue
                q_quants = extract_quantities(quote, reference_datetime)
                if any(cq.matches(aq) and not _is_identifier_quantity(quote, aq) for aq in q_quants):
                    confirming_docs.add(doc_key)
                    break

        if not confirming_docs:
            continue

        for doc_key_b, (doc_b, quotes_b) in doc_fragments.items():
            if getattr(doc_b, "kind", "") != "official":
                continue
            if not (confirming_docs - {doc_key_b}):
                continue
            doc_b_years, _ = resolve_target_years(
                f"{getattr(doc_b, 'title', '')} {(getattr(doc_b, 'text', '') or '')[:350]}",
                reference_datetime,
            )
            for quote_b in quotes_b:
                qb_low = quote_b.casefold().replace("ё", "е")
                if "колледж" in claim_text.casefold() and "колледж" not in qb_low and ("бакалавр" in qb_low or "магистр" in qb_low):
                    continue
                quote_words_b = {_lemma(w) for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", qb_low)}
                if len(claim_words & quote_words_b) < 2:
                    continue
                qb_years, _ = resolve_target_years(quote_b, reference_datetime)
                eff_b_years = qb_years or doc_b_years
                if claim_years and eff_b_years and not (claim_years & eff_b_years):
                    continue
                qb_quants = extract_quantities(quote_b, reference_datetime)
                if any(cq.matches(other) for other in qb_quants):
                    continue
                for dq in qb_quants:
                    if _is_identifier_quantity(quote_b, dq):
                        continue
                    if dq.raw and re.search(rf"\bна\s+{re.escape(dq.raw.casefold())}\b[^.;]{{0,35}}\b(?:меньше|больше|выше|ниже)\b", qb_low):
                        continue
                    if dq.raw and re.search(rf"\b(?:пн|вт|ср|чт|пт|сб|вс)\s*[:.]\s*{re.escape(dq.raw.casefold())}(?!\d)", qb_low):
                        continue
                    if cq.dim == "PERCENT" and cq.raw and dq.raw:
                        c_u = _percent_clause_lemmas(claim_text, cq.raw)
                        q_u = _percent_clause_lemmas(quote_b, dq.raw)
                        if not c_u or not q_u or not (c_u & q_u):
                            continue
                    elif cq.dim == "COUNT" and cq.raw and dq.raw:
                        c_m = re.search(rf"(?<!\d){re.escape(cq.raw.casefold())}(?!\d)", claim_text.casefold())
                        q_m = re.search(rf"(?<!\d){re.escape(dq.raw.casefold())}(?!\d)", quote_b.casefold())
                        c_pos = c_m.start() if c_m else -1
                        q_pos = q_m.start() if q_m else -1
                        c_win = claim_text[max(0, c_pos - 35):c_pos + len(cq.raw) + 35] if c_pos >= 0 else ""
                        q_win = quote_b[max(0, q_pos - 35):q_pos + len(dq.raw) + 35] if q_pos >= 0 else ""
                        c_u = {_lemma(w) for w in re.findall(r"[а-яёa-z]{4,}", c_win.casefold())}
                        q_u = {_lemma(w) for w in re.findall(r"[а-яёa-z]{4,}", q_win.casefold())}
                        if c_u and not (c_u & q_u):
                            continue
                    if _is_compatible_conflict_pair(cq, dq):
                        return True, f"В официальных документах обнаружен конфликт данных: {cq.raw} против {dq.raw}."
    return False, ""


_UNMODERATED_UGC_PATTERNS = (
    "vk.com/wall",
    "ok.ru",
    "pikabu.ru",
    "otzovik.com",
    "irecommend.ru",
    "2gis.ru",
    "otvet.mail.ru",
    "dzen.ru/a/",
)


def _is_unmoderated_ugc(doc: Any) -> bool:
    """Возвращает True только для немодерируемых UGC/форумов, на которые не было явной ссылки в тексте."""
    if getattr(doc, "kind", "") in ("user_attachment", "attachment", "official", "media", "ledger"):
        return False
    if getattr(doc, "is_linked", False):
        return False
    url_low = (getattr(doc, "url", "") or "").casefold()
    return any(pat in url_low for pat in _UNMODERATED_UGC_PATTERNS)


def build_editorial_note_suggestion(claim_quote: str, claimed_raw: str, actual_raw: str) -> str:
    """Вставляет примечание редакции сразу после спорного/ошибочного значения в тексте фрагмента."""
    note_str = f"{claimed_raw} ({actual_raw} — прим. редакции)"
    if claimed_raw and claimed_raw in claim_quote:
        return claim_quote.replace(claimed_raw, note_str, 1)
    return f"{claim_quote} ({actual_raw} — прим. редакции)"


def _find_conflicting_quantity_pair(
    claim_text: str,
    evidence_quotes: list[str],
    reference_datetime: datetime | None = None,
):
    """Находит пару (cq, dq) одной размерности и совместимого масштаба с разными значениями."""
    joined_evidence = " ".join(evidence_quotes)
    c_quants = [
        q for q in extract_quantities(claim_text, reference_datetime)
        if q.dim not in ("YEAR", "PHONE")
        and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
        and not _is_identifier_quantity(claim_text, q)
    ]
    s_quants = [
        q for q in extract_quantities(joined_evidence, reference_datetime)
        if q.dim not in ("YEAR", "PHONE")
        and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
        and not _is_identifier_quantity(joined_evidence, q)
    ]
    pairs = [
        (cq, dq)
        for cq in c_quants
        for dq in s_quants
        if _is_compatible_conflict_pair(cq, dq) and not any(cq.matches(other) for other in s_quants)
    ]
    if not pairs:
        return None
    for cq, dq in pairs:
        if is_typo_or_minor_numeric_diff(cq, dq):
            return cq, dq
    return pairs[0]


def _has_plan_modality_mismatch(claim_text: str, doc_leads: str) -> bool:
    """Возвращает True, если в утверждении безусловный факт/будущее, а в лиде источника лишь концепция/план."""
    c_low = claim_text.casefold().replace("ё", "е")
    if not any(v in c_low for v in _COMPLETED_PAST_VERBS) or any(m in c_low for m in _PLAN_MODALITY_MARKERS):
        return False
    leads_low = (doc_leads or "").casefold().replace("ё", "е")
    return any(m in leads_low for m in ("согласована концепция", "архитектурную концепцию", "концепци", "планируется построить"))


def _try_recover_confirmed_candidate(
    claim_text: str,
    candidates: list[dict[str, Any]],
    ref_dt: datetime,
    *,
    blocked: bool = False,
) -> tuple[str, str, list[tuple[Doc, str, str]]] | None:
    """Восстанавливает confirmed по топ-кандидатам, если 2+ нетривиальных числа и CPU-верификация полностью сходятся."""
    if blocked or not candidates:
        return None
    c_sig_n = [
        q for q in extract_quantities(claim_text, ref_dt)
        if q.dim not in ("YEAR", "DATE", "DATE_RANGE") and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
    ]
    if len(c_sig_n) < 2:
        return None
    claim_lemmas = {_lemma(w) for w in re.findall(r"[а-яёa-z]{4,}", claim_text.casefold())}
    for cand in candidates[:MAX_CANDIDATES]:
        doc = cand["doc"]
        if _is_unmoderated_ugc(doc):
            continue
        qt = cand["quote"]
        cand_lemmas = {_lemma(w) for w in re.findall(r"[а-яёa-z]{4,}", qt.casefold())}
        if len(claim_lemmas & cand_lemmas) < 3:
            continue
        q_quants = extract_quantities(qt, ref_dt)
        if not all(any(cq.matches(dq) for dq in q_quants) for cq in c_sig_n):
            continue
        qt_main = re.split(
            r"\n\s*(?:Станьте нашим подписчиком|Читайте также|Также вам может быть интересно|Реклама(?:\s|$))",
            qt,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        if _has_hard_dimensional_conflict(claim_text, qt_main, ref_dt) or has_predicate_negation_conflict(claim_text, qt_main):
            continue
        if _has_plan_modality_mismatch(claim_text, (getattr(doc, "text", "") or "")[:450]):
            continue
        conf = verify_confirmation_cpu(
            claim_text,
            qt,
            doc_title=getattr(doc, "title", ""),
            reference_datetime=ref_dt,
            doc_text=getattr(doc, "text", "") or "",
        )
        if conf.is_valid:
            return "supported", conf.reason, [(doc, qt, "supports")]
    return None


def evaluate_claim(
    claim_text: str,
    documents: list[Doc],
    meter: Meter,
    warnings: list[str],
    *,
    timeout: float,
    note: str = "",
    is_arithmetic_premise: bool = False,
    derived_verified: bool = False,
    in_text_arithmetic_consistent: bool = False,
    reference_datetime: datetime | None = None,
    deadline: Any | None = None,
) -> tuple[str, str, list[tuple[Doc, str, str]], str | None]:
    """→ (вердикт, объяснение, [(документ, цитата, relation)], правка или None).

    Гард, понизивший вердикт, правку снимает: исправление по недоказанному опровержению
    меняло бы факт, а этого ТЗ не допускает.
    """
    candidates = find_candidates(claim_text, documents, reference_datetime=reference_datetime)
    if not candidates:
        return ("unresolved", "В найденных источниках нет подходящего фрагмента. "
                "Отсутствие подтверждения не означает, что утверждение ложно.", [], None)
    def _format_cand(n: int, c: dict) -> str:
        doc = c["doc"]
        q = c["quote"][:1200]
        lead = " ".join((doc.text or "")[:280].split())
        hdr = (
            f", контекст: «{lead[:160]}»"
            if lead and lead[:40] not in q and re.search(r"\b(?:\d{2}\.\d{2}\.\d{4}|(?:19|20)\d\d)\b", lead)
            else ""
        )
        return f"[{n}] ({doc.id} «{doc.title[:100]}»{hdr}, {doc.kind}) «{q}»"

    listing = "\n".join(_format_cand(n, c) for n, c in enumerate(candidates, 1))
    ref_dt = reference_datetime if reference_datetime is not None else datetime.now(MOSCOW_TZ)
    today = ref_dt.date() if isinstance(ref_dt, datetime) else ref_dt
    deadline_ts = time.monotonic() + timeout
    eval_schema = _build_eval_schema(len(candidates))

    has_official_conflict, conflict_reason = _detect_official_numeric_conflict(
        claim_text, documents, candidates, reference_datetime=ref_dt
    )

    raw = _ask(
        meter,
        PROMPT.format(
            claim=claim_text,
            candidates=listing,
            today=today.isoformat(),
            year=today.year,
            last_year=today.year - 1,
        ),
        max_tokens=2048,
        timeout=max(0.001, deadline_ts - time.monotonic()),
        schema=eval_schema,
        deadline=deadline,
    )

    chosen = []
    n_val = raw.get("n")
    if isinstance(n_val, int) and 1 <= n_val <= len(candidates):
        cand = candidates[n_val - 1]
        v_raw = str(raw.get("verdict") or "supported")
        rel = "supports"
        if v_raw in ("contradicted", "refuted"):
            rel = "contradicts"
        elif v_raw == "partially_supported":
            rel = "partially_supports"
        resolved_q = _resolve_source_quote(
            str(raw.get("exact_source_quote") or ""),
            claim_text,
            cand["quote"],
        )
        chosen.append((cand, rel, resolved_q))
    else:
        for item in raw.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            n, relation = item.get("n"), item.get("relation")
            if isinstance(n, int) and 1 <= n <= len(candidates) and relation in RELATIONS:
                cand = candidates[n - 1]
                resolved_q = _resolve_source_quote("", claim_text, cand["quote"])
                chosen.append((cand, relation, resolved_q))

    evidence = [
        (c["doc"], _annotate_quote_with_page(c["doc"], resolved_q), relation)
        for c, relation, resolved_q in chosen
    ]

    raw_v = str(raw.get("verdict") or "unresolved")
    if raw_v == "refuted":
        raw_v = "contradicted"
    name = raw_v if raw_v in VERDICTS else "unresolved"
    limitations = " ".join(str(x).strip() for x in raw.get("limitations") or [] if str(x).strip())
    reason_text = (
        str(raw.get("explanation") or raw.get("reason") or "").strip() or "Модель не объяснила вердикт."
    )
    reason = " ".join(filter(None, [reason_text, limitations]))

    if name in EVIDENCE_REQUIRED and not evidence:
        return "unresolved", f"Модель предложила «{name}», но не указала доказательство.", [], None

    # Конфликт в официальных данных
    if has_official_conflict and name in ("supported", "contradicted"):
        name = "partially_supported"
        reason = conflict_reason or "В официальных документах обнаружен конфликт данных."

    # Немодерируемая пользовательская площадка (UGC) без ссылки в тексте не даёт confirmed
    if name == "supported":
        supporting_docs = [doc for doc, _, rel in evidence if rel in ("supports", "partially_supports")]
        if supporting_docs and all(_is_unmoderated_ugc(doc) for doc in supporting_docs):
            name = "partially_supported"
            reason = "Подтверждается только пользовательской площадкой (UGC), авторитетный источник отсутствует."

    # Если LLM поставил partially_supported лишь из-за «в этом году» в цитате архивного документа с датой в лиде,
    # или дал однозначное подтверждение без каких-либо оговорок/ограничений, повышаем до supported (пройдёт Гард №1)
    _blocked_promo = has_official_conflict or in_text_arithmetic_consistent or is_arithmetic_premise or derived_verified
    if (
        name == "partially_supported"
        and evidence
        and not _blocked_promo
        and not all(_is_unmoderated_ugc(d) for d, _, _ in evidence)
    ):
        ev_quotes_all = " ".join(q for _, q, _ in evidence)
        ev_titles_all = ", ".join(dict.fromkeys(d.title for d, _, _ in evidence if getattr(d, "title", "")))
        ev_texts_all = " ".join((getattr(d, "text", "") or "") for d, _, _ in evidence)
        _has_rel_this_year = (
            "в этом году" in ev_quotes_all.casefold()
            and not re.search(r"бакалавриат|только\s+(?:на|по|в|очн|часть)|частн|неполн|концепц|план|модальн", reason.casefold())
            and len([
                q for q in extract_quantities(claim_text, ref_dt)
                if q.dim != "YEAR" and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
            ]) >= 2
        )
        _reason_no_extra_omission = re.sub(
            r"(?:однако|но|вместе\s+с\s+тем)\s+(?:утверждение|фраза)\s+не\s+"
            r"(?:содержит|указывает|называет|упоминает)\s+[^.!?]+"
            r"(?:присутству\w*|указан\w*|име\w+|есть|содержа\w*)\s+в\s+(?:источник\w*|фрагмент\w*)\.?",
            "",
            reason.casefold(),
        )
        _pure_conf_reason = (
            bool(re.search(r"подтвержда\w+", _reason_no_extra_omission))
            and not re.search(
                r"\b(?:но|однако|не|нет|лишь|только|косвенн\w*|интерпрет\w*|частич\w*|неполн\w*|недостаточ\w*|"
                r"концепц\w*|план\w*|возможн\w*|уточн\w*|противореч\w*|расхожд\w*|отлича\w*|друго[йем]\w*|"
                r"ошиб\w*|разнит\w*|вместе\s+с\s+тем)\b",
                _reason_no_extra_omission,
            )
            and any(getattr(d, "kind", "") in ("official", "user_attachment", "attachment", "media") for d, _, _ in evidence)
        )
        if (
            (_has_rel_this_year or _pure_conf_reason)
            and not _has_hard_dimensional_conflict(claim_text, ev_quotes_all, ref_dt)
            and not has_predicate_negation_conflict(claim_text, ev_quotes_all)
        ):
            rel_conf = verify_confirmation_cpu(
                claim_text,
                ev_quotes_all,
                doc_title=ev_titles_all,
                reference_datetime=ref_dt,
                doc_text=ev_texts_all,
            )
            if rel_conf.is_valid:
                name = "supported"
                evidence = [(d, q, "supports") for d, q, _ in evidence]
                reason = rel_conf.reason

    # Гард №1: Защита от Ложных Подтверждений
    if name == "supported":
        combined_quotes = " ".join(
            f"{getattr(d, 'title', '')}. {q}".strip() if getattr(d, "title", "") else q
            for d, q, _ in evidence
        )
        source_titles = ", ".join(dict.fromkeys(d.title for d, _, _ in evidence if getattr(d, "title", "")))
        if not derived_verified:
            # Запрет подтверждения только по заголовку документа без тела и проверка отрицания
            if all(q.strip() == getattr(d, "title", "").strip() for d, q, _ in evidence if q.strip()):
                name = "unresolved"
                reason = "В источнике совпадает только заголовок без подтверждающего текста в теле документа."
            elif has_predicate_negation_conflict(claim_text, combined_quotes):
                name = "unresolved"
                reason = "Полярность утверждения (отрицание) не совпадает с цитатой источника."

        if name == "supported":
            c_strip = claim_text.strip()
            if c_strip.startswith(("«", '"')) and c_strip.endswith(("»", '"')) and re.search(r"\b(?:мы|я|наш\w*|мо[йяеи])\b", c_strip.casefold()):
                name = "partially_supported"
                reason = "Прямая субъективная цитата от первого лица не может быть безусловно подтверждена сторонним источником."

        if name == "supported":
            source_texts = " ".join((getattr(d, "text", "") or "") for d, _, _ in evidence)
            conf_guard = verify_confirmation_cpu(
                claim_text,
                combined_quotes,
                doc_title=source_titles,
                reference_datetime=ref_dt,
                is_derived_verified=derived_verified,
                doc_text=source_texts,
            )
            if not conf_guard.is_valid:
                name = "unresolved"
                reason = conf_guard.reason
            elif not derived_verified and _has_plan_modality_mismatch(
                claim_text,
                " ".join((getattr(d, "text", "") or "")[:450] for d, _, _ in evidence),
            ):
                name = "partially_supported"
                reason = "Расхождение модальности: в заголовке/лиде источника речь о концепции, а в утверждении — о безусловном факте."

    # Промоушен частично-подтверждённого/неясного вердикта к contradicted при прямом размерном конфликте на том же событии
    if (
        name in ("partially_supported", "unresolved")
        and not has_official_conflict
        and not in_text_arithmetic_consistent
        and not is_arithmetic_premise
    ):
        promoted = False
        if evidence:
            ev_quotes = " ".join(q for _, q, _ in evidence)
            ev_titles = ", ".join(dict.fromkeys(d.title for d, _, _ in evidence if getattr(d, "title", "")))
            if _has_hard_dimensional_conflict(claim_text, ev_quotes, ref_dt):
                promo_guard = verify_contradiction_cpu(
                    claim_text,
                    ev_quotes,
                    doc_title=ev_titles,
                    reference_datetime=ref_dt,
                    is_arithmetic_premise=is_arithmetic_premise,
                )
                if promo_guard.is_valid:
                    name = "contradicted"
                    chosen = [(c, "contradicts", rq) for c, _, rq in chosen]
                    evidence = [(d, q, "contradicts") for d, q, _ in evidence]
                    promoted = True

        # Если LLM выбрал нерелевантный подтверждающий фрагмент, но в топ-кандидатах есть прямое
        # неподтверждённое размерное противоречие (этажность/дата/срок) без подтверждения в других фрагментах
        if not promoted and candidates:
            c_dim_all = [
                q for q in extract_quantities(claim_text, ref_dt)
                if q.dim in ("DURATION", "DATE", "DATE_RANGE", "MONEY", "MASS")
            ]
            c_floors_all = _extract_floor_numbers(claim_text)
            pool_text = " ".join(c["quote"] for c in candidates[:8])
            pool_quants = extract_quantities(pool_text, ref_dt)
            pool_floors = _extract_floor_numbers(pool_text)
            already_confirmed = (
                (bool(c_floors_all) and c_floors_all <= pool_floors)
                or (bool(c_dim_all) and all(_dim_covered_in_quants(cq, pool_quants) for cq in c_dim_all))
            )
            c_quoted = {m.casefold() for m in re.findall(r"«([^»]{4,})»", claim_text)}
            if not already_confirmed:
                # Предпочитаем кандидатов с DATE_RANGE при проверке сроков/дат, затем по рангу find_candidates
                ranked_cands = sorted(
                    enumerate(candidates[:5]),
                    key=lambda ic: (
                        0 if any(q.dim == "DATE_RANGE" for q in extract_quantities(ic[1]["quote"], ref_dt)) else 1,
                        ic[0],
                    ),
                )
                for _, cand in ranked_cands:
                    qt = cand["quote"]
                    q_quoted = {m.casefold() for m in re.findall(r"«([^»]{4,})»", qt)}
                    if c_quoted and q_quoted and not (c_quoted & q_quoted):
                        continue
                    if _has_hard_dimensional_conflict(claim_text, qt, ref_dt):
                        cand_guard = verify_contradiction_cpu(
                            claim_text,
                            qt,
                            doc_title=cand["doc"].title,
                            reference_datetime=ref_dt,
                            is_arithmetic_premise=is_arithmetic_premise,
                        )
                        if cand_guard.is_valid:
                            name = "contradicted"
                            chosen = [(cand, "contradicts", qt)]
                            evidence = [(cand["doc"], qt, "contradicts")]
                            break

    # Гард №2: Защита от Ложных Обвинений
    contra = [(c, rel) for c, rel, _ in chosen if rel == "contradicts"]
    if name == "contradicted" and not contra:
        return "unresolved", "Опровержение без фрагмента, который прямо противоречит утверждению.", evidence, None

    if name == "contradicted":
        contra_quotes = " ".join(q for _, q, rel in evidence if rel == "contradicts")
        contra_titles = ", ".join(
            dict.fromkeys(d.title for d, _, rel in evidence if rel == "contradicts" and getattr(d, "title", ""))
        )
        contra_guard = verify_contradiction_cpu(
            claim_text,
            contra_quotes,
            doc_title=contra_titles,
            reference_datetime=ref_dt,
            is_arithmetic_premise=is_arithmetic_premise,
        )
        if not contra_guard.is_valid:
            contra_doc_texts = " ".join((getattr(d, "text", "") or "") for d, _, rel in evidence if rel == "contradicts")
            if "в этом году" in contra_quotes.casefold() and not _has_hard_dimensional_conflict(claim_text, contra_quotes, ref_dt):
                alt_conf = verify_confirmation_cpu(
                    claim_text,
                    contra_quotes,
                    doc_title=contra_titles,
                    reference_datetime=ref_dt,
                    doc_text=contra_doc_texts,
                )
                if alt_conf.is_valid:
                    evidence = [(d, q, "supports") for d, q, _ in evidence]
                    return "supported", alt_conf.reason, evidence, None
            rec = _try_recover_confirmed_candidate(claim_text, candidates, ref_dt, blocked=_blocked_promo)
            if rec is not None:
                return rec[0], rec[1], rec[2], None
            return "unresolved", contra_guard.reason, evidence, None

        if _SUBSET_LIMITER_RE.search(claim_text) and not _SUBSET_LIMITER_RE.search(contra_quotes.casefold()):
            rec = _try_recover_confirmed_candidate(claim_text, candidates, ref_dt, blocked=_blocked_promo)
            if rec is not None:
                return rec[0], rec[1], rec[2], None
            return (
                "unresolved",
                "В источнике приведена общая цифра без разбивки по этапам/квотам: противоречие не доказано.",
                evidence,
                None,
            )

        if has_official_conflict:
            name = "partially_supported"
            reason = conflict_reason or "В официальных документах обнаружен конфликт данных."

        if name == "contradicted" and in_text_arithmetic_consistent:
            if _find_conflicting_quantity_pair(claim_text, [q for _, q, _ in evidence], ref_dt) is not None:
                name = "partially_supported"
                reason = "Число в тексте связано внутренней арифметикой абзаца; требуется уточнение с примечанием редакции."

        c_sig_quants = [
            q for q in extract_quantities(claim_text, ref_dt)
            if q.dim != "YEAR" and not (q.dim == "COUNT" and q.value in (Decimal(0), Decimal(1)))
        ]
        if name == "contradicted" and c_sig_quants and any(
            all(any(cq.matches(dq) for dq in extract_quantities(c["quote"], ref_dt)) for cq in c_sig_quants)
            for c, _ in contra
        ):
            c_has_range = bool(re.search(r"(?:от\s+)?\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?\s*(?:до|[-–—])\s*\d+", claim_text.casefold()))
            if c_has_range and not has_predicate_negation_conflict(claim_text, contra_quotes):
                return "supported", "Все числа и диапазон утверждения прямо подтверждены источником.", evidence, None
            return "unresolved", "Все числа утверждения есть в «опровергающем» фрагменте: противоречие не доказано.", evidence, None

        det_dim_conflict = _has_hard_dimensional_conflict(claim_text, contra_quotes, ref_dt)

        if name == "contradicted" and not det_dim_conflict:
            remaining = deadline_ts - time.monotonic()
            if remaining <= 0.001:
                return "unresolved", "Проверка противоречия не уложилась в оставшийся бюджет времени.", evidence, None
            marked = {id(c) for c, _ in contra}
            fragments = "\n".join(
                f"{'ПРОТИВОРЕЧИТ' if id(c) in marked else 'контекст'} "
                f"({(c['doc'].title or c['doc'].id)[:100]}): «{c['quote']}»"
                for c in candidates
            )
            try:
                check = _ask(
                    meter,
                    CONTRADICTION_CHECK_V2.format(
                        claim=claim_text,
                        note=note or today.isoformat(),
                        fragments=fragments,
                    ),
                    max_tokens=1024,
                    timeout=max(0.001, remaining),
                    schema=CONTRADICTION_SCHEMA_V2,
                    deadline=deadline,
                )
            except (llm.LLMError, ValueError) as error:
                warnings.append(f"проверка опровержения «{claim_text[:60]}» не выполнена: {error}")
                check = {}
            def _is_truthy(val: Any) -> bool:
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.casefold() in ("true", "1", "yes", "да")
                return bool(val)

            same_sub = _is_truthy(check.get("same_subject"))
            same_time = _is_truthy(check.get("same_time_and_subset"))
            mut_excl = _is_truthy(check.get("mutually_exclusive"))
            not_missing = _is_truthy(check.get("not_just_missing"))
            why_text = str(check.get("why") or "").casefold()
            missing_info_markers = (
                "не содержит информации",
                "не содержат информации",
                "нет информации",
                "а не факт",
                "отсутствуют сведения",
            )
            reason_lacks_info = any(m in reason_text.casefold() or m in why_text for m in missing_info_markers)

            is_confirmed_contradiction = (
                same_sub
                and mut_excl
                and not reason_lacks_info
                and (same_time or "противореч" in why_text or "исключают" in why_text)
                and (not_missing or "прямое противоречие" in why_text)
            )
            if not is_confirmed_contradiction:
                rec = _try_recover_confirmed_candidate(claim_text, candidates, ref_dt, blocked=_blocked_promo)
                if rec is not None:
                    return rec[0], rec[1], rec[2], None
                why = str(check.get("why") or "проверка не подтвердила строгое противоречие").strip()
                return (
                    "unresolved",
                    f"Похоже на опровержение, но не доказано: {why} Расхождение найдено, но источник мог говорить о другом.",
                    evidence,
                    None,
                )

    raw_suggestion = str(raw.get("suggestion") or "").strip()
    suggestion: str | None = raw_suggestion if raw_suggestion.casefold() not in ("", "null", "none") else None

    if name == "unresolved":
        rec = _try_recover_confirmed_candidate(claim_text, candidates, ref_dt, blocked=_blocked_promo)
        if rec is not None:
            return rec[0], rec[1], rec[2], None

    if evidence and name in ("partially_supported", "contradicted"):
        pair = _find_conflicting_quantity_pair(claim_text, [q for _, q, _ in evidence], ref_dt)
        if pair is not None:
            cq, dq = pair
            if in_text_arithmetic_consistent or has_official_conflict:
                name = "partially_supported"
                note_val = f"в части официальных сводок указано {dq.raw}" if has_official_conflict else dq.raw
                suggestion = build_editorial_note_suggestion(claim_text, cq.raw, note_val)
            elif name == "contradicted" and not suggestion:
                if cq.raw and cq.raw in claim_text:
                    suggestion = claim_text.replace(cq.raw, dq.raw, 1)
                else:
                    suggestion = raw_suggestion or None
        elif has_official_conflict and not suggestion:
            m_conf = re.search(r":\s*(.+?)\s+против\s+(.+?)\.?$", conflict_reason or "")
            if m_conf:
                name = "partially_supported"
                suggestion = build_editorial_note_suggestion(
                    claim_text,
                    m_conf.group(1),
                    f"в части официальных сводок указано {m_conf.group(2)}",
                )

    return name, reason, evidence, suggestion
