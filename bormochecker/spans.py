"""Привязка утверждения к исходнику: span считает код, а не модель.

`contract.Report` требует `text[start:end] == Claim.text`, и исходник переписывать
нельзя. Модель отдаёт цитату — слой находит ей место в тексте. Сравнение идёт
по токенам, поэтому переносы строк, лишние пробелы, «ё» и кавычки не мешают;
не нашлось — утверждение отбрасывается с записью в ограничения, а не выдумывается.
"""

from __future__ import annotations

import re
from bormochecker.contract import Label

TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
#: Типографику приводим к простой, но посимвольно: длина строки не меняется, и позиция
#: токена в свёрнутой строке остаётся позицией в исходнике. В отчёте символы исходные.
FOLD = {"—": "-", "–": "-", "‑": "-", "−": "-", "«": '"', "»": '"', "„": '"',
        "“": '"', "”": '"', "’": "'", "‘": "'", "…": ".", " ": " ", " ": " ", " ": " "}


def _fold(value: str) -> str:
    return value.casefold().translate(str.maketrans(FOLD)) if any(item in value for item in FOLD) else value.casefold()


def _tokens(value: str) -> list[tuple[str, int]]:
    return [(match.group(), match.start()) for match in TOKEN.finditer(_fold(value))]


def _token_end(text: str, start: int) -> int:
    match = TOKEN.search(text, start)
    return match.end() if match else len(text)


def find_span_bridging_parens(text: str, quote: str, *, start_from: int = 0) -> tuple[int, int] | None:
    """Находит span цитаты в пределах одного предложения, сшивая пропущенные скобочные вставки и инлайновые URL."""
    q = (quote or "").strip()
    if not q or not text:
        return None
    # Убираем скобочные фрагменты из quote для поиска по опорным словам
    q_clean = re.sub(r"\([^()]*\)", " ", q)
    parts = [p for p in q_clean.split() if p]
    if len(parts) < 2:
        return None
    gap_pat = r"(?:\s+|(?:\s*\([^()\n]*(?:\([^()\n]*\)[^()\n]*)*\)\s*)+)"
    try:
        pattern = re.compile(gap_pat.join(re.escape(p) for p in parts))
    except re.error:
        return None
    m = pattern.search(text, start_from) or (pattern.search(text, 0) if start_from > 0 else None)
    if not m:
        return None
    s, e = m.start(), m.end()
    seg = text[s:e]
    if "\n" in seg or (e - s) > len(q) + 220:
        return None
    # Не пересекать границу предложения
    if re.search(r"[.!?]\s+[А-ЯЁA-Z]", re.sub(r"\([^()\n]*\)", "", seg)):
        return None
    return s, e


def find_span(text: str, quote: str, *, start_from: int = 0) -> tuple[int, int] | None:
    """(start, end) цитаты в исходнике. Сначала точное вхождение, потом по токенам, затем сшивка скобок."""
    quote = quote.strip()
    if not quote:
        return None
    exact = text.find(quote, start_from)
    if exact >= 0:
        return exact, exact + len(quote)
    haystack, needle = _tokens(text), _tokens(quote)
    width = len(needle)
    if not width:
        return None
    for i, (_, position) in enumerate(haystack):
        if position < start_from:
            continue
        if [token for token, _ in haystack[i : i + width]] == [token for token, _ in needle]:
            return position, _token_end(text, haystack[i + width - 1][1])
    return find_span_bridging_parens(text, quote, start_from=start_from)


def trim(text: str, found: tuple[int, int]) -> tuple[int, int, str]:
    """Сдвигает границы внутрь, чтобы подсветка не захватывала пробелы и переносы."""
    start, end = found
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def snap(text: str, quote: str, *, start_from: int = 0) -> tuple[int, int, str] | None:
    """Привязанный к исходнику фрагмент: границы и ровно тот текст, что стоит в отчёте."""
    found = find_span(text, quote, start_from=start_from)
    return trim(text, found) if found is not None else None


def _has_substantive_issue(item, is_pydantic: bool) -> bool:
    """Определяет, содержит ли атом реальную ошибку/нестыковку (а не просто 'не найдено в веб-поиске')."""
    label = getattr(item, "label", None) if is_pydantic else item.get("label")
    if label == Label.error:
        return True
    if label != Label.clarify:
        return False
    exp = (getattr(item, "explanation", "") if is_pydantic else (item.get("explanation") or "")).casefold()
    unresolved_markers = (
        "в найденных источниках нет подходящего фрагмента",
        "не проверялось в этом запуске",
        "отсутствие подтверждения не означает",
        "отдельного поиска по этому утверждению не было",
        "модель не ответила",
        "исчерпан бюджет времени",
    )
    if any(m in exp for m in unresolved_markers):
        return False
    return True


def resolve_span_overlaps(claims: list, text: str) -> list:
    """Выравнивает границы утверждений без вложенных и конфликтующих тегов.

    При перекрытии объединяет фрагменты или сохраняет утверждение с более критичной меткой:
    error > clarify > editorial > confirmed > opinion.
    """
    if not claims:
        return []

    is_pydantic = hasattr(claims[0], "start")

    def _get_val(c, key):
        return getattr(c, key) if is_pydantic else c.get(key)

    severity_order = {
        Label.error: 0,
        Label.clarify: 1,
        Label.editorial: 2,
        Label.confirmed: 3,
        Label.opinion: 4,
    }

    sorted_items = sorted(
        claims,
        key=lambda c: (_get_val(c, "start"), -(_get_val(c, "end") - _get_val(c, "start"))),
    )

    resolved: list = []
    for item in sorted_items:
        start = max(0, min(len(text), _get_val(item, "start")))
        end = max(start, min(len(text), _get_val(item, "end")))
        if start >= end:
            continue

        if not resolved:
            resolved.append(item)
            continue

        prev = resolved[-1]
        prev_start = _get_val(prev, "start")
        prev_end = _get_val(prev, "end")

        if start >= prev_end:
            resolved.append(item)
            continue

        prev_label = _get_val(prev, "label")
        curr_label = _get_val(item, "label")
        prev_exp = _get_val(prev, "explanation") or ""
        curr_exp = _get_val(item, "explanation") or ""

        # Защита посылки расчёта от поглощения смежной ошибкой
        if (
            ("посылк" in prev_exp.casefold() or "исходная" in prev_exp.casefold())
            and curr_label == Label.error
            and start >= prev_start
        ):
            new_prev_end = max(prev_start, start)
            if is_pydantic:
                from bormochecker.contract import Claim
                resolved[-1] = Claim(
                    start=prev_start,
                    end=new_prev_end,
                    text=text[prev_start:new_prev_end],
                    label=prev_label,
                    explanation=prev_exp,
                    sources=getattr(prev, "sources", []),
                    suggestion=getattr(prev, "suggestion", None),
                    origin=getattr(prev, "origin", None),
                )
            else:
                resolved[-1]["end"] = new_prev_end
                resolved[-1]["text"] = text[prev_start:new_prev_end]
            resolved.append(item)
            continue
        prev_sev = severity_order.get(prev_label, 9)
        curr_sev = severity_order.get(curr_label, 9)

        merged_start = min(prev_start, start)
        merged_end = max(prev_end, end)
        merged_text = text[merged_start:merged_end]

        # Защита Label.editorial от затирания обычным unresolved clarify
        if prev_label == Label.editorial and curr_label == Label.clarify:
            if not _has_substantive_issue(item, is_pydantic):
                chosen_label = Label.editorial
            else:
                chosen_label = Label.clarify
        elif curr_label == Label.editorial and prev_label == Label.clarify:
            if not _has_substantive_issue(prev, is_pydantic):
                chosen_label = Label.editorial
            else:
                chosen_label = Label.clarify
        else:
            chosen_label = curr_label if curr_sev < prev_sev else prev_label
        prev_exp = _get_val(prev, "explanation") or ""
        curr_exp = _get_val(item, "explanation") or ""
        merged_exp = f"{prev_exp}; {curr_exp}".strip("; ") if prev_exp != curr_exp else prev_exp

        prev_sugg = _get_val(prev, "suggestion")
        curr_sugg = _get_val(item, "suggestion")
        chosen_sugg = curr_sugg if curr_sev < prev_sev else (prev_sugg or curr_sugg)

        prev_sources = _get_val(prev, "sources") or []
        curr_sources = _get_val(item, "sources") or []
        merged_sources = list({(s.url, s.quote): s for s in (list(prev_sources) + list(curr_sources))}.values())

        prev_origin = _get_val(prev, "origin")
        curr_origin = _get_val(item, "origin")
        merged_origin = curr_origin or prev_origin

        if is_pydantic:
            from bormochecker.contract import Claim

            merged_item = Claim(
                start=merged_start,
                end=merged_end,
                text=merged_text,
                label=chosen_label,
                explanation=merged_exp,
                sources=merged_sources,
                suggestion=chosen_sugg,
                origin=merged_origin,
            )
        else:
            merged_item = dict(prev if curr_sev >= prev_sev else item)
            merged_item.update({
                "start": merged_start,
                "end": merged_end,
                "text": merged_text,
                "label": chosen_label,
                "explanation": merged_exp,
                "sources": merged_sources,
                "suggestion": chosen_sugg,
                "origin": merged_origin,
            })
        resolved[-1] = merged_item

    return resolved

