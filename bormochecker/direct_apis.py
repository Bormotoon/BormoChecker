"""Ступень 1 поиска: прямые бесплатные JSON API Рунета без ключей и капчи.
Зачем: энциклопедические факты (даты основания организаций, биографии, география, классика),
номера приказов/законов и научно-методические тезисы быстрее и надёжнее брать напрямую из
MediaWiki API (ru.wikipedia.org), publication.pravo.gov.ru и КиберЛенинки, чем парсить поисковую выдачу."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from bormochecker.retrieval import (
    BLOCKED_DOMAINS,
    NetworkNotAllowedError,
    is_blocked_host,
    network_allowed_ctx,
)

__all__ = [
    "BLOCKED_DOMAINS",
    "CyberLeninkaAPI",
    "DirectAPIs",
    "DirectDocument",
    "FgosReestrAPI",
    "MosRuSuggestAPI",
    "PravoGovAPI",
    "RuWikipediaAPI",
    "is_blocked_domain",
]

UA = "BormoChecker/1.0 (+https://github.com/Bormotoon/BormoChecker)"

_NORMATIVE_RE = re.compile(
    r"\b(?:приказ[а-я]*|постановлени[еяюй]|указ[а-я]*|распоряжени[ея]|санпин[а-я]*|фгос|фз-\d+|№\s*\d+)\b",
    re.IGNORECASE,
)

_SCIENCE_RE = re.compile(
    r"\b(?:исследован[а-я]*|биолог[а-я]*|медицин[а-я]*|психолог[а-я]*|методик[а-я]*|педагогик[а-я]*|вакцин[а-я]*|научн[а-я]*)\b",
    re.IGNORECASE,
)

_MOSCOW_RE = re.compile(
    r"\b(?:москв[а-я]*|зеленоград[а-я]*|мэш|собянин[а-я]*|мосгор[а-я]*|департамент образовани[а-я]*)\b",
    re.IGNORECASE,
)


def is_blocked_domain(url: str) -> bool:
    return is_blocked_host(url)


@dataclass(frozen=True)
class DirectDocument:
    url: str
    title: str
    text: str
    kind: str  # 'official' | 'wikipedia' | 'scientific' | 'other'


class RuWikipediaAPI:
    """Прямой клиент MediaWiki Action API Русской Википедии (0 ₽, без лимитов при корректном User-Agent)."""

    API_URL = "https://ru.wikipedia.org/w/api.php"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def search_and_extract(self, query: str, *, limit: int = 2) -> list[DirectDocument]:
        clean_q = re.sub(r"site:\S+|\|", " ", query).strip()
        if len(clean_q) < 3:
            return []
        params = urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": clean_q,
            "srlimit": str(limit),
            "format": "json",
            "utf8": "1",
        })
        try:
            req = urllib.request.Request(f"{self.API_URL}?{params}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        search_hits = data.get("query", {}).get("search", [])
        if not search_hits:
            return []

        page_ids = [str(h["pageid"]) for h in search_hits if "pageid" in h][:limit]
        if not page_ids:
            return []

        ext_params = urllib.parse.urlencode({
            "action": "query",
            "pageids": "|".join(page_ids),
            "prop": "extracts|info",
            "inprop": "url",
            "explaintext": "1",
            "exchars": "15000",
            "format": "json",
            "utf8": "1",
        })
        try:
            req2 = urllib.request.Request(f"{self.API_URL}?{ext_params}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req2, timeout=self.timeout) as resp2:
                ext_data = json.loads(resp2.read().decode("utf-8"))
        except Exception:
            return []

        pages = ext_data.get("query", {}).get("pages", {})
        docs: list[DirectDocument] = []
        for pid in page_ids:
            p = pages.get(pid) or {}
            extract = (p.get("extract") or "").strip()
            title = (p.get("title") or "").strip()
            fullurl = p.get("fullurl") or f"https://ru.wikipedia.org/?curid={pid}"
            if is_blocked_domain(fullurl):
                continue
            if extract and len(extract) >= 80:
                docs.append(
                    DirectDocument(
                        url=fullurl,
                        title=f"{title} — Википедия",
                        text=f"{title}\n\n{extract}",
                        kind="wikipedia",
                    )
                )
        return docs


class PravoGovAPI:
    """Прямой поиск нормативных правовых актов на Официальном интернет-портале правовой информации (pravo.gov.ru)."""

    API_URL = "https://publication.pravo.gov.ru/api/Documents"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def is_applicable(self, text: str) -> bool:
        return bool(_NORMATIVE_RE.search(text or ""))

    def search_acts(self, query: str, *, limit: int = 3) -> list[DirectDocument]:
        clean_q = re.sub(r"site:\S+|\|", " ", query).strip()
        if not self.is_applicable(clean_q):
            return []
        params: dict[str, str] = {"PageSize": str(limit), "Index": "1"}
        num_match = re.search(r"№\s*([0-9]+(?:-[а-яА-Яa-zA-Z0-9/]+)?)", clean_q)
        if num_match:
            params["Number"] = num_match.group(1)
        params["Name"] = re.sub(r"№\s*\S+", "", clean_q).strip()[:120]
        url = f"{self.API_URL}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        items = data.get("items") or []
        docs: list[DirectDocument] = []
        for it in items[:limit]:
            eo_num = it.get("eoNumber") or ""
            complex_name = (it.get("complexName") or it.get("name") or "").strip()
            doc_date = it.get("documentDate") or it.get("publishDateShort") or ""
            num = it.get("number") or ""
            if not complex_name:
                continue
            doc_url = f"https://publication.pravo.gov.ru/document/{eo_num}" if eo_num else "https://pravo.gov.ru/"
            if is_blocked_domain(doc_url):
                continue
            body_text = (
                f"Официальный правовой акт: {complex_name}\n"
                f"Номер документа: {num}\n"
                f"Дата документа: {doc_date}\n"
                f"Номер опубликования: {eo_num}\n"
                f"Статус: Официально опубликован на pravo.gov.ru."
            )
            docs.append(DirectDocument(url=doc_url, title=complex_name[:140], text=body_text, kind="official"))
        return docs


class CyberLeninkaAPI:
    """Прямой поиск научных статей на КиберЛенинке (cyberleninka.ru)."""

    API_URL = "https://cyberleninka.ru/api/search"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def is_applicable(self, text: str) -> bool:
        return bool(_SCIENCE_RE.search(text or ""))

    def search_articles(self, query: str, *, limit: int = 2) -> list[DirectDocument]:
        clean_q = re.sub(r"site:\S+|\|", " ", query).strip()
        if not self.is_applicable(clean_q) or len(clean_q) < 4:
            return []
        payload = json.dumps({"q": clean_q, "page": 1, "size": limit}).encode("utf-8")
        req = urllib.request.Request(
            self.API_URL,
            data=payload,
            headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        articles = data.get("articles") or []
        docs: list[DirectDocument] = []
        for art in articles[:limit]:
            title = (art.get("name") or "").strip()
            url = art.get("link") or ""
            if url and not url.startswith("http"):
                url = f"https://cyberleninka.ru{url}"
            annotation = (art.get("annotation") or "").strip()
            if not title or is_blocked_domain(url):
                continue
            text = f"{title}\n\nАннотация: {annotation}" if annotation else title
            docs.append(DirectDocument(url=url or "https://cyberleninka.ru", title=title, text=text, kind="scientific"))
        return docs


class MosRuSuggestAPI:
    """Поиск по публичным новостям и материалам mos.ru."""

    API_URL = "https://www.mos.ru/api/newsfeed/v4/frontend/json/ru/articles"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def is_applicable(self, text: str) -> bool:
        return bool(_MOSCOW_RE.search(text or ""))

    def search_mos_ru(self, query: str, *, limit: int = 2) -> list[DirectDocument]:
        clean_q = re.sub(r"site:\S+|\|", " ", query).strip()
        if not self.is_applicable(clean_q) or len(clean_q) < 4:
            return []
        params = urllib.parse.urlencode({"q": clean_q, "per-page": str(limit)})
        url = f"{self.API_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        items = data.get("items") or []
        docs: list[DirectDocument] = []
        for item in items[:limit]:
            title = (item.get("title") or "").strip()
            item_url = item.get("url") or ""
            if item_url and not item_url.startswith("http"):
                item_url = f"https://www.mos.ru{item_url}"
            lead = (item.get("lead") or item.get("annotation") or "").strip()
            if not title or is_blocked_domain(item_url):
                continue
            text = f"{title}\n\n{lead}" if lead else title
            docs.append(DirectDocument(url=item_url or "https://www.mos.ru", title=title, text=text, kind="official"))
        return docs


class FgosReestrAPI:
    """Прямой поиск по реестру ФГОС / ФАООП ОВЗ (fgosreestr.edsoo.ru)."""

    API_URL = "https://fgosreestr.edsoo.ru/api/search"

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def is_applicable(self, text: str) -> bool:
        return bool(re.search(r"\b(?:фгос|фаооп|овз|реестр\s+программ|аооп)\b", text or "", re.IGNORECASE))

    def search_fgos(self, query: str, *, limit: int = 2) -> list[DirectDocument]:
        clean_q = re.sub(r"site:\S+|\|", " ", query).strip()
        if not self.is_applicable(clean_q) or len(clean_q) < 3:
            return []
        try:
            params = urllib.parse.urlencode({"query": clean_q, "limit": str(limit)})
            req = urllib.request.Request(
                f"{self.API_URL}?{params}",
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items") or data.get("data") or []
            docs = []
            for it in items[:limit]:
                title = it.get("title") or it.get("name") or "ФГОС Реестр"
                url = it.get("url") or f"https://fgosreestr.edsoo.ru/reestr/{it.get('id', '')}"
                text = it.get("description") or it.get("text") or title
                docs.append(DirectDocument(url=url, title=title, text=text, kind="official"))
            return docs
        except Exception:
            return []


def _is_direct_api_enabled(env_var: str, default: str = "0") -> bool:
    return os.environ.get(env_var, default).strip().casefold() in {"1", "true", "yes", "on"}


class DirectAPIs:
    """Фасад Ступени 1: объединяет прямые API (Википедия, pravo.gov.ru, fgosreestr, КиберЛенинка, mos.ru)."""

    def __init__(self, meter: Any | None = None) -> None:
        self.meter = meter
        self.wiki = RuWikipediaAPI(timeout=5.0)
        self.pravo = PravoGovAPI(timeout=5.0)
        self.fgos = FgosReestrAPI(timeout=5.0)
        self.cyberleninka = CyberLeninkaAPI(timeout=5.0)
        self.mos_ru = MosRuSuggestAPI(timeout=5.0)

    def _record_http(self, provider: str) -> None:
        if self.meter is not None and hasattr(self.meter, "record_http_request"):
            self.meter.record_http_request(provider)

    def fetch_candidates(self, query: str, *, limit: int = 4, deadline: Any | None = None) -> list[DirectDocument]:
        import concurrent.futures

        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError("Сетевые вызовы DirectAPIs запрещены в офлайн/локальном режиме")

        if deadline is not None and deadline.expired():
            return []

        task_timeout = 5.0
        if deadline is not None:
            task_timeout = deadline.timeout(5.0)
            if task_timeout <= 0.05:
                return []

        tasks = []
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        try:
            if _is_direct_api_enabled("FACTCHECK_ENABLE_PRAVOGOV", "0") and self.pravo.is_applicable(query):
                self._record_http("pravo_gov")
                tasks.append(executor.submit(self.pravo.search_acts, query, limit=limit))
            if _is_direct_api_enabled("FACTCHECK_ENABLE_FGOS", "0") and self.fgos.is_applicable(query):
                self._record_http("fgos_reestr")
                tasks.append(executor.submit(self.fgos.search_fgos, query, limit=limit))
            if _is_direct_api_enabled("FACTCHECK_ENABLE_MOSRU", "0") and self.mos_ru.is_applicable(query):
                self._record_http("mos_ru")
                tasks.append(executor.submit(self.mos_ru.search_mos_ru, query, limit=limit))
            if _is_direct_api_enabled("FACTCHECK_ENABLE_CYBERLENINKA", "0") and self.cyberleninka.is_applicable(query):
                self._record_http("cyberleninka")
                tasks.append(executor.submit(self.cyberleninka.search_articles, query, limit=limit))
            # Википедия проверена и включена по умолчанию (FACTCHECK_ENABLE_WIKIPEDIA=1)
            if _is_direct_api_enabled("FACTCHECK_ENABLE_WIKIPEDIA", "1"):
                self._record_http("wikipedia")
                tasks.append(executor.submit(self.wiki.search_and_extract, query, limit=limit))

            results: list[DirectDocument] = []
            seen_urls: set[str] = set()
            try:
                for fut in concurrent.futures.as_completed(tasks, timeout=task_timeout):
                    try:
                        docs = fut.result()
                        for d in docs:
                            if d.url not in seen_urls and not is_blocked_domain(d.url):
                                seen_urls.add(d.url)
                                results.append(d)
                    except Exception:
                        pass
            except (concurrent.futures.TimeoutError, TimeoutError):
                pass
            return results
        finally:
            # Отказ от ожидания зависших потоков
            executor.shutdown(wait=False, cancel_futures=True)

        return results[:limit]
