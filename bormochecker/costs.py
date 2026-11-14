"""Учёт расходов и сетевых обращений за одну проверку.

Каждый вызов модели идёт через meter.add_llm(usage), каждый поиск — через meter.add_search().
Повторные попытки тоже учитываются: полная цена включает все вызовы и каскады поиска.
"""

import os
import threading
import time
from datetime import datetime, timezone

from bormochecker.contract import Costs


def _price(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _add(total: int | None, value: int | None) -> int | None:
    # неизвестное в любом вызове делает неизвестной всю сумму
    return None if total is None or value is None else total + value


def _mul(count: int | float | None, price: float | None) -> float | None:
    if count == 0:
        return 0.0  # ноль вызовов стоит ноль даже при неизвестном тарифе
    return None if count is None or price is None else count * price


def _per_m(count: int | None, price: float | None) -> float | None:
    m = _mul(count, price)
    return None if m is None else m / 1e6


def _sum(*parts: float | None) -> float | None:
    return None if any(p is None for p in parts) else sum(parts)


PAID_SEARCH_PROVIDERS = {"yandex", "yandex_paid", "yandex_search"}


class Meter:
    def __init__(self, provider: str | None = None, model: str | None = None, settings: dict | None = None):
        self.costs = Costs(
            provider=provider, model=model, settings=settings or {}, started_at=datetime.now(timezone.utc)
        )
        self._t0 = time.monotonic()
        self.used_search_providers: set[str] = set()
        self._lock = threading.Lock()

    def record_http_request(self, provider: str, n: int = 1) -> None:
        """Считает физические HTTP-запросы по провайдерам (включая повторы и пустые ответы) (T504)."""
        with self._lock:
            by_prov = self.costs.http_requests_by_provider
            by_prov[provider] = by_prov.get(provider, 0) + n
            if provider:
                self.used_search_providers.add(provider)

    def add_llm(self, usage: dict | None, retry: bool = False, seconds: float = 0.0) -> None:
        """usage в формате OpenAI chat.completions; None — провайдер его не вернул."""
        with self._lock:
            c = self.costs
            c.model_requests += 1
            c.retries += retry
            c.model_seconds = round(c.model_seconds + seconds, 3)
            usage = usage or {}
            details_in = usage.get("prompt_tokens_details") or {}
            details_out = usage.get("completion_tokens_details") or {}
            c.input_tokens = _add(c.input_tokens, usage.get("prompt_tokens"))
            c.cached_input_tokens = _add(c.cached_input_tokens, details_in.get("cached_tokens"))
            c.output_tokens = _add(c.output_tokens, usage.get("completion_tokens"))
            c.reasoning_tokens = _add(c.reasoning_tokens, details_out.get("reasoning_tokens"))

    def add_search(self, n: int = 1, *, paid: bool | None = None, provider: str = "free") -> None:
        """Считает логические поиски. Признак «платный» ставится по провайдеру, а не по непустому результату (T504)."""
        is_paid = bool(paid) or (provider in PAID_SEARCH_PROVIDERS)
        with self._lock:
            self.costs.search_requests += n
            if is_paid:
                self.costs.paid_search_requests += n
            else:
                self.costs.free_search_requests += n
            if provider:
                self.used_search_providers.add(provider)

    def add_cache_hit(self, n: int = 1) -> None:
        with self._lock:
            self.costs.cache_hits += n

    def add_local_fts_hit(self, n: int = 1) -> None:
        with self._lock:
            self.costs.local_fts_hits += n

    def add_fetch(self, n: int = 1) -> None:
        with self._lock:
            self.costs.fetch_requests += n

    def finish(self) -> Costs:
        with self._lock:
            c = self.costs
            c.duration_s = round(time.monotonic() - self._t0, 2)
            p_in, p_out = _price("PRICE_INPUT_PER_M"), _price("PRICE_OUTPUT_PER_M")
            # ponytail: тариф кэша не задан → считаем кэш по полной цене входа (оценка сверху, не снизу)
            p_cached = _price("PRICE_CACHED_INPUT_PER_M")
            p_cached = p_in if p_cached is None else p_cached
            cached = c.cached_input_tokens or 0  # prompt_tokens уже включает кэш (OpenAI)
            uncached = None if c.input_tokens is None else c.input_tokens - cached
            # reasoning входит в completion_tokens — отдельно не тарифицируем
            # своя GPU: токены по 0, платим за час работы модели (PRICE_GPU_HOUR); облако — наоборот.
            # Каждый тариф задаётся явно, 0 тоже: незаданный = неизвестный.
            gpu = None if (g := _mul(c.model_seconds, _price("PRICE_GPU_HOUR"))) is None else g / 3600
            c.model_cost_usd = _sum(
                _per_m(uncached, p_in), _per_m(cached, p_cached), _per_m(c.output_tokens, p_out), gpu
            )
            search_price = _price("PRICE_SEARCH_PER_REQUEST")
            if search_price is None and os.environ.get("FACTCHECK_SEARCH_COST_USD"):
                search_price = _price("FACTCHECK_SEARCH_COST_USD")
            fetch_price = _price("PRICE_FETCH_PER_REQUEST")
            c.tools_cost_usd = _sum(
                _mul(c.paid_search_requests, search_price or 0.0055 if c.paid_search_requests else 0.0),
                _mul(c.fetch_requests, fetch_price if fetch_price is not None else 0.0),
            )
            c.total_cost_usd = _sum(c.model_cost_usd, c.tools_cost_usd)

            # Отражаем реально использованных провайдеров поиска в settings["search"]
            if self.used_search_providers:
                prov_str = ", ".join(sorted(self.used_search_providers))
                c.settings["search"] = (
                    f"FreeRuNetSearch ({prov_str})"
                    if c.paid_search_requests == 0
                    else f"Yandex Search ({prov_str})"
                )
            elif c.settings.get("mode") == "local_frp":
                c.settings["search"] = "local_frp"

            return c
