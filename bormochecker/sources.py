"""Поиск и источники: какие запросы задать, какие страницы загрузить, что не считать источником.

Результат поиска — навигация, а не доказательство: страницу по найденному URL загружаем сами
(retrieval.py) и цитируем только её текст. Документы общие на всю проверку: один документ
обслуживает много утверждений, повторный URL берётся из кэша и в цену не идёт.

Трёхуровневый каскад источников:
  Уровень 0: Локальная база знаний SQLite FTS5 (knowledge_fts.sqlite) + preload_local_docs (0 ₽, 0 мс)
  Уровень 1: Локальный мета-поисковик SearXNG + параллельный фетч (0 ₽)
  Уровень 2: Платный Yandex Search API (фоллбэк только для journalism_full и high importance)
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from bormochecker.paths import knowledge_fts_path, official_domains_path

from bormochecker.costs import Meter
from bormochecker.direct_apis import DirectAPIs
from bormochecker.local_index import LocalKnowledgeIndex
from bormochecker.retrieval import (
    BLOCKED_DOMAINS as BLOCKED_DOMAINS,
    FetchedDocument,
    FetchError,
    SafeDocumentFetcher,
    UnsafeUrlError,
    is_blocked_domain_host,
)
from bormochecker.search import FreeRuNetSearch, SearchError, SearchResult, SearXNGSearch, YandexSearch
from bormochecker.search_cache import SearchCache

# Первоисточники федеральной образовательной и нормативно-правовой повестки РФ.
DEFAULT_OFFICIAL_DOMAINS = (
    "edu.gov.ru",           # Минпросвещения России
    "obrnadzor.gov.ru",     # Рособрнадзор
    "fipi.ru",              # ФИПИ (ОГЭ, ЕГЭ, ГВЭ)
    "edsoo.ru",             # Единое содержание общего образования (ФРП)
    "fgosreestr.edsoo.ru",  # Реестр ФГОС и ФАООП ОВЗ
    "pravo.gov.ru",         # Официальный интернет-портал правовой информации
    "regulation.gov.ru",    # Проекты нормативных правовых актов
    "minobrnauki.gov.ru",   # Минобрнауки России
    "rostrud.gov.ru",       # Роструд (трудовые права педагогов и доплаты)
    "rosstat.gov.ru",       # Росстат
    "ikp-rao.ru",           # Институт коррекционной педагогики (ОВЗ)
    "mos.ru",               # МЭШ / Департамент образования Москвы
    "zelao.mos.ru",         # Префектура Зеленограда
    "dkzelenograd.ru",      # Культурный центр «Зеленоград»
    "zelkultura.ru",        # Культура Зеленограда
    "miet.ru",              # НИУ МИЭТ
    "abiturient.miet.ru",   # Абитуриент МИЭТ
    "mgpu.ru",              # МГПУ
    "government.ru",
    "kremlin.ru",
    "gov.ru",
)


def load_official_domains() -> tuple[str, ...]:
    path = official_domains_path()
    if path.is_file():
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        domains = tuple(line for line in lines if line and not line.startswith("#"))
        if domains:
            return domains
    return DEFAULT_OFFICIAL_DOMAINS


OFFICIAL_DOMAINS = load_official_domains()
MEDIA_DOMAINS = ("tass.ru", "ria.ru", "interfax.ru", "rbc.ru", "kommersant.ru")

# Внешние платформы, недоступные для прямого парсинга
_UNSCRAPEABLE_DOMAINS = ("facebook.com", "instagram.com", "tiktok.com")


def _select_urls_to_fetch(ranked: list[SearchResult], seen: set[str], limit: int = 2) -> list[str]:
    def _is_pdf(hit: SearchResult) -> bool:
        return hit.url.casefold().split("?", 1)[0].endswith(".pdf")

    ordered = sorted(ranked, key=_is_pdf)
    has_html = any(not _is_pdf(h) for h in ordered)
    to_fetch: list[str] = []
    for hit in ordered:
        host = (urlsplit(hit.url).hostname or "").removeprefix("www.")
        if is_blocked_domain_host(host) or _matches(host, _UNSCRAPEABLE_DOMAINS) or hit.url.rstrip("/") in seen:
            continue
        if has_html and _is_pdf(hit):
            continue
        seen.add(hit.url.rstrip("/"))
        to_fetch.append(hit.url)
        if len(to_fetch) >= limit:
            break
    return to_fetch

# Тема утверждения → профильные первоисточники
TOPIC_DOMAINS = (
    (
        ("школ", "учени", "учител", "образован", "егэ", "огэ", "кегэ", "фгос", "фрп", "едсоо"),
        ("edu.gov.ru", "obrnadzor.gov.ru", "fipi.ru", "edsoo.ru", "fgosreestr.edsoo.ru"),
    ),
    (
        ("приказ", "постановлен", "указ", "распоряжен", "закон", "регламент", "проект"),
        ("pravo.gov.ru", "regulation.gov.ru"),
    ),
    (
        ("вуз", "студент", "университет", "минобр", "наук", "миэт", "бакалавриат", "магистратур", "абитуриент", "колледж", "приемн"),
        ("minobrnauki.gov.ru", "miet.ru", "abiturient.miet.ru"),
    ),
    (
        ("зеленоград", "дк зеленоград", "кц зеленоград", "афиш", "концерт", "спектакл", "выставк"),
        ("dkzelenograd.ru", "zelkultura.ru", "mos.ru", "zelao.mos.ru"),
    ),
    (
        ("зарплат", "труд", "роструд", "отпуск", "нагрузк", "ставка", "надбавк"),
        ("rostrud.gov.ru",),
    ),
    (
        ("овз", "фаооп", "коррекцион", "адаптирован"),
        ("ikp-rao.ru", "fgosreestr.edsoo.ru"),
    ),
    (
        ("статистик", "росстат", "число", "динамик"),
        ("rosstat.gov.ru",),
    ),
)
MAX_QUERY_CHARS = 250  # у Yandex потолок 400 на весь запрос, место оставляем под site:
COPY_SENTENCE_CHARS = 40
COPY_MATCHES = 3
URL_RE = re.compile(r"https?://[^\s<>()«»\"']+")


class SearchUnavailable(SearchError):
    """Поиск не подключён: запрос не отправлялся и в цену не идёт."""


def web_search(query: str, allow_paid: bool = False) -> list[SearchResult]:
    searxng_url = os.environ.get("SEARXNG_BASE_URL", "").strip() or "http://127.0.0.1:8088"
    if searxng_url:
        try:
            return SearXNGSearch(searxng_url).search(query, limit=10)
        except Exception:
            pass
    key = os.environ.get("YANDEX_CLOUD_API_KEY", "").strip()
    if allow_paid and key:
        return YandexSearch(key).search(query, limit=10)
    if not searxng_url and not key:
        raise SearchUnavailable("поиск не подключён: SEARXNG_BASE_URL и YANDEX_CLOUD_API_KEY не заданы")
    return []


_fetcher = SafeDocumentFetcher()


def fetch_page(url: str, timeout: float | None = None) -> FetchedDocument:
    return _fetcher.fetch(url, timeout=timeout)


@dataclass
class Doc:
    id: str
    url: str
    title: str = ""
    kind: str = "unknown"  # official | media | user_attachment | ledger | unknown
    text: str = ""
    error: str = ""  # найден, но не прочитан: цитировать нельзя
    is_linked: bool = False


UGC_DOMAINS = (
    "pikabu.ru",
    "dzen.ru",
    "livejournal.com",
    "vk.com",
    "t.me",
    "ok.ru",
    "drive2.ru",
    "vc.ru",
    "habr.com",
)


def _matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def publisher_kind(domain: str) -> str:
    if _matches(domain, UGC_DOMAINS):
        return "ugc"
    return "official" if _matches(domain, OFFICIAL_DOMAINS) else "media" if _matches(domain, MEDIA_DOMAINS) else "unknown"


def sanitize_query_for_free_engines(query: str) -> str:
    """Очищает запрос от множественных 'site:a | site:b', ломающих бесплатные поисковики.

    Бесплатные движки (SearXNG/DuckDuckGo/Brave/DDGS) отлично понимают одиночный 'site:domain.ru',
    но возвращают 0 результатов при склейках '(site:a | site:b | site:c)'.
    """
    raw = (query or "").strip()
    sites = re.findall(r"site:([a-zA-Z0-9.\-]+)", raw)
    if len(sites) <= 1:
        return re.sub(r"[\(\)]", "", raw).strip()
    # Множественные site: вырезаем
    cleaned = re.sub(r"\([^\)]*site:[^\)]*\)", " ", raw)
    cleaned = re.sub(r"site:[a-zA-Z0-9.\-]+(?:\s*\|\s*site:[a-zA-Z0-9.\-]+)*", " ", cleaned)
    cleaned = re.sub(r"\|", " ", cleaned)
    anchors = []
    folded = cleaned.casefold()
    if any(s in ("edu.gov.ru", "edsoo.ru", "fgosreestr.edsoo.ru") for s in sites) and "минпросвещ" not in folded and "фрп" not in folded:
        anchors.append("Минпросвещения")
    elif any(s in ("fipi.ru", "obrnadzor.gov.ru") for s in sites) and "фипи" not in folded and "рособрнадзор" not in folded:
        anchors.append("ФИПИ Рособрнадзор")
    elif any(s in ("pravo.gov.ru", "regulation.gov.ru") for s in sites) and "приказ" not in folded and "закон" not in folded:
        anchors.append("официальный документ")
    elif any(s in ("mos.ru",) for s in sites) and "москв" not in folded:
        anchors.append("mos.ru")

    suffix = (" " + " ".join(anchors)) if anchors else ""
    return (" ".join(cleaned.split()) + suffix).strip()


def clean_query_for_free_search(query: str) -> str:
    """Убирает конструкции '(site:a | site:b | ...)', которые обнуляют выдачу в DDG/SearXNG/Tavily."""
    return sanitize_query_for_free_engines(query)


_ANCHOR_STOP_WORDS = frozenset({
    "момент", "более", "также", "потом", "кроме", "около", "всего", "приемная", "кампания",
    "концерт", "источник", "после", "перед", "когда", "чтобы", "если", "при", "для", "про",
    "или", "без", "тестовый", "текст", "новость", "редакция", "россии", "россия", "москва",
})

_CONCISE_STOP_WORDS = frozenset({
    "это", "как", "так", "ибо", "или", "при", "для", "без", "под", "над",
    "про", "все", "вся", "всё", "они", "она", "оно", "его", "ему", "них",
    "был", "была", "были", "быть", "есть", "будет", "год", "года", "году",
    "тогда", "также", "более", "менее", "около", "почти", "всего", "вместе",
    "фиксировал", "фиксировала", "фиксировали", "отмечал", "сообщал", "заявлял",
    "ранее", "годом", "этом", "том", "этой", "этих", "числе", "числа",
    "существенный", "сравнению", "предыдущим", "впервые", "только", "получил",
    "подано", "специальности",
})


def extract_article_subject(text: str) -> str:
    """Извлекает главное имя собственное статьи (МИЭТ, Dabro и т.п.) из заголовка и лида."""
    head = "\n".join(line.strip() for line in (text or "").splitlines()[:4] if line.strip())[:350]
    if not head:
        return ""
    # 1. Явная аббревиатура вуза / организации (МИЭТ, МГУ, МФТИ, ...)
    for m in re.finditer(r"\b(?:НИУ\s+)?([А-ЯЁ]{3,10})\b", head):
        abbr = m.group(1)
        if abbr not in ("СМИ", "ВУЗ", "ЕГЭ", "ОГЭ", "ГВЭ", "ФРП", "ООП", "ФГОС", "ОВЗ", "МЦД", "ЖКХ", "ДТП", "МЧС", "ФССП"):
            return abbr
    # 2. Латинское имя собственное (Dabro, ...)
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9-]{2,})\b", head):
        return m.group(1)
    # 3. Заглавное слово из заголовка (первая строка)
    first_line = head.splitlines()[0] if head.splitlines() else ""
    for w in re.findall(r"\b([А-ЯЁ][а-яё-]{3,})\b", first_line):
        if w.casefold() not in _ANCHOR_STOP_WORDS:
            return w
    return ""


def official_sites(text: str) -> list[str]:
    folded = text.casefold()
    extra = [d for words, domains in TOPIC_DOMAINS if any(w in folded for w in words) for d in domains]
    is_school = any(
        w in folded
        for w in ("школ", "учител", "урок", "егэ", "огэ", "фрп", "минпросвещ", "учебник", "детск", "ученик")
    )
    is_legal = any(
        w in folded
        for w in ("приказ", "закон", "постановлен", "указ", "распоряжен", "кодекс", "регламент", "норм")
    )
    base: list[str] = []
    if is_school or not extra:
        base.append("edu.gov.ru")
    if is_legal or not extra:
        base.append("pravo.gov.ru")
    return [*base, *dict.fromkeys(extra)]


def _scoped_claim_text(text: str, anchor_subject: str = "") -> str:
    base = (text or "").strip()
    anchor = (anchor_subject or "").strip()
    if anchor and anchor.casefold() not in base.casefold():
        return f"{anchor} {base}".strip()
    return base


def _concise_numeric_query(claim_text: str, anchor_subject: str = "") -> str:
    scoped = _scoped_claim_text(claim_text, anchor_subject)
    anchor_words = (anchor_subject or "").casefold().split()
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9%.\-]+", scoped)
    meaningful: list[str] = []
    seen_tok: set[str] = set()
    for t in tokens:
        tl = t.casefold().replace("ё", "е")
        if tl in _CONCISE_STOP_WORDS or tl in seen_tok:
            continue
        if any(c.isdigit() for c in t) or len(t) >= 4 or tl in anchor_words:
            seen_tok.add(tl)
            meaningful.append(t)
    if len(meaningful) > 8:
        must = [t for t in meaningful if t.casefold().replace("ё", "е") in anchor_words or any(c.isdigit() for c in t)]
        rest = [t for t in meaningful if t not in must][:max(0, 8 - len(must))]
        kept = set(must) | set(rest)
        return " ".join([t for t in meaningful if t in kept][:9])
    return " ".join(meaningful)


def build_queries(claim_text: str, anchor_subject: str = "") -> list[str]:
    """Сначала точный числовой запрос с якорной сущностью, затем очищенный официальный и весь веб."""
    scoped = _scoped_claim_text(claim_text, anchor_subject)
    text = scoped[:MAX_QUERY_CHARS]
    sites = " | ".join(f"site:{d}" for d in official_sites(scoped))
    raw_official = f"{text} ({sites})"
    sanitized_official = sanitize_query_for_free_engines(raw_official)
    concise = _concise_numeric_query(claim_text, anchor_subject=anchor_subject)

    queries: list[str] = []
    if anchor_subject and re.search(r"\b(?:годом\s+ранее|прошл\w+\s+году|в\s+2025)\b", claim_text.casefold()) and "заявлен" in claim_text.casefold():
        queries.append(f"{anchor_subject} итоги приемной кампании 2025")
    if concise and re.search(r"\d+%|\d{3,}", claim_text):
        queries.append(concise)
    queries.append(sanitized_official)
    if concise and concise not in queries:
        queries.append(concise)
    if text != sanitized_official:
        queries.append(text)
    else:
        folded = text.casefold()
        anchor = ""
        if any(w in folded for w in ("школ", "учител", "егэ", "огэ", "класс")):
            anchor = "Минпросвещения"
        elif any(w in folded for w in ("вуз", "студент", "университет", "абитуриент", "минобр", "миэт", "колледж")):
            anchor = "МИЭТ" if "миэт" in folded else "Минобрнауки"
        elif any(w in folded for w in ("зеленоград", "дк", "кц")):
            anchor = "mos.ru Зеленоград"
        elif not anchor_subject:
            anchor = "официальный сайт"
        if anchor and anchor.casefold() not in folded:
            queries.append(f"{text} {anchor}".strip())
    return list(dict.fromkeys(q for q in queries if q))[:3]


def build_targeted_refinement_query(claim_text: str, anchor_subject: str = "") -> str:
    """Формирует второй, прицельный запрос (Hop 2): сущность заголовка + ключевые слова + числа/даты."""
    scoped = _scoped_claim_text(claim_text, anchor_subject)
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9%\-]+", scoped)
    meaningful = []
    for t in tokens:
        tl = t.casefold().replace("ё", "е")
        if tl in _CONCISE_STOP_WORDS:
            continue
        if any(c.isdigit() for c in t) or len(t) >= 4:
            meaningful.append(t)
    return " ".join(meaningful[:8])



def _rank_search_hits(hits: list[SearchResult], topic_domains: tuple[str, ...] = ()) -> list[SearchResult]:
    """Ранжирует выдачу: официальные сайты РФ и профильные домены темы получают приоритет перед СМИ."""
    def _score(h: SearchResult) -> int:
        parts = urlsplit(h.url)
        host = (parts.hostname or "").removeprefix("www.").casefold()
        path = (parts.path or "").casefold()
        penalty = 0
        if path in ("", "/", "/index.html", "/index.php"):
            penalty += 130
        if _matches(host, ("wikipedia.org",)) and re.search(r"/wiki/\d{4}(?:_|$)", path):
            penalty += 160
        if _matches(host, ("russkiypro.ru", "orfographia.ru", "gramota.ru")):
            penalty += 160
        if _matches(host, OFFICIAL_DOMAINS):
            base = 200 - h.rank
        elif topic_domains and any(_matches(host, (d,)) for d in topic_domains):
            base = 150 - h.rank
        elif _matches(host, MEDIA_DOMAINS):
            base = 80 - h.rank
        else:
            base = 20 - h.rank
        return base - penalty
    return sorted(hits, key=_score, reverse=True)


def _squash(text: str) -> str:
    return " ".join(text.split())


def is_copy_paste_source(
    source_text: str,
    checked_text: str,
    *,
    is_linked: bool = False,
    kind: str = "media",
) -> bool:
    """Определяет, является ли источник перепечаткой/копипастом самого проверяемого текста.
    Страницы по прямым ссылкам новости и официальные порталы защищены от отбрасывания."""
    if is_linked or kind in ("official", "user_attachment", "ledger"):
        return False
    sentences = [_squash(s) for s in re.split(r"(?<=[.!?…])\s+|\n+", checked_text) if s.strip()]
    own = [s for s in sentences if len(s) >= COPY_SENTENCE_CHARS]
    if len(own) < COPY_MATCHES:
        return False
    squashed = _squash(source_text)
    return sum(s in squashed for s in own) >= COPY_MATCHES


class Sources:
    def __init__(self, meter: Meter, checked_text: str = "", network_allowed: bool = True) -> None:
        self.meter = meter
        self.network_allowed = network_allowed
        self.timeout = 5.0
        self.errors: dict[str, None] = {}  # сбои поиска без повторов, в порядке появления
        self.claim_failures: dict[str, str] = {}  # утверждения, где все источники недоступны (T505)
        self.hits = 0
        self.fetch_failed = 0
        self.linked: list[Doc] = []  # прочитанные страницы по ссылкам из самого текста
        self.attachments: list[object] = []
        self._docs: dict[str, Doc] = {}
        self._checked_text_raw = checked_text
        sentences = (_squash(s) for s in re.split(r"(?<=[.!?…])\s+|\n+", checked_text))
        self._own = [s for s in sentences if len(s) >= COPY_SENTENCE_CHARS]
        self._lock = threading.Lock()
        self.cache = SearchCache()
        self.local_index = LocalKnowledgeIndex()
        self.direct_apis = DirectAPIs(meter=self.meter)
        self.search_engine = FreeRuNetSearch(cache=self.cache, meter=self.meter)

    def _validate_page_content(self, text: str, *, kind: str, is_linked: bool) -> tuple[str, str]:
        """Проверяет качество текста страницы и копипаст. Возвращает (clean_text, error)."""
        if not text or not text.strip():
            return "", "пустой текст после очистки HTML"
        if is_copy_paste_source(text, self._checked_text_raw, is_linked=is_linked, kind=kind):
            return "", "копия проверяемого текста — не независимый источник"
        return text, ""

    def _load(self, url: str, is_linked: bool = False, timeout: float | None = None) -> Doc:
        if url in self._docs:
            if is_linked:
                self._docs[url].is_linked = True
            return self._docs[url]

        from bormochecker.retrieval import is_aggregator_or_search_url

        if is_aggregator_or_search_url(url):
            doc = Doc(
                id=f"d{len(self._docs) + 1}",
                url=url,
                title=url,
                kind="aggregator",
                text="",
                error="страница-агрегатор или поисковая выдача — не является источником",
                is_linked=is_linked,
            )
            self._docs[url] = doc
            return doc

        cached_page = self.cache.get_page(url)
        if cached_page:
            kind = publisher_kind((urlsplit(url).hostname or "").removeprefix("www."))
            clean_text, error = self._validate_page_content(cached_page[1], kind=kind, is_linked=is_linked)
            doc = Doc(
                id=f"d{len(self._docs) + 1}",
                url=url,
                title=cached_page[0] or url,
                kind=kind,
                text=clean_text,
                error=error,
                is_linked=is_linked,
            )
            self._docs[url] = doc
            self.meter.add_cache_hit()
            return doc

        if not self.network_allowed:
            doc = Doc(
                id=f"d{len(self._docs) + 1}",
                url=url,
                title=url,
                kind=publisher_kind((urlsplit(url).hostname or "").removeprefix("www.")),
                text="",
                error="сетевые запросы запрещены в текущем режиме (network_allowed=False)",
                is_linked=is_linked,
            )
            self._docs[url] = doc
            self.fetch_failed += 1
            return doc

        with self._lock:
            doc = Doc(id=f"d{len(self._docs) + 1}", url=url, is_linked=is_linked)
            self._docs[url] = doc
        self.meter.add_fetch()
        try:
            _fetcher.meter = self.meter
            page = fetch_page(url, timeout=timeout)
        except (FetchError, UnsafeUrlError, TimeoutError, OSError, Exception) as error:
            doc.error = str(error)[:300]
            with self._lock:
                self.fetch_failed += 1
            return doc
        final_url = page.final_url or url
        doc.url = final_url
        doc.kind = publisher_kind((urlsplit(doc.url).hostname or "").removeprefix("www."))
        doc.title = page.title
        clean_text, error = self._validate_page_content(page.text, kind=doc.kind, is_linked=is_linked)
        if error:
            doc.error = error
        else:
            doc.text = clean_text
            self.cache.put_page(doc.url, doc.title, doc.text)
            if final_url != url:
                self.cache.put_page(url, doc.title, doc.text)
                self._docs[final_url] = doc
        return doc

    def preload(self, text: str, *, deadline: Any | None = None) -> None:
        """Ссылки из текста и пояснения — источник, который назвала сама редакция: читаем без поиска."""
        for url in dict.fromkeys(u.rstrip(".,;:!?") for u in URL_RE.findall(text)):
            if deadline is not None and deadline.expired():
                break
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            if is_blocked_domain_host(host):
                continue
            if deadline is not None:
                doc = self._load(url, is_linked=True, timeout=deadline.timeout(self.timeout))
            else:
                doc = self._load(url, is_linked=True)
            if doc.text and doc not in self.linked:
                self.linked.append(doc)

    def preload_local_docs(self, attachments: list[object]) -> None:
        """Предзагрузка локальных распакованных документов из AttachmentManifest с наивысшим приоритетом."""
        self.attachments = list(attachments)
        for att in attachments:
            if not att:
                continue
            text = ""
            title = "Вложение"
            url = ""
            if hasattr(att, "extracted_text"):
                text = getattr(att, "extracted_text", "")
                title = getattr(att, "file_name", "Вложение")
                storage_path = getattr(att, "storage_path", "")
                url = f"file://{storage_path}" if storage_path else f"attachment://{title}"
            elif isinstance(att, dict):
                text = att.get("extracted_text") or att.get("text") or ""
                title = att.get("file_name") or att.get("title") or "Вложение"
                url = att.get("storage_path") or f"attachment://{title}"
            if not text:
                continue

            doc_id = f"local_att_{len(self._docs) + 1}"
            doc = Doc(
                id=doc_id,
                url=url,
                title=title,
                kind="user_attachment",
                text=text,
            )
            self._docs[url] = doc
            self.linked.append(doc)

    def search_local_fts(self, query: str, limit: int = 3) -> list[Doc]:
        """Уровень 0: локальный поиск по FTS5-индексу документов (0 ₽, 0 мс)."""
        db_path = knowledge_fts_path()
        if not db_path.exists():
            return []
        try:
            hits = self.local_index.search_docs(query, limit=limit)
            docs = []
            for h in hits:
                url = h.url or "fts://knowledge/local"
                if url in self._docs:
                    docs.append(self._docs[url])
                    continue
                doc = Doc(
                    id=f"fts_{len(self._docs) + 1}",
                    url=url,
                    title=h.title or "ФРП / Нормативный акт",
                    kind=h.kind or "official",
                    text=h.text or h.title or "",
                )
                self._docs[url] = doc
                docs.append(doc)
            return docs
        except Exception:
            return []

    def pool(self) -> list[Doc]:
        """Все прочитанные за проверку страницы, отсортированные детерминированно."""
        docs = [d for d in self._docs.values() if d.text]
        docs.sort(
            key=lambda d: (
                0 if getattr(d, "kind", "") in ("user_attachment", "attachment")
                else (1 if getattr(d, "kind", "") == "official" else 2),
                d.url,
                d.title or "",
            )
        )
        return docs


    def _is_high_confidence_pool_match(self, claim_text: str) -> bool:
        """Проверяет, есть ли в уже прочитанных НЕ-локальных документах исчерпывающий кандидат."""
        from decimal import Decimal
        from bormochecker.numerals import extract_quantities

        valid_docs = [
            d for d in self.pool()
            if not d.url.startswith("fts://") and not d.url.startswith("https://edsoo.ru/local/")
        ]
        if not valid_docs:
            return False

        from bormochecker import evaluate
        candidates = evaluate.find_candidates(claim_text, valid_docs)
        if not candidates:
            return False
        best = candidates[0]
        quote_text = best.get("quote", "").casefold().replace("ё", "е")
        claim_stems = evaluate._stems(claim_text)
        quote_stems = evaluate._stems(quote_text)
        shared_stems = claim_stems & quote_stems
        if not shared_stems:
            return False

        # Не менее 60% смысловых нечисловых лемм (минимум 1 при коротком утверждении)
        non_num_claim = {s for s in claim_stems if not s.isdigit()}
        if non_num_claim:
            shared_non_num = {s for s in shared_stems if not s.isdigit()}
            needed = 1 if len(non_num_claim) <= 2 else max(2, int(len(non_num_claim) * 0.6))
            if len(shared_non_num) < needed:
                return False

        # Числа и проценты: если конкретные числа/проценты утверждения (напр. 47%, 261, 5763)
        # ещё ни разу не встречались ни в одном загруженном документе пула — веб-поиск пропускать нельзя!
        claim_numbers = set(re.findall(r"\d+", claim_text))
        quote_numbers = set(re.findall(r"\d+", quote_text))
        if claim_numbers and not quote_numbers:
            return False

        pool_nums = set(re.findall(r"\d+", " ".join(d.text for d in valid_docs)))
        c_quants = extract_quantities(claim_text)
        q_quants = extract_quantities(quote_text)
        covered_quant_digits: set[str] = set()
        for cq in c_quants:
            raw_digits = re.findall(r"\d+", cq.raw or "")
            if cq.dim == "YEAR" or (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1))):
                covered_quant_digits.update(raw_digits)
                continue
            if any(cq.matches(dq) for dq in q_quants):
                covered_quant_digits.update(raw_digits)
                continue
            if cq.dim == "DURATION" and any(dq.dim == "DURATION" for dq in q_quants):
                covered_quant_digits.update(raw_digits)
                continue
            if raw_digits and not all(d in pool_nums for d in raw_digits):
                return False
            if cq.dim in ("PERCENT", "COUNT") and raw_digits and not all(d in quote_numbers for d in raw_digits):
                return False

        for num in claim_numbers - covered_quant_digits:
            if num not in ("0", "1") and num not in pool_nums:
                return False

        return True

    def gather_for(
        self,
        claim_text: str,
        limit: int = 3,
        *,
        mode: str = "education",
        importance: str = "medium",
        query: str = "",
        deadline: Any | None = None,
    ) -> list[Doc]:
        """Многоуровневый каскад сбора источников с умным пропуском и Multi-Hop поиском:
        Шаг 0: Проверка пула (предзагруженные вложения/ссылки)
        Шаг А: Локальный индекс ФРП и зеркал (только для образовательных/нормативных тем)
        Шаг Б: Прямые бесключевые API (Википедия, pravo.gov.ru, mos.ru, КиберЛенинка)
        Шаг В: Проверка пула перед сетевым веб-поиском
        Шаг Г: Каскадный веб-поиск FreeRuNetSearch (Hop 1)
        Шаг Д: Multi-Hop (Hop 2) Targeted Query Refinement при отсутствии точного совпадения
        """
        docs: list[Doc] = []
        outcomes_for_claim: list[str] = []
        if deadline is not None and deadline.expired():
            return docs

        search_target = query.strip() or claim_text.strip()

        # Шаг 0: Проверка пула (только при исчерпывающем покрытии веб/вложениями)
        if self._is_high_confidence_pool_match(claim_text):
            return docs

        # Шаг А: Локальный FTS5 индекс (опрашивается для образования/нормативов, но не при наличии предзагруженных вложений)
        from bormochecker.local_index import is_education_or_normative_query
        has_attachments = bool(self.attachments) or any(
            getattr(d, "kind", "") in ("user_attachment", "attachment")
            for d in self._docs.values()
        )
        if not has_attachments and (mode == "local_frp" or is_education_or_normative_query(search_target, mode=mode)):
            local_hits = self.local_index.search_docs(search_target, limit=limit)
            if local_hits:
                self.meter.add_local_fts_hit(len(local_hits))
                for lh in local_hits:
                    if lh.url in self._docs:
                        doc = self._docs[lh.url]
                    else:
                        doc = Doc(
                            id=f"loc_{len(self._docs) + 1}",
                            url=lh.url,
                            title=lh.title,
                            kind="official",
                            text=lh.text,
                        )
                        self._docs[lh.url] = doc
                        self.linked.append(doc)
                    if doc not in docs:
                        docs.append(doc)
        if mode == "local_frp":
            return docs[:limit]

        if deadline is not None and deadline.expired():
            return docs[:limit]

        # Шаг Б: Прямые API
        try:
            direct_hits = self.direct_apis.fetch_candidates(search_target, limit=limit, deadline=deadline)
        except Exception as error:
            direct_hits = []
            outcomes_for_claim.append("provider_error")
            self.errors[f"direct_apis: {error}"] = None
        for dh in direct_hits:
            if dh.url in self._docs:
                doc = self._docs[dh.url]
            else:
                doc = Doc(
                    id=f"dir_{len(self._docs) + 1}",
                    url=dh.url,
                    title=dh.title,
                    kind="official" if dh.kind == "official" else "unknown",
                    text=dh.text,
                )
                self._docs[dh.url] = doc
                self.linked.append(doc)
            if doc not in docs:
                docs.append(doc)

        # Шаг В: Проверка пула перед сетевым веб-поиском
        if self._is_high_confidence_pool_match(claim_text):
            return docs[:limit]

        if mode == "local_frp":
            return docs[:limit]

        if deadline is not None and deadline.expired():
            return docs[:limit]

        # Шаг Г: Бесплатный каскадный поиск (Hop 1)
        allow_paid = mode == "journalism_full" and importance == "high"
        self.search_engine.allow_paid_fallback = allow_paid
        seen = set()
        anchor_subject = extract_article_subject(self._checked_text_raw)
        queries = build_queries(search_target, anchor_subject=anchor_subject)
        if query and re.search(r"\d+%|\d{3,}", claim_text):
            claim_nums = set(re.findall(r"\d+%|\d{3,}", claim_text))
            if not claim_nums.issubset(set(re.findall(r"\d+%|\d{3,}", search_target))):
                c_num_q = _concise_numeric_query(claim_text, anchor_subject=anchor_subject)
                if c_num_q:
                    queries = list(dict.fromkeys([c_num_q, *queries]))[:3]
        pcts = re.findall(r"\b\d+(?:[.,]\d+)?\s*%", claim_text)
        if pcts and self._checked_text_raw:
            pos = self._checked_text_raw.find(pcts[0])
            if pos > 0:
                prev_win = self._checked_text_raw[max(0, pos - 220):pos]
                ctx_nums = [
                    m for m in re.findall(r"\b\d{4,6}\s+[а-яёА-ЯЁ]{4,14}\b", prev_win)
                    if not re.match(r"^(?:19|20)\d\d\b", m)
                ]
                if ctx_nums:
                    ctx_q = " ".join(p for p in (anchor_subject, ctx_nums[-1], pcts[0]) if p)
                    queries = list(dict.fromkeys([ctx_q, *queries]))[:3]
        claim_end = time.monotonic() + 8.0
        docs_added = 0
        for q in queries[:2]:
            if (
                (len(docs) >= limit and self._is_high_confidence_pool_match(claim_text))
                or docs_added >= 2
                or time.monotonic() >= claim_end
                or (deadline is not None and deadline.expired())
            ):
                break
            try:
                hits = self.search_engine.search(q, limit=limit * 2)
                outcome = getattr(self.search_engine, "last_outcome", "ok" if hits else "empty")
            except SearchError as error:
                paid = self.search_engine.last_was_paid
                prov = self.search_engine.last_provider
                self.meter.add_search(paid=paid, provider=prov)
                self.errors[str(error)] = None
                outcomes_for_claim.append("provider_error")
                continue
            except Exception as error:
                paid = getattr(self.search_engine, "last_was_paid", False)
                prov = getattr(self.search_engine, "last_provider", "none")
                self.meter.add_search(paid=paid, provider=prov)
                self.errors[str(error)] = None
                outcomes_for_claim.append("provider_error")
                continue
            paid = self.search_engine.last_was_paid
            prov = self.search_engine.last_provider
            self.meter.add_search(paid=paid, provider=prov)
            if prov == "sqlite_cache":
                self.meter.add_cache_hit()
            self.hits += len(hits)
            outcomes_for_claim.append(outcome)

            ranked = _rank_search_hits(hits)
            to_fetch = _select_urls_to_fetch(ranked, seen, limit=2)

            if to_fetch:
                docs_before = len(docs)
                if deadline is not None:
                    fetch_timeout = deadline.timeout(self.timeout)
                    fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(to_fetch)))
                    try:
                        fetch_futures = [
                            fetch_executor.submit(self._load, u, timeout=fetch_timeout) for u in to_fetch
                        ]
                        for fut in concurrent.futures.as_completed(fetch_futures, timeout=fetch_timeout):
                            try:
                                d = fut.result()
                                if d.text and d not in docs:
                                    docs.append(d)
                                if len(docs) >= limit:
                                    break
                            except Exception:
                                pass
                    except (concurrent.futures.TimeoutError, TimeoutError):
                        pass
                    finally:
                        fetch_executor.shutdown(wait=False, cancel_futures=True)
                else:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(to_fetch))) as executor:
                        fetched_docs = list(executor.map(self._load, to_fetch))
                    for d in fetched_docs:
                        if d.text and d not in docs:
                            docs.append(d)
                docs_added += max(0, len(docs) - docs_before)
                if len(docs) == docs_before and self.fetch_failed:
                    outcomes_for_claim[-1] = "fetch_failed"

        # Шаг Д: Multi-Hop (Hop 2) Targeted Query Refinement
        if (
            docs_added < 2
            and time.monotonic() < claim_end
            and not (deadline is not None and deadline.expired())
            and not self._is_high_confidence_pool_match(claim_text)
        ):
            hop2_query = build_targeted_refinement_query(claim_text, anchor_subject=anchor_subject)
            if hop2_query and hop2_query not in queries:
                try:
                    hits = self.search_engine.search(hop2_query, limit=limit * 2)
                    outcome = getattr(self.search_engine, "last_outcome", "ok" if hits else "empty")
                    paid = self.search_engine.last_was_paid
                    prov = self.search_engine.last_provider
                    self.meter.add_search(paid=paid, provider=prov)
                    if prov == "sqlite_cache":
                        self.meter.add_cache_hit()
                    self.hits += len(hits)
                    outcomes_for_claim.append(outcome)
                    ranked = _rank_search_hits(hits)
                    to_fetch = _select_urls_to_fetch(ranked, seen, limit=3)
                    if to_fetch and not (deadline is not None and deadline.expired()):
                        docs_before = len(docs)
                        fetch_timeout = deadline.timeout(self.timeout) if deadline is not None else self.timeout
                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(to_fetch))) as executor:
                            fetched_docs = list(executor.map(lambda u: self._load(u, timeout=fetch_timeout), to_fetch))
                        for d in fetched_docs:
                            if d.text and d not in docs:
                                docs.append(d)
                            if len(docs) >= limit:
                                break
                        if len(docs) == docs_before and self.fetch_failed:
                            outcomes_for_claim[-1] = "fetch_failed"
                except SearchError as error:
                    paid = self.search_engine.last_was_paid
                    prov = self.search_engine.last_provider
                    self.meter.add_search(paid=paid, provider=prov)
                    self.errors[str(error)] = None
                    outcomes_for_claim.append("provider_error")
                except Exception as error:
                    paid = getattr(self.search_engine, "last_was_paid", False)
                    prov = getattr(self.search_engine, "last_provider", "none")
                    self.meter.add_search(paid=paid, provider=prov)
                    self.errors[str(error)] = None
                    outcomes_for_claim.append("provider_error")

        if not docs and outcomes_for_claim:
            if all(o in ("provider_error", "timeout", "fetch_failed", "blocked") for o in outcomes_for_claim):
                self.claim_failures[claim_text] = "источники недоступны"
                self.errors["источники недоступны: сбой провайдеров поиска"] = None

        return docs[:limit]

    def retrieve(
        self,
        claim_text: str,
        limit: int,
        *,
        mode: str = "education",
        importance: str = "medium",
    ) -> list[Doc]:
        return self.gather_for(claim_text, limit=limit, mode=mode, importance=importance)

