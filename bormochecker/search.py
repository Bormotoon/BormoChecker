"""Yandex Search API v2 adapter and Zero-Cost FreeRuNetSearch cascade."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bormochecker.retrieval import (
    NetworkNotAllowedError,
    is_blocked_host,
    network_allowed_ctx,
)
from bormochecker.search_cache import SearchCache


def is_blocked_search_result(url: str) -> bool:
    return is_blocked_host(url)


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    rank: int = 0


_MULTI_SITE_RE = re.compile(r"\(\s*site:[^)]+\|\s*site:[^)]+\)", re.IGNORECASE)


def clean_query_for_free_search(query: str) -> str:
    """Убирает конструкции '(site:a | site:b | ...)', которые обнуляют выдачу в DDG/SearXNG/Tavily."""
    cleaned = _MULTI_SITE_RE.sub("", query or "")
    # Убираем также цепочки без скобок
    cleaned = re.sub(r"site:[a-zA-Z0-9.\-]+(?:\s*\|\s*site:[a-zA-Z0-9.\-]+)+", " ", cleaned)
    cleaned = re.sub(r"\|", " ", cleaned)
    return " ".join(cleaned.split()).strip()


class YandexSearch:
    endpoint = "https://searchapi.api.cloud.yandex.net/v2/web/search"

    def __init__(self, api_key: str, *, timeout_seconds: float = 30.0) -> None:
        if not api_key.strip():
            raise ValueError("Yandex API key пуст")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "YandexSearch":
        return cls(os.environ.get("YANDEX_CLOUD_API_KEY", ""))

    @staticmethod
    def parse_response(payload: dict[str, object], *, limit: int) -> list[SearchResult]:
        try:
            root = ET.fromstring(base64.b64decode(str(payload["rawData"])))
        except (KeyError, ValueError, ET.ParseError) as error:
            raise SearchError("Yandex вернул некорректный rawData") from error
        api_error = root.find(".//error")
        if api_error is not None:
            raise SearchError("Yandex Search API: " + "".join(api_error.itertext()).strip())
        results: list[SearchResult] = []
        for rank, doc in enumerate(root.findall(".//doc"), start=1):
            url_node = doc.find("url")
            title_node = doc.find("title")
            headline_node = doc.find(".//headline")
            passages = [p.text for p in doc.findall(".//passages/passage") if p.text]
            if url_node is None or not url_node.text:
                continue
            url = url_node.text.strip()
            if is_blocked_search_result(url):
                continue
            title = "".join(title_node.itertext()).strip() if title_node is not None else ""
            snippet = "".join(headline_node.itertext()).strip() if headline_node is not None else " ".join(filter(None, passages))
            results.append(SearchResult(title=title, url=url, snippet=snippet, rank=rank))
            if len(results) >= limit:
                break
        return results

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError("Сетевой поиск (YandexSearch) запрещён в офлайн/локальном режиме")
        body = json.dumps({"query": {"searchType": "SEARCH_TYPE_RU", "queryText": query, "page": 0}, "responseFormat": "FORMAT_XML"}).encode()
        request = Request(self.endpoint, data=body, headers={"Authorization": f"Api-Key {self._api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise SearchError(f"Yandex Search API недоступен: {error}") from error
        if not isinstance(payload, dict):
            raise SearchError("Yandex вернул не JSON-объект")
        return self.parse_response(payload, limit=limit)


class SearXNGSearch:
    """SearXNG JSON adapter; snippets remain navigation, not evidence."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0) -> None:
        if not base_url.strip():
            raise ValueError("SearXNG base URL пуст")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def parse_response(payload: dict[str, object], *, limit: int) -> list[SearchResult]:
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise SearchError("SearXNG вернул некорректный список результатов")
        results: list[SearchResult] = []
        for rank, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url or is_blocked_search_result(url):
                continue
            results.append(SearchResult(
                title=str(item.get("title", "")).strip(),
                url=url,
                snippet=str(item.get("content", "")).strip(),
                published_at=str(item.get("publishedDate", "")).strip(),
                rank=rank,
            ))
            if len(results) >= limit:
                break
        return results

    def _fetch_searxng_json(self, params: dict[str, str]) -> dict:
        url = self._base_url + "/search?" + urlencode(params)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "factcheck/0.1"})
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise SearchError(f"SearXNG недоступен: {error}") from error
        if not isinstance(payload, dict):
            raise SearchError("SearXNG вернул не JSON-объект")
        return payload

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError("Сетевой поиск (SearXNG) запрещён в офлайн/локальном режиме")
        payload = self._fetch_searxng_json({"q": query, "format": "json", "language": "ru"})
        results = self.parse_response(payload, limit=limit)
        good_cyr_hits = sum(
            1
            for r in results
            if any("\u0400" <= c <= "\u04ff" for c in r.title + r.snippet)
            and not r.url.casefold().split("?", 1)[0].endswith(".pdf")
        )
        unresp = payload.get("unresponsive_engines") or []
        if not results or len(unresp) >= 3 or (bool(unresp) and good_cyr_hits < 2):
            try:
                alt_payload = self._fetch_searxng_json({
                    "q": query,
                    "format": "json",
                    "engines": "yahoo,bing",
                })
                alt_results = self.parse_response(alt_payload, limit=limit)
                seen = {r.url.rstrip("/") for r in alt_results}
                results = (alt_results + [r for r in results if r.url.rstrip("/") not in seen])[:limit]
            except SearchError:
                pass
        return results


class DDGSSearch:
    """Бесплатный бесключевой поиск по Рунету (region='ru-ru') через пакет ddgs
    с автоматическим stdlib-фоллбэком на DuckDuckGo Lite HTML при отсутствии пакета."""

    def __init__(self, *, timeout_seconds: float = 3.0) -> None:
        self.timeout_seconds = min(timeout_seconds, 3.0)
        self._fails = 0

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError("Сетевой поиск (DDGSSearch) запрещён в офлайн/локальном режиме")
        if self._fails >= 2:
            return []
        clean_q = clean_query_for_free_search(query)
        if not clean_q:
            return []
        # 1. Пробуем пакет ddgs
        try:
            from ddgs import DDGS  # type: ignore

            raw_hits = DDGS(timeout=int(self.timeout_seconds)).text(
                clean_q, region="ru-ru", safesearch="off", max_results=limit
            )
            results: list[SearchResult] = []
            for idx, item in enumerate(raw_hits or [], start=1):
                url = (item.get("href") or item.get("url") or "").strip()
                if not url.startswith(("http://", "https://")) or is_blocked_search_result(url):
                    continue
                results.append(
                    SearchResult(
                        title=(item.get("title") or url).strip(),
                        url=url,
                        snippet=(item.get("body") or item.get("snippet") or "").strip(),
                        rank=idx,
                    )
                )
            if results:
                self._fails = 0
                return results[:limit]
        except Exception:
            pass

        # 2. Чистый stdlib-фоллбэк через DuckDuckGo HTML (kl=ru-ru)
        try:
            data = urllib.parse.urlencode({"q": clean_q, "kl": "ru-ru"}).encode("utf-8")
            req = urllib.request.Request(
                "https://html.duckduckgo.com/html/",
                data=data,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                page = resp.read().decode("utf-8", errors="ignore")
            results = []
            pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.IGNORECASE | re.DOTALL,
            )
            for idx, m in enumerate(pattern.finditer(page), start=1):
                raw_href = html.unescape(m.group(1))
                if "uddg=" in raw_href:
                    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_href).query)
                    raw_href = qs.get("uddg", [raw_href])[0]
                if not raw_href.startswith(("http://", "https://")) or is_blocked_search_result(raw_href):
                    continue
                title_clean = re.sub(r"<[^>]+>", "", html.unescape(m.group(2))).strip()
                results.append(SearchResult(title=title_clean or raw_href, url=raw_href, snippet="", rank=idx))
                if len(results) >= limit:
                    break
            if results:
                self._fails = 0
                return results
        except Exception:
            pass

        self._fails += 1
        return []


class CloudFreeRotator:
    """Ротатор бесплатных тарифов облачных Search API (Parallel, Tavily, Firecrawl, Serper, SerpApi)
    с жёстким контролем месячных лимитов в SQLite (0 ₽, без привязки карты)."""

    def __init__(self, cache: SearchCache, *, timeout_seconds: float = 12.0, meter: object | None = None) -> None:
        self.cache = cache
        self.timeout = timeout_seconds
        self.meter = meter
        self.parallel_key = os.environ.get("PARALLEL_API_KEY", "").strip()
        self.tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
        self.firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
        self.serper_key = os.environ.get("SERPER_API_KEY", "").strip()
        self.serpapi_key = os.environ.get("SERPAPI_API_KEY", "").strip()

    def _record_http(self, provider: str) -> None:
        if self.meter is not None and hasattr(self.meter, "record_http_request"):
            self.meter.record_http_request(provider)

    def search(self, query: str, *, limit: int = 5) -> tuple[str, list[SearchResult]]:
        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError("Сетевой поиск (CloudFreeRotator) запрещён в офлайн/локальном режиме")
        clean_q = clean_query_for_free_search(query)
        if not clean_q:
            return ("none", [])

        # 1. Parallel Search API (до 5000 бесплатных запросов/мес)
        if self.parallel_key and self.cache.try_consume_quota("parallel", 4950):
            self._record_http("parallel_free")
            hits = self._search_parallel(clean_q, limit)
            if hits:
                return ("parallel_free", hits)

        # 2. Tavily API (1000 бесплатных запросов/мес на тарифе Researcher без карты)
        if self.tavily_key and self.cache.try_consume_quota("tavily", 980):
            self._record_http("tavily_free")
            hits = self._search_tavily(clean_q, limit)
            if hits:
                return ("tavily_free", hits)

        # 3. Serper.dev (живая выдача Google с gl=ru & hl=ru)
        if self.serper_key and self.cache.try_consume_quota("serper", 240):
            self._record_http("serper_free")
            hits = self._search_serper(clean_q, limit)
            if hits:
                return ("serper_free", hits)

        # 4. Firecrawl Search API (бесплатный пул 500 кредитов)
        if self.firecrawl_key and self.cache.try_consume_quota("firecrawl", 480):
            self._record_http("firecrawl_free")
            hits = self._search_firecrawl(clean_q, limit)
            if hits:
                return ("firecrawl_free", hits)

        # 5. SerpApi (Google RU, 250 бесплатных запросов/мес)
        if self.serpapi_key and self.cache.try_consume_quota("serpapi", 240):
            self._record_http("serpapi_free")
            hits = self._search_serpapi(clean_q, limit)
            if hits:
                return ("serpapi_free", hits)

        return ("none", [])

    def _search_parallel(self, query: str, limit: int) -> list[SearchResult]:
        try:
            body = json.dumps({"objective": query, "search_queries": [query], "max_results": limit}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.parallel.ai/v1beta/search",
                data=body,
                headers={"Content-Type": "application/json", "x-api-key": self.parallel_key},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = []
            for idx, item in enumerate(data.get("results") or [], start=1):
                url = (item.get("url") or "").strip()
                if url and not is_blocked_search_result(url):
                    excerpts = " ".join(item.get("excerpts") or [])
                    results.append(
                        SearchResult(title=item.get("title") or url, url=url, snippet=excerpts[:400], rank=idx)
                    )
            return results[:limit]
        except Exception:
            return []

    def _search_tavily(self, query: str, limit: int) -> list[SearchResult]:
        try:
            body = json.dumps({
                "api_key": self.tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": limit,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [
                SearchResult(
                    title=(item.get("title") or item["url"]).strip(),
                    url=item["url"].strip(),
                    snippet=(item.get("content") or "").strip(),
                    rank=idx,
                )
                for idx, item in enumerate(data.get("results") or [], start=1)
                if item.get("url") and not is_blocked_search_result(item["url"])
            ][:limit]
        except Exception:
            return []

    def _search_serper(self, query: str, limit: int) -> list[SearchResult]:
        try:
            body = json.dumps({"q": query, "gl": "ru", "hl": "ru", "num": limit}).encode("utf-8")
            req = urllib.request.Request(
                "https://google.serper.dev/search",
                data=body,
                headers={"Content-Type": "application/json", "X-API-KEY": self.serper_key},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [
                SearchResult(
                    title=(item.get("title") or item["link"]).strip(),
                    url=item["link"].strip(),
                    snippet=(item.get("snippet") or "").strip(),
                    rank=idx,
                )
                for idx, item in enumerate(data.get("organic") or [], start=1)
                if item.get("link") and not is_blocked_search_result(item["link"])
            ][:limit]
        except Exception:
            return []

    def _search_firecrawl(self, query: str, limit: int) -> list[SearchResult]:
        try:
            body = json.dumps({"query": query, "limit": limit, "lang": "ru", "country": "ru"}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.firecrawl.dev/v1/search",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.firecrawl_key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [
                SearchResult(
                    title=(item.get("title") or item["url"]).strip(),
                    url=item["url"].strip(),
                    snippet=(item.get("description") or item.get("markdown") or "")[:400].strip(),
                    rank=idx,
                )
                for idx, item in enumerate(data.get("data") or [], start=1)
                if item.get("url") and not is_blocked_search_result(item["url"])
            ][:limit]
        except Exception:
            return []

    def _search_serpapi(self, query: str, limit: int) -> list[SearchResult]:
        try:
            params = urllib.parse.urlencode({
                "engine": "google",
                "q": query,
                "gl": "ru",
                "hl": "ru",
                "num": str(limit),
                "api_key": self.serpapi_key,
            })
            req = urllib.request.Request(f"https://serpapi.com/search.json?{params}")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [
                SearchResult(
                    title=(item.get("title") or item["link"]).strip(),
                    url=item["link"].strip(),
                    snippet=(item.get("snippet") or "").strip(),
                    rank=idx,
                )
                for idx, item in enumerate(data.get("organic_results") or [], start=1)
                if item.get("link") and not is_blocked_search_result(item["link"])
            ][:limit]
        except Exception:
            return []


class FreeRuNetSearch:
    """Единый каскад бесплатного поиска по Рунету:
    SQLite Cache -> SearXNG (local) -> DDGS (ru-ru) -> CloudFreeRotator -> Yandex (только если разрешён платный фоллбэк)."""

    def __init__(
        self,
        *,
        cache: SearchCache | None = None,
        timeout_seconds: float = 15.0,
        allow_paid_fallback: bool = False,
        meter: object | None = None,
    ) -> None:
        self.cache = cache or SearchCache()
        self.timeout_seconds = timeout_seconds
        self.allow_paid_fallback = allow_paid_fallback or (os.environ.get("FACTCHECK_ALLOW_PAID_SEARCH") == "1")
        self.searxng_url = os.environ.get("SEARXNG_BASE_URL", "").strip() or "http://127.0.0.1:8088"
        self.yandex_key = os.environ.get("YANDEX_CLOUD_API_KEY", "").strip()
        self.ddgs = DDGSSearch(timeout_seconds=min(timeout_seconds, 12.0))
        self.cloud_rotator = CloudFreeRotator(self.cache, timeout_seconds=min(timeout_seconds, 12.0), meter=meter)
        self.meter = meter
        self.last_provider: str = "none"
        self.last_was_paid: bool = False
        self.last_outcome: str = "empty"

    def search_with_outcome(self, query: str, *, limit: int = 5) -> tuple[list[SearchResult], str]:
        """Возвращает (результаты, тип_исхода) (T505): ok, empty, provider_error, timeout, blocked."""
        hits = self.search(query, limit=limit)
        return hits, self.last_outcome

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        self.last_was_paid = False
        self.last_outcome = "empty"

        # 0. Проверяем персистентный SQLite-кэш до проверки сетевого режима (0 сетевых запросов)
        cached = self.cache.get_search(query)
        if cached:
            self.last_provider = "sqlite_cache"
            hits = [SearchResult(**item) for item in cached if not is_blocked_search_result(item.get("url", ""))]
            if hits:
                self.last_outcome = "ok"
                return hits[:limit]

        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            self.last_outcome = "blocked"
            raise NetworkNotAllowedError("Сетевой поиск (FreeRuNetSearch) запрещён в офлайн/локальном режиме")

        provider_attempts = 0
        provider_errors = 0
        provider_timeouts = 0

        # 1. Локальный SearXNG (0 ₽, без лимитов)
        if self.searxng_url:
            provider_attempts += 1
            if self.meter and hasattr(self.meter, "record_http_request"):
                self.meter.record_http_request("searxng")
            try:
                clean_q = clean_query_for_free_search(query)
                raw_hits = SearXNGSearch(self.searxng_url, timeout_seconds=self.timeout_seconds).search(clean_q, limit=limit)
                hits = [h for h in raw_hits if not is_blocked_search_result(h.url)]
                if hits:
                    self.last_provider = "searxng"
                    self.last_outcome = "ok"
                    self.cache.put_search(query, "searxng", [asdict(h) for h in hits])
                    return hits
            except TimeoutError:
                provider_timeouts += 1
            except Exception:
                provider_errors += 1

        # 2. Бесключевой пакет DDGS / DuckDuckGo RU (0 ₽, без лимитов)
        provider_attempts += 1
        if self.meter and hasattr(self.meter, "record_http_request"):
            self.meter.record_http_request("ddgs_ru")
        try:
            raw_hits = self.ddgs.search(query, limit=limit)
            hits = [h for h in raw_hits if not is_blocked_search_result(h.url)]
            if hits:
                self.last_provider = "ddgs_ru"
                self.last_outcome = "ok"
                self.cache.put_search(query, "ddgs_ru", [asdict(h) for h in hits])
                return hits
        except TimeoutError:
            provider_timeouts += 1
        except Exception:
            provider_errors += 1

        # 3. Пул бесплатных облачных Search API (Parallel -> Tavily -> Serper -> Firecrawl -> SerpApi)
        provider_attempts += 1
        try:
            provider_name, raw_hits = self.cloud_rotator.search(query, limit=limit)
            if provider_name and provider_name != "none":
                hits = [h for h in raw_hits if not is_blocked_search_result(h.url)]
                if hits:
                    self.last_provider = provider_name
                    self.last_outcome = "ok"
                    self.cache.put_search(query, provider_name, [asdict(h) for h in hits])
                    return hits
        except TimeoutError:
            provider_timeouts += 1
        except Exception:
            provider_errors += 1

        # 4. Аварийный платный фоллбэк на Yandex Search API (только если явно разрешён)
        if self.allow_paid_fallback and self.yandex_key:
            provider_attempts += 1
            self.last_provider = "yandex_paid"
            self.last_was_paid = True
            if self.meter and hasattr(self.meter, "record_http_request"):
                self.meter.record_http_request("yandex_paid")
            try:
                raw_hits = YandexSearch(self.yandex_key, timeout_seconds=self.timeout_seconds).search(query, limit=limit)
                hits = [h for h in raw_hits if not is_blocked_search_result(h.url)]
                if hits:
                    self.last_outcome = "ok"
                    self.cache.put_search(query, "yandex_paid", [asdict(h) for h in hits])
                    return hits
            except TimeoutError:
                provider_timeouts += 1
            except Exception:
                provider_errors += 1

            if provider_attempts > 0 and (provider_errors + provider_timeouts) == provider_attempts:
                self.last_outcome = "timeout" if provider_timeouts > 0 and provider_errors == 0 else "provider_error"
            else:
                self.last_outcome = "empty"
            return []

        if provider_attempts > 0 and (provider_errors + provider_timeouts) == provider_attempts:
            self.last_outcome = "timeout" if provider_timeouts > 0 and provider_errors == 0 else "provider_error"
        else:
            self.last_outcome = "empty"
        self.last_provider = "none"
        return []
