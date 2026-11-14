"""Лимиты одной проверки — из окружения, как остальные настройки.

Одна проверка = одна строка расходов, поэтому лимиты важнее скорости: без них
одна новость съедает весь бюджет. `Settings.public()` идёт в `Costs.settings` —
форма печатает эти значения в таблице расходов.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    # сколько утверждений реально проверяем; остальные — в limitations, а не «проверено»
    max_claims: int = 60
    max_queries_per_claim: int = 2
    max_documents_per_claim: int = 3
    max_documents: int = 12
    search_results_per_query: int = 5
    time_budget_seconds: float = 600.0
    chunk_chars: int = 6000
    search_enabled: bool = True
    model_enabled: bool = True
    eval_concurrency: int = 3
    eval_temperature: float = 0.0
    eval_seed: int = 42
    cache_search_ttl_seconds: int = 72 * 3600
    cache_page_ttl_seconds: int = 7 * 24 * 3600
    cache_max_entries: int = 10000
    cache_max_bytes: int = 100 * 1024 * 1024
    docx_max_compressed_bytes: int = 10 * 1024 * 1024
    docx_max_uncompressed_bytes: int = 10 * 1024 * 1024
    docx_max_entries: int = 100
    docx_max_compression_ratio: float = 100.0
    docx_max_xml_depth: int = 256
    pdf_max_bytes: int = 20 * 1024 * 1024
    pdf_max_pages: int = 200
    html_max_bytes: int = 5 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            max_claims=int(_env("FACTCHECK_MAX_CLAIMS", str(d.max_claims))),
            max_queries_per_claim=int(_env("FACTCHECK_MAX_QUERIES_PER_CLAIM", str(d.max_queries_per_claim))),
            max_documents_per_claim=int(_env("FACTCHECK_MAX_DOCS_PER_CLAIM", str(d.max_documents_per_claim))),
            max_documents=int(_env("FACTCHECK_MAX_DOCUMENTS", str(d.max_documents))),
            search_results_per_query=int(_env("FACTCHECK_SEARCH_RESULTS", str(d.search_results_per_query))),
            time_budget_seconds=float(_env("FACTCHECK_TIME_BUDGET_SECONDS", str(d.time_budget_seconds))),
            chunk_chars=int(_env("FACTCHECK_CHUNK_CHARS", str(d.chunk_chars))),
            search_enabled=_env("FACTCHECK_SEARCH", "1") != "0",
            model_enabled=_env("FACTCHECK_MODEL", "1") != "0",
            eval_concurrency=max(1, int(_env("FACTCHECK_EVAL_CONCURRENCY", str(d.eval_concurrency)))),
            eval_temperature=float(_env("FACTCHECK_EVAL_TEMPERATURE", str(d.eval_temperature))),
            eval_seed=int(_env("FACTCHECK_EVAL_SEED", str(d.eval_seed))),
            cache_search_ttl_seconds=int(_env("FACTCHECK_CACHE_SEARCH_TTL_SECONDS", str(d.cache_search_ttl_seconds))),
            cache_page_ttl_seconds=int(_env("FACTCHECK_CACHE_PAGE_TTL_SECONDS", str(d.cache_page_ttl_seconds))),
            cache_max_entries=int(_env("FACTCHECK_CACHE_MAX_ENTRIES", str(d.cache_max_entries))),
            cache_max_bytes=int(_env("FACTCHECK_CACHE_MAX_BYTES", str(d.cache_max_bytes))),
            docx_max_compressed_bytes=int(_env("FACTCHECK_DOCX_MAX_COMPRESSED_BYTES", str(d.docx_max_compressed_bytes))),
            docx_max_uncompressed_bytes=int(_env("FACTCHECK_DOCX_MAX_UNCOMPRESSED_BYTES", str(d.docx_max_uncompressed_bytes))),
            docx_max_entries=int(_env("FACTCHECK_DOCX_MAX_ENTRIES", str(d.docx_max_entries))),
            docx_max_compression_ratio=float(_env("FACTCHECK_DOCX_MAX_COMPRESSION_RATIO", str(d.docx_max_compression_ratio))),
            docx_max_xml_depth=int(_env("FACTCHECK_DOCX_MAX_XML_DEPTH", str(d.docx_max_xml_depth))),
            pdf_max_bytes=int(_env("FACTCHECK_PDF_MAX_BYTES", str(d.pdf_max_bytes))),
            pdf_max_pages=int(_env("FACTCHECK_PDF_MAX_PAGES", str(d.pdf_max_pages))),
            html_max_bytes=int(_env("FACTCHECK_HTML_MAX_BYTES", str(d.html_max_bytes))),
        )

    def public(self) -> dict:
        return asdict(self)

    def remaining(self, spent: float) -> float:
        """Остаток бюджета времени. Один вызов модели не должен съесть весь прогон."""
        return max(0.0, self.time_budget_seconds - spent)
