"""Унифицированная морфологическая нормализация токенов для FTS-индекса и поисковых запросов."""

from __future__ import annotations

import re

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _MORPH = None

NORMALIZER_VERSION = "1.0"

_RU_ENDINGS_RE = re.compile(
    r"(?:иями|ыями|ями|ами|ого|его|ому|ему|ыми|ими|ях|ах|ов|ев|ей|ой|ий|ый|ая|яя|ое|ее|ые|ие|"
    r"ам|ям|ом|ем|а|я|о|е|ы|и|у|ю|ь|ъ)$",
    re.IGNORECASE,
)


def normalize_token(word: str) -> str:
    """Нормализует отдельный токен через pymorphy3 с фоллбэком на псевдо-стеммер."""
    w = word.strip().casefold().replace("ё", "е")
    if len(w) <= 3 or w.isdigit():
        return w
    if _MORPH is not None:
        try:
            parsed = _MORPH.parse(w)
            if parsed:
                norm = parsed[0].normal_form.casefold().replace("ё", "е")
                if len(norm) >= 2:
                    return norm
        except Exception:
            pass
    cleaned = _RU_ENDINGS_RE.sub("", w)
    return cleaned if len(cleaned) >= 2 else w


def normalize_tokens(text: str) -> list[str]:
    """Извлекает и нормализует все слова и токены из текста."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", (text or "").casefold().replace("ё", "е"))
    return [normalize_token(w) for w in words]


def lemmatize_text(text: str) -> str:
    """Возвращает строку нормализованных токенов через пробел."""
    return " ".join(normalize_tokens(text))


normalize_text = lemmatize_text
