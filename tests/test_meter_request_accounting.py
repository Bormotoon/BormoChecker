"""Тест точного учёта запросов в Meter (T504 / [P03])."""

from unittest.mock import patch

from bormochecker.contract import Costs
from bormochecker.costs import Meter
from bormochecker.search import FreeRuNetSearch


def test_paid_request_with_empty_response_is_counted_as_paid() -> None:
    """Платный запрос с пустым ответом учтён как платный."""
    meter = Meter(settings={"mode": "journalism_full"})

    # Создаём FreeRuNetSearch с разрешённым платным фоллбэком
    searcher = FreeRuNetSearch(
        allow_paid_fallback=True,
        meter=meter,
    )
    searcher.searxng_url = ""  # отключаем searxng
    searcher.yandex_key = "fake-key"

    # Мокаем: DDGS возвращает пустой список, Yandex возвращает пустой список
    with patch.object(searcher.ddgs, "search", return_value=[]):
        with patch.object(searcher.cloud_rotator, "search", return_value=("none", [])):
            with patch("bormochecker.search.YandexSearch.search", return_value=[]):
                hits = searcher.search("любой запрос")

    assert hits == []
    assert searcher.last_provider == "yandex_paid"
    assert searcher.last_was_paid is True

    meter.add_search(paid=searcher.last_was_paid, provider=searcher.last_provider)
    costs = meter.finish()

    assert costs.paid_search_requests == 1, (
        f"Ожидался 1 платный запрос, получено {costs.paid_search_requests}"
    )
    assert costs.tools_cost_usd is not None and costs.tools_cost_usd > 0.0, (
        "Стоимость инструментов должна быть больше 0 при платном запросе"
    )
    assert costs.http_requests_by_provider.get("yandex_paid", 0) >= 1


def test_provider_determines_paid_status_even_if_passed_false() -> None:
    """Признак «платный» ставится по провайдеру, а не по непустому результату или флагу."""
    meter = Meter()
    # Провайдер yandex_paid должен считаться платным, даже если ошибочно передано paid=False
    meter.add_search(1, paid=False, provider="yandex_paid")
    costs = meter.finish()
    assert costs.paid_search_requests == 1
    assert costs.free_search_requests == 0


def test_meter_tracks_physical_http_requests_separately_from_logical_searches() -> None:
    """Meter раздельно считает логические поиски и физические HTTP-запросы по провайдерам."""
    meter = Meter()
    meter.record_http_request("searxng", n=1)
    meter.record_http_request("ddgs_ru", n=2)  # например, 1 повтор
    meter.record_http_request("yandex_paid", n=1)
    meter.add_search(n=1, provider="yandex_paid")

    costs: Costs = meter.finish()
    assert costs.search_requests == 1  # 1 логический поиск
    assert costs.http_requests_by_provider == {
        "searxng": 1,
        "ddgs_ru": 2,
        "yandex_paid": 1,
    }
