from __future__ import annotations

import pytest

from bormochecker.retrieval import UnsafeUrlError, assert_safe_http_url, is_aggregator_or_search_url
from bormochecker.sources import publisher_kind


def test_aggregator_and_search_url_filtered():
    # Адрес страницы тега на домене отфильтрован
    tag_url = "https://example.com/tag/education"
    assert is_aggregator_or_search_url(tag_url)

    with pytest.raises(UnsafeUrlError, match="страница-агрегатор/поиск/лента"):
        assert_safe_http_url(tag_url)

    # Другие шаблоны агрегаторов
    assert is_aggregator_or_search_url("https://example.com/tags/shkola")
    assert is_aggregator_or_search_url("https://example.com/category/events/")
    assert is_aggregator_or_search_url("https://example.com/search?q=shkola")
    assert is_aggregator_or_search_url("https://pikabu.ru/tag/obrazovanie")

    # Адрес статьи на том же домене проходит фильтр агрегаторов
    article_url = "https://example.com/articles/education-reform-2026"
    assert not is_aggregator_or_search_url(article_url)


def test_ugc_platforms_classification():
    # Пользовательские площадки классифицируются как ugc
    assert publisher_kind("pikabu.ru") == "ugc"
    assert publisher_kind("dzen.ru") == "ugc"
    assert publisher_kind("livejournal.com") == "ugc"
    assert publisher_kind("habr.com") == "ugc"
