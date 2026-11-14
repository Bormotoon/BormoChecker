"""Тест ограничения кеша поиска и prune (T506 / [P05])."""

import subprocess
import sys
import time

from bormochecker.search_cache import SearchCache


def test_prune_removes_oldest_records_when_max_entries_exceeded(tmp_path) -> None:
    """При превышении лимита числа записей старейшие записи удаляются."""
    db_file = tmp_path / "test_cache.sqlite"
    cache = SearchCache(db_path=db_file, max_entries=3)

    # Добавляем 4 записи
    for i in range(1, 5):
        cache.put_search(f"запрос {i}", "ddgs", [{"title": f"title {i}", "url": f"http://example.com/{i}"}])
        time.sleep(0.01)

    # Запрашиваем записи: старейшая (запрос 1) должна быть удалена по лимиту max_entries=3
    assert cache.get_search("запрос 1") is None
    assert cache.get_search("запрос 2") is not None
    assert cache.get_search("запрос 3") is not None
    assert cache.get_search("запрос 4") is not None


def test_prune_removes_expired_records(tmp_path) -> None:
    """Просроченные записи удаляются."""
    db_file = tmp_path / "test_cache_expired.sqlite"
    cache = SearchCache(db_path=db_file)

    now = int(time.time())
    with cache._connect() as conn:
        conn.execute(
            """
            INSERT INTO search_queries_cache (query_hash, query_text, provider, results_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("hash1", "старый запрос", "ddgs", '[{"title": "t"}]', now - 100, now - 10),
        )

    stats = cache.prune()
    assert stats["deleted_expired"] >= 1
    assert cache.get_search("старый запрос") is None


def test_do_not_cache_error_responses(tmp_path) -> None:
    """Ошибочные ответы не кешируются."""
    db_file = tmp_path / "test_cache_no_error.sqlite"
    cache = SearchCache(db_path=db_file)

    # Пустые результаты
    cache.put_search("пустой запрос", "ddgs", [])
    assert cache.get_search("пустой запрос") is None

    # Результаты с ошибкой
    cache.put_search("ошибочный запрос", "ddgs", [{"error": "403 Forbidden"}])
    assert cache.get_search("ошибочный запрос") is None


def test_cli_prune_command(tmp_path) -> None:
    """CLI команда python -m bormochecker.search_cache prune выполняется успешно."""
    db_file = tmp_path / "test_cli.sqlite"
    cache = SearchCache(db_path=db_file)
    cache.put_search("старый", "ddgs", [{"title": "t", "url": "http://example.com"}], ttl_seconds=-5)

    cmd = [
        sys.executable,
        "-m",
        "bormochecker.search_cache",
        "prune",
        "--db-path",
        str(db_file),
        "--max-entries",
        "5",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "Кеш очищен" in res.stdout
    assert cache.get_search("старый") is None
