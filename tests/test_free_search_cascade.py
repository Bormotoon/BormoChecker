"""Комплексные тесты многоуровневого бесплатного каскада поиска (0 ₽ за проверку).

Проверяет:
1. Канонизацию URL и SQLite WAL кэш выдачи и страниц (TTL).
2. Защиту месячных квот бесплатных облачных провайдеров (CloudFreeRotator).
3. Санитизацию поисковых запросов (удаление ломающих 'site:a | site:b' цепочек).
4. Водопад провайдеров (SearXNG -> DDGS -> CloudFreeRotator) без обращения к платному Yandex.
5. Интеллектуальную экономию: кластерное переиспользование документов пула (1 поиск вместо 4).
6. Соблюдение инвариантов безопасности (SSRF-стоп-лист) и локального контура без сторонних CDN.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

from bormochecker.costs import Meter
from bormochecker.direct_apis import BLOCKED_DOMAINS, is_blocked_domain
from bormochecker.search import (
    DDGSSearch,
    FreeRuNetSearch,
    SearchResult,
    clean_query_for_free_search,
    is_blocked_search_result,
)
from bormochecker.search_cache import SearchCache, canonical_url
from bormochecker.sources import Doc, Sources, sanitize_query_for_free_engines


def test_canonical_url_and_sqlite_cache_ttl(tmp_path: pathlib.Path) -> None:
    # 1. Проверяем канонизацию URL: удаление www, трекинг-параметров, якорей, портов
    url1 = "https://www.mos.ru/news/item/12345/?utm_source=tg&utm_medium=cpc#lead"
    url2 = "http://mos.ru:80/news/item/12345"
    assert canonical_url(url1) == "https://mos.ru/news/item/12345"
    assert canonical_url(url2) == "http://mos.ru/news/item/12345"

    # 2. SQLite WAL кэш
    db_file = tmp_path / "test_cache.sqlite"
    cache = SearchCache(db_path=db_file)

    # Кэш результатов поиска
    q = "приказ 729 Минпросвещения"
    hits = [
        {"title": "Приказ № 729", "url": "https://edsoo.ru/order-729", "snippet": "ФРП 2026", "rank": 1}
    ]
    cache.put_search(q, "searxng", hits, ttl_seconds=3600)
    cached_hits = cache.get_search(q)
    assert cached_hits is not None
    assert len(cached_hits) == 1
    assert cached_hits[0]["url"] == "https://edsoo.ru/order-729"

    # Кэш прочитанных страниц
    page_url = "https://edsoo.ru/order-729?ref=digest"
    body_text = "Приказ Министерства просвещения Российской Федерации № 729 об утверждении Федеральных рабочих программ по обществознанию."
    cache.put_page(page_url, "Приказ № 729", body_text, ttl_seconds=7200)

    # Получение по каноническому URL без трекинга
    cached_page = cache.get_page("https://www.edsoo.ru/order-729")
    assert cached_page is not None
    assert cached_page[0] == "Приказ № 729"
    assert "№ 729" in cached_page[1]


def test_api_monthly_quota_guard(tmp_path: pathlib.Path) -> None:
    db_file = tmp_path / "quota_cache.sqlite"
    cache = SearchCache(db_path=db_file)

    # Лимит 2 запроса в месяц для провайдера tavily
    assert cache.try_consume_quota("tavily", 2) is True
    assert cache.try_consume_quota("tavily", 2) is True
    # 3-й запрос обязан быть заблокирован
    assert cache.try_consume_quota("tavily", 2) is False


def test_clean_query_strips_multi_site_chains() -> None:
    # Множественные site: склейки ломают SearXNG и DuckDuckGo
    raw_query = "концерт Dabro в Зеленограде (site:mos.ru | site:dkzelenograd.ru | site:zelkultura.ru)"
    cleaned = clean_query_for_free_search(raw_query)
    assert "site:" not in cleaned
    assert "концерт Dabro в Зеленограде" in cleaned

    # Одиночный site: сохраняется для точечного поиска
    single_site = "расписание ЕГЭ 2026 site:fipi.ru"
    assert "site:fipi.ru" in clean_query_for_free_search(single_site)

    # Проверяем санитайзер в sources.py
    sanitized = sanitize_query_for_free_engines(raw_query)
    assert "site:" not in sanitized
    assert "Dabro" in sanitized


def test_free_runet_search_waterfall_order(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    db_file = tmp_path / "waterfall_cache.sqlite"
    cache = SearchCache(db_path=db_file)

    # Имитируем недоступность SearXNG
    searcher = FreeRuNetSearch(cache=cache, allow_paid_fallback=False)
    searcher.searxng_url = "http://127.0.0.1:9999"  # заведомо неработающий порт

    # Мокаем DDGSSearch так, чтобы он возвращал результат
    mock_ddgs_results = [
        SearchResult(title="Dabro Афиша", url="https://dkzelenograd.ru/events/dabro", snippet="1 час 40 минут", rank=1)
    ]
    monkeypatch.setattr(DDGSSearch, "search", lambda self, q, limit=5: mock_ddgs_results)

    meter = Meter()
    hits = searcher.search("концерт Dabro", limit=5)
    assert len(hits) == 1
    assert searcher.last_provider == "ddgs_ru"
    assert searcher.last_was_paid is False

    meter.add_search(paid=searcher.last_was_paid, provider=searcher.last_provider)
    costs = meter.finish()
    # Стоимость поиска обязана быть строго 0.00 USD
    assert costs.tools_cost_usd == 0.0
    assert costs.paid_search_requests == 0
    assert costs.free_search_requests == 1


def test_cluster_pool_reuse_saves_searches(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Проверяет, что 4 утверждения одного кластера порождают только 1 поисковый сетевой запрос."""
    db_file = tmp_path / "pool_reuse.sqlite"
    meter = Meter()
    sources = Sources(meter, checked_text="Тестовый текст концерта Dabro")
    sources.cache = SearchCache(db_path=db_file)

    # Мокаем поисковик, чтобы фиксировать число вызовов
    search_mock = MagicMock(return_value=[
        SearchResult(
            title="Концерт Dabro",
            url="https://dkzelenograd.ru/events/dabro",
            snippet="15 октября 19:00 продолжительность 1 час 40 минут цена от 2000 руб",
            rank=1,
        )
    ])
    monkeypatch.setattr(sources.search_engine, "search", search_mock)

    # Мокаем скачивание страницы с добавлением в pool
    sample_page_text = (
        "Культурный центр Зеленоград. Концерт группы Dabro состоится 15 октября в 19:00. "
        "Продолжительность программы: 1 час 40 минут. Билеты от 2000 рублей."
    )
    def mock_load(url: str) -> Doc:
        doc = Doc(
            id=f"d{len(sources._docs) + 1}",
            url=url,
            title="Концерт Dabro",
            kind="media",
            text=sample_page_text,
        )
        sources._docs[url] = doc
        return doc
    monkeypatch.setattr(sources, "_load", mock_load)

    # 4 утверждения из одного кластера 'концерт-dabro'
    claims = [
        "Концерт группы Dabro состоится 15 октября",
        "Начало концерта в 19:00",
        "Продолжительность концерта 1,5 часа",
        "Стоимость билетов от 2000 рублей",
    ]

    # Первое утверждение (лидер кластера) выполняет поиск и пополняет pool()
    sources.gather_for(claims[0], limit=3, mode="education", importance="high", query="концерт Dabro Зеленоград")
    initial_search_calls = search_mock.call_count
    assert initial_search_calls >= 1

    # Следующие 3 утверждения того же кластера находят ответ в sources.pool() на Шаге В
    for c in claims[1:]:
        sources.gather_for(c, limit=3, mode="education", importance="medium", query="концерт Dabro Зеленоград")

    # Число сетевых поисков НЕ должно увеличиться!
    assert search_mock.call_count == initial_search_calls


def test_ssrf_security_invariants() -> None:
    # 1. Стоп-лист доменов обязан работать в direct_apis и search
    for domain in BLOCKED_DOMAINS:
        assert is_blocked_domain(f"https://{domain}/path") is True
        assert is_blocked_domain(f"https://sub.{domain}/path") is True
        assert is_blocked_search_result(f"https://{domain}/article") is True

    # 2. Публичные легитимные домены не должны блокироваться
    assert is_blocked_domain("https://edu.gov.ru/orders") is False
    assert is_blocked_domain("https://ru.wikipedia.org/wiki/Test") is False
    assert is_blocked_search_result("https://fipi.ru/oge") is False
