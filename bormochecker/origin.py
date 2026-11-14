"""Происхождение сведений редакции: `Claim.origin` и пятая метка.

Модель предлагает origin, код решает: служебное пояснение и формулировки — надёжнее
одного ответа модели, а по промпту заказчика сведения одного материала (одно видео,
одна съёмка, один ответ) должны быть помечены одинаково.

Три состояния: `stated` — редакция назвала свой источник, `unclear` — похоже на
собственные сведения, происхождение уточнить, `None` — внешний факт. Происхождение —
не шестой цвет: метка `editorial` им помечается, а у содержательной проблемы
редакционного фрагмента остаётся `clarify`/`error` с подписью origin.
"""

from __future__ import annotations

import re

from bormochecker.contract import Label

STATED = "stated"
UNCLEAR = "unclear"
ORIGIN_TEXT = {STATED: "источник назван редакцией", UNCLEAR: "источник не назван, происхождение уточнить"}

#: Редакция прямо указала свой источник.
STATED_MARKERS = re.compile(
    r"(в ответе на (наш|запрос редакции|нашу просьбу)|нашей редакции|нашему корреспонденту|"
    r"корреспондент (увидел|зафиксировал|передал)|читател[ьи] прислал[аи]? нам|прислан\w* (нам|нашей редакции)|"
    r"рассказал[аи]? (нам|нашей редакции)|по словам (сотрудников|пресс-службы) (учреждения|нам))",
    re.IGNORECASE,
)
#: Похоже на собственное наблюдение, но источник не назван.
UNCLEAR_MARKERS = re.compile(
    r"(на видео|судя по видео|видеозапис\w*|на кадрах (ночью|утром|днём|вчера)|"
    r"съёмк\w*|по (записям|кадрам|материалам) (камер|редакции))",
    re.IGNORECASE,
)
#: Пояснение редакции указывает, что материал её собственный.
NOTE_MARKERS = re.compile(
    r"(редакци|прислан|съёмк|видео|запис|наблюден|интервью|ответ\w* (на запрос|нам)|"
    r"собственн\w* (материал|сведени)|оригинал|передан)",
    re.IGNORECASE,
)

#: Внешняя атрибуция пресс-релизам/организаторам — не является редакционным материалом.
_EXTERNAL_ATTRIBUTION = re.compile(
    r"(?:пишут\s+организатор|сообщает\s+пресс-служб|по\s+данным\s+организатор|указано\s+на\s+сайте|говорится\s+на\s+сайте|отметили\s+в\s+пресс-службе)",
    re.IGNORECASE,
)

_FINITE_PREDICATE_RE = re.compile(
    r"\b(?:составляет|является|запрещается|разрешается|обязан[ыао]?|проводится|утвердил[аио]?|"
    r"принял[аио]?|состоится|состоял[аио]?сь|пройдёт|пройдет|прошёл|прошел|начал[аио]?сь|"
    r"открыл[аио]?сь|установил[аио]?|вступил[аио]?|работает|действует|содержит|включает|"
    r"выпустил[аио]?|продолжает|записыва\w*|было|была|были|будет|будут|может|могут|"
    r"должен|должна|должны|нельзя|нужно|следует)\b",
    re.IGNORECASE,
)

try:
    import pymorphy3
    _ORIGIN_MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _ORIGIN_MORPH = None


def _contains_verb_or_number(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\d", text) or _FINITE_PREDICATE_RE.search(text):
        return True
    if _ORIGIN_MORPH is not None:
        for w in re.findall(r"[а-яА-ЯёЁ]{3,}", text):
            parsed = _ORIGIN_MORPH.parse(w)
            if parsed and parsed[0].tag.POS in ("VERB", "INFN"):
                return True
    return False


def is_heading_without_claim(
    text: str,
    full_text: str | None = None,
    span: tuple[int, int] | None = None,
) -> bool:
    """Определяет заголовок/подзаголовок по структуре: отдельная короткая строка без точки, глаголов и чисел."""
    s = (text or "").strip()
    if not s or len(s) > 100 or "\n" in s:
        return False

    if s.endswith((".", "!", "?", "…")):
        return False

    if _contains_verb_or_number(s):
        return False

    # Если передан полный текст и координаты, проверяем структурное положение заголовка
    if full_text and span:
        start, end = span
        prefix = full_text[:start]
        line_start = prefix.rfind("\n")
        pre_chars = prefix[line_start + 1:] if line_start != -1 else prefix
        if pre_chars.strip():
            return False

        suffix = full_text[end:]
        line_end = suffix.find("\n")
        post_chars = suffix[:line_end] if line_end != -1 else suffix
        if post_chars.strip(" :👇"):
            return False

        # Проверяем, что строка находится в начале блока (начало текста или после пустой строки)
        if line_start != -1:
            before_line = prefix[:line_start]
            prev_nl = before_line.rfind("\n")
            line_above = before_line[prev_nl + 1:] if prev_nl != -1 else before_line
            if line_above.strip():
                return False

        return True

    return False


def note_declares_editorial(note: str) -> bool:
    return bool(NOTE_MARKERS.search(note or ""))


#: Деонтические предикаты, оценка риска в общем случае и циклические природные/научные процессы.
_DEONTIC_PREDICATES_RE = re.compile(
    r"\b(?:не\s+нужно|не\s+стоит|не\s+следует|не\s+пытайтесь|не\s+рекомендуется|"
    r"рекомендуется|следует|лучше\s+остановиться|стоит\s+быть\s+(?:особенно\s+)?осторожн\w+)\b",
    re.IGNORECASE,
)
_PROBABILISTIC_SAFETY_RE = re.compile(
    r"\b(?:здоров\w+\s+\w+\s+для\s+человека\s+опасности\s+(?:практически\s+)?не\s+представляет|"
    r"скорее\s+всего,\s+сам\s+убежит)\b",
    re.IGNORECASE,
)
_CYCLIC_PROCESS_RE = re.compile(
    r"\b(?:в\s+конце\s+\w+\s+у\s+этих\s+\w+\s+начинается|пик\s+приходится\s+на|"
    r"больше\s+перемещаются\s+в\s+поисках|становятся\s+менее\s+осторожными|"
    r"могут\s+проявлять\s+агрессию)\b",
    re.IGNORECASE,
)


def is_probabilistic_safety_opinion(text: str) -> bool:
    """Оценочное суждение о безопасности с ограничителем вероятности."""
    t = (text or "").casefold().replace("ё", "е")
    return bool(_PROBABILISTIC_SAFETY_RE.search(t))


def is_generic_or_deontic_statement(text: str) -> bool:
    """Деонтические инструкции, общие оценки риска и циклические природные/научные закономерности."""
    t = (text or "").casefold().replace("ё", "е")
    return bool(
        _DEONTIC_PREDICATES_RE.search(t)
        or _PROBABILISTIC_SAFETY_RE.search(t)
        or _CYCLIC_PROCESS_RE.search(t)
    )


def from_text(fragment: str) -> str | None:
    """Origin по формулировке самого фрагмента: None — внешний факт."""
    frag = fragment or ""
    if _EXTERNAL_ATTRIBUTION.search(frag) or is_heading_without_claim(frag) or is_generic_or_deontic_statement(frag):
        return None
    if STATED_MARKERS.search(frag):
        return STATED
    if UNCLEAR_MARKERS.search(frag):
        return UNCLEAR
    return None


def from_note(note: str) -> str | None:
    """Пояснение называет источник → сведения такого материала `stated`.

    Пояснение вроде «детали эпизода — из видео, присланных редакции» само называет
    источник, поэтому уточнение не нужно; без такого указания материал остаётся гипотезой.
    """
    return STATED if note_declares_editorial(note) else None


def _paragraph_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Абзац вокруг утверждения: пустая строка или конец текста."""
    head = text.rfind("\n\n", 0, start)
    tail = text.find("\n\n", end)
    return (head + 2 if head >= 0 else 0, tail if tail >= 0 else len(text))


def propagate(fragments: list, text: str, note: str) -> None:
    """Проставляет origin по местам (в объектах с полями `origin`, `quote`, `start`, `end`).

    Сведения одного материала описываются подряд, поэтому origin якорного фрагмента
    (видео, съёмка, названный ответ) распространяется на весь абзац: так визуальное
    наблюдение в том же абзаце получает то же происхождение, что и якорная фраза. Справка о
    поведении и рекомендации в том же абзаце остаются внешними фактами: помечаем
    только то, что похоже на наблюдение, и никогда — то, что модель назвала внешним.
    """
    note_origin = from_note(note)
    for fragment in fragments:
        lexical = from_text(fragment.quote)
        if lexical and not fragment.origin:
            fragment.origin = lexical  # «судя по видео» — источник не назван
        if note_origin and fragment.origin == UNCLEAR:
            fragment.origin = note_origin  # пояснение редакции назвало источник
    anchors: dict[tuple[int, int], str] = {}
    for fragment in fragments:
        if fragment.origin:
            anchors.setdefault(_paragraph_bounds(text, fragment.start, fragment.end), fragment.origin)
    for fragment in fragments:
        if fragment.origin or not _looks_like_observation(fragment.quote):
            continue
        origin = anchors.get(_paragraph_bounds(text, fragment.start, fragment.end))
        if origin:
            fragment.origin = origin


#: Прошедшее время визуального наблюдения и перемещения в кадре.
_OBSERVATION = re.compile(
    r"(?:съёмк|съемк|видео|увидел|увидели|заметил|заметили|снял|сняли|заснял|засняли|попал\w*\s+в\s+кадр|"
    r"сидел|сидела|стоял|стояла|пере[сш]ёк|пересек|пересекал\w*|ушёл|ушел|ушла|убежал|прыгал|лежал|бежал|"
    r"вышел|вышла|зашёл|зашел|гулял|бродил|пробежал|судя\s+по\s+(?:видео|кадрам|фото)|на\s+видео\s+видно)",
    re.IGNORECASE,
)
#: Справка, рекомендация или обобщение: это внешний факт, даже рядом с материалом редакции.
_EXTERNAL_CONTEXT = re.compile(
    r"(обычно|как правило|по данным|справка|рекоменд|нужно|нельзя|следует|в природе|в мире|учёные|"
    r"исследовани|статья|когда начина|период|составляет|в среднем|не нужно|не стоит|стоит быть|опасности|памятк|мчс|департамент|при встрече)",
    re.IGNORECASE,
)


def is_episodic_observation(quote: str) -> bool:
    """Эпизодическое наблюдение из медиаматериалов редакции (прошедшее время перемещения/съёмки)."""
    q = quote or ""
    if is_generic_or_deontic_statement(q):
        return False
    return bool(_OBSERVATION.search(q)) and not _EXTERNAL_CONTEXT.search(q)


def _looks_like_observation(quote: str) -> bool:
    """Описание увиденного или снятого скорее, чем внешний справочный факт."""
    return is_episodic_observation(quote)


def label_for(verdict: Label | None, checkability: str, origin: str | None) -> Label:
    """Метка с учётом происхождения.

    Редакционный фрагмент без содержательной проблемы — `editorial`: оригинал в
    прогоне не проверялся, и это не подтверждение. Проблема (жёлтый/красный)
    остаётся главной меткой, origin идёт подписью — по промпту заказчика §3.
    """
    if origin and verdict is None:
        return Label.editorial
    if not origin:
        return verdict or Label.clarify
    if verdict == Label.confirmed:
        return Label.editorial
    if verdict == Label.opinion:
        return verdict
    return verdict or Label.editorial



def explanation_for(verdict: Label, checkability: str, origin: str | None) -> str:
    """Подпись, если сама метка не говорит, что это данные редакции."""
    if origin and verdict != Label.editorial:
        return f"Сведение из материала редакции ({ORIGIN_TEXT[origin]})"
    if checkability in {"opinion", "prediction"} and verdict == Label.opinion:
        return "Мнение, оценка или прогноз; фактическое ядро проверяется отдельным утверждением."
    if checkability == "not_checkable":
        return "Проверке по открытым источникам не подлежит."
    return ""

