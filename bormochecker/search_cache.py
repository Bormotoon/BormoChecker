"""Персистентный SQLite WAL кэш поисковых запросов, скачанных документов и месячных квот бесплатных API.

Сохранение результатов поиска и загруженных страниц на диск (TTL 24–168 ч)
исключает повторные сетевые походы при проверке серий заметок по одной теме и бережёт лимиты провайдеров."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import sqlite3
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .paths import data_dir, search_cache_path

_STRIP_QUERY_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "yclid", "ysclid", "fbclid", "gclid", "_openstat", "from", "ref",
})

DEFAULT_CACHE_PATH = search_cache_path()


def canonical_url(url: str) -> str:
    """Нормализует URL для дедупликации между разными поисковиками."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    clean_query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in _STRIP_QUERY_PARAMS)
    )
    return urlunsplit((scheme, netloc, path, clean_query, ""))


def normalize_query_key(query: str) -> str:
    """Канонический ключ поискового запроса (регистронезависимый, ё -> е)."""
    tokens = (query or "").casefold().replace("ё", "е").split()
    norm = " ".join(tokens)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


class SearchCache:
    def __init__(
        self,
        db_path: pathlib.Path | str | None = None,
        *,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        search_ttl_seconds: int | None = None,
        page_ttl_seconds: int | None = None,
    ) -> None:
        target = pathlib.Path(db_path) if db_path else search_cache_path()
        if os.environ.get("FACTCHECK_OFFLINE") == "1":
            prod_path = (data_dir() / "factcheck_search_cache.sqlite").resolve()
            if target.resolve() == prod_path:
                raise RuntimeError("Запрещено чтение боевого кеша в режиме offline (T408 / [E07])")
        self.db_path = target
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = (
            max_entries
            if max_entries is not None
            else int(os.environ.get("FACTCHECK_CACHE_MAX_ENTRIES") or 10000)
        )
        self.max_bytes = (
            max_bytes
            if max_bytes is not None
            else int(os.environ.get("FACTCHECK_CACHE_MAX_BYTES") or 100 * 1024 * 1024)
        )
        self.search_ttl_seconds = (
            search_ttl_seconds
            if search_ttl_seconds is not None
            else int(os.environ.get("FACTCHECK_CACHE_SEARCH_TTL_SECONDS") or 72 * 3600)
        )
        self.page_ttl_seconds = (
            page_ttl_seconds
            if page_ttl_seconds is not None
            else int(os.environ.get("FACTCHECK_CACHE_PAGE_TTL_SECONDS") or 7 * 24 * 3600)
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=10.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_queries_cache (
                    query_hash   TEXT PRIMARY KEY,
                    query_text   TEXT NOT NULL,
                    provider     TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at   INTEGER NOT NULL,
                    expires_at   INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sqc_expires ON search_queries_cache(expires_at);

                CREATE TABLE IF NOT EXISTS fetched_pages_cache (
                    canonical_url TEXT PRIMARY KEY,
                    original_url  TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    text          TEXT NOT NULL,
                    fetched_at    INTEGER NOT NULL,
                    expires_at    INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fpc_expires ON fetched_pages_cache(expires_at);

                CREATE TABLE IF NOT EXISTS api_monthly_quotas (
                    provider      TEXT NOT NULL,
                    year_month    TEXT NOT NULL,
                    used_count    INTEGER NOT NULL DEFAULT 0,
                    monthly_limit INTEGER NOT NULL,
                    PRIMARY KEY (provider, year_month)
                );
                """
            )

    def prune(self, max_entries: int | None = None, max_bytes: int | None = None) -> dict[str, int]:
        """Удаляет просроченные записи и старые записи при превышении лимитов (T506)."""
        limit_entries = max_entries if max_entries is not None else self.max_entries
        limit_bytes = max_bytes if max_bytes is not None else self.max_bytes
        now = int(time.time())
        deleted_expired = 0
        deleted_overflow = 0

        with self._connect() as conn:
            cur1 = conn.execute("DELETE FROM search_queries_cache WHERE expires_at <= ?", (now,))
            deleted_expired += cur1.rowcount
            cur2 = conn.execute("DELETE FROM fetched_pages_cache WHERE expires_at <= ?", (now,))
            deleted_expired += cur2.rowcount

            count_sqc = conn.execute("SELECT COUNT(*) FROM search_queries_cache").fetchone()[0]
            if count_sqc > limit_entries:
                to_delete = count_sqc - limit_entries
                cur = conn.execute(
                    """
                    DELETE FROM search_queries_cache
                    WHERE query_hash IN (
                        SELECT query_hash FROM search_queries_cache
                        ORDER BY created_at ASC LIMIT ?
                    )
                    """,
                    (to_delete,),
                )
                deleted_overflow += cur.rowcount

            count_fpc = conn.execute("SELECT COUNT(*) FROM fetched_pages_cache").fetchone()[0]
            if count_fpc > limit_entries:
                to_delete = count_fpc - limit_entries
                cur = conn.execute(
                    """
                    DELETE FROM fetched_pages_cache
                    WHERE canonical_url IN (
                        SELECT canonical_url FROM fetched_pages_cache
                        ORDER BY fetched_at ASC LIMIT ?
                    )
                    """,
                    (to_delete,),
                )
                deleted_overflow += cur.rowcount

            try:
                size_bytes = self.db_path.stat().st_size
            except OSError:
                size_bytes = 0

            if size_bytes > limit_bytes:
                conn.execute(
                    """
                    DELETE FROM search_queries_cache
                    WHERE query_hash IN (
                        SELECT query_hash FROM search_queries_cache
                        ORDER BY created_at ASC LIMIT max(1, (SELECT COUNT(*) / 5 FROM search_queries_cache))
                    )
                    """
                )
                conn.execute(
                    """
                    DELETE FROM fetched_pages_cache
                    WHERE canonical_url IN (
                        SELECT canonical_url FROM fetched_pages_cache
                        ORDER BY fetched_at ASC LIMIT max(1, (SELECT COUNT(*) / 5 FROM fetched_pages_cache))
                    )
                    """
                )
                conn.execute("VACUUM;")

        return {"deleted_expired": deleted_expired, "deleted_overflow": deleted_overflow}

    def get_search(self, query: str) -> list[dict] | None:
        qhash = normalize_query_key(query)
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT results_json FROM search_queries_cache WHERE query_hash = ? AND expires_at > ?",
                (qhash, now),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def put_search(
        self,
        query: str,
        provider: str,
        results: list[dict],
        ttl_seconds: int | None = None,
    ) -> None:
        if not results:
            return
        if any(isinstance(r, dict) and bool(r.get("error")) for r in results):
            return
        ttl = ttl_seconds if ttl_seconds is not None else self.search_ttl_seconds
        qhash = normalize_query_key(query)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO search_queries_cache (query_hash, query_text, provider, results_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_hash) DO UPDATE SET
                    provider = excluded.provider,
                    results_json = excluded.results_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (qhash, query.strip(), provider, json.dumps(results, ensure_ascii=False), now, now + ttl),
            )
        self.prune()

    def get_page(self, url: str) -> tuple[str, str] | None:
        curl = canonical_url(url)
        if not curl:
            return None
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT title, text FROM fetched_pages_cache WHERE canonical_url = ? AND expires_at > ?",
                (curl, now),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def put_page(
        self,
        url: str,
        title: str,
        text: str,
        ttl_seconds: int | None = None,
    ) -> None:
        curl = canonical_url(url)
        if not curl or not text or len(text.strip()) < 80:
            return
        if text.strip().startswith("Ошибка") or "404 Not Found" in text:
            return
        ttl = ttl_seconds if ttl_seconds is not None else self.page_ttl_seconds
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fetched_pages_cache (canonical_url, original_url, title, text, fetched_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    title = excluded.title,
                    text = excluded.text,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (curl, url, title or url, text, now, now + ttl),
            )
        self.prune()

    def try_consume_quota(self, provider: str, monthly_limit: int) -> bool:
        """Атомарно проверяет и инкрементирует счётчик бесплатной квоты облачного поисковика за текущий месяц."""
        ym = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_monthly_quotas (provider, year_month, used_count, monthly_limit)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(provider, year_month) DO UPDATE SET monthly_limit = excluded.monthly_limit
                """,
                (provider, ym, monthly_limit),
            )
            cur = conn.execute(
                """
                UPDATE api_monthly_quotas
                SET used_count = used_count + 1
                WHERE provider = ? AND year_month = ? AND used_count < monthly_limit
                """,
                (provider, ym),
            )
            return cur.rowcount == 1


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Управление кешем поиска (T506)")
    subparsers = parser.add_subparsers(dest="command")
    prune_parser = subparsers.add_parser("prune", help="Очистить устаревшие и избыточные записи кеша")
    prune_parser.add_argument("--max-entries", type=int, default=None, help="Предельное число записей")
    prune_parser.add_argument("--max-bytes", type=int, default=None, help="Предельный размер в байтах")
    prune_parser.add_argument("--db-path", type=str, default=None, help="Путь к sqlite базе кеша")

    args = parser.parse_args()
    if args.command == "prune":
        cache = SearchCache(db_path=args.db_path)
        stats = cache.prune(max_entries=args.max_entries, max_bytes=args.max_bytes)
        print(
            f"Кеш очищен: удалено просроченных: {stats['deleted_expired']}, "
            f"удалено сверх лимита: {stats['deleted_overflow']}"
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
