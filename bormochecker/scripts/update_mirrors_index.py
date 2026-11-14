#!/usr/bin/env python3
"""Скрипт безопасного обновления FTS-индекса локальных зеркал (T907).

Алгоритм:
1. Индексация в изолированный временный файл (*.tmp.sqlite).
2. Верификация:
   - Число документов не меньше 90% от существующего индекса.
   - Пробный поисковый запрос по FTS-таблице выполняется без ошибок.
3. Атомарная замена:
   - Текущий индекс сохраняется как *.prev.sqlite.
   - Временный файл перемещается на место целевого индекса.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sqlite3
import sys

if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

from bormochecker.paths import mirrors_dir, mirrors_fts_path
from bormochecker.scripts.build_mirrors_fts import build_mirrors_index


def get_doc_count(db_path: pathlib.Path) -> int:
    """Возвращает число проиндексированных документов в базе."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for tbl in ("mirror_documents", "docs"):
            try:
                cur.execute(f"SELECT count(*) FROM {tbl}")
                row = cur.fetchone()
                if row:
                    conn.close()
                    return int(row[0])
            except Exception:
                continue
        conn.close()
        return 0
    except Exception:
        return 0


def verify_fts_query(db_path: pathlib.Path, query: str = "образование OR приказ OR школа") -> bool:
    """Выполняет пробный FTS-запрос для проверки целостности индекса."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for tbl in ("mirrors_fts", "docs_fts"):
            try:
                cur.execute(f"SELECT count(*) FROM {tbl} WHERE {tbl} MATCH ?", (query,))
                row = cur.fetchone()
                if row is not None:
                    conn.close()
                    return True
            except Exception:
                continue
        conn.close()
        return False
    except Exception as err:
        print(f"Ошибка тестового FTS-запроса: {err}", file=sys.stderr)
        return False


def safe_update_index(
    mirror_dir: pathlib.Path,
    db_path: pathlib.Path,
    *,
    min_ratio: float = 0.90,
    force: bool = False,
    test_query: str = "образование OR приказ OR школа",
) -> bool:
    """Безопасно пересобирает индекс и атомарно заменяет старый при прохождении проверок."""
    tmp_path = db_path.with_name(f"{db_path.name}.tmp.sqlite")
    prev_path = db_path.with_name(f"{db_path.name}.prev.sqlite")

    if tmp_path.exists():
        tmp_path.unlink()

    print(f"1. Сборка индекса во временный файл: {tmp_path}")
    count_indexed = build_mirrors_index(mirror_dir, tmp_path)
    if count_indexed <= 0:
        print("Ошибка: индексация завершилась с 0 документов.", file=sys.stderr)
        if tmp_path.exists():
            tmp_path.unlink()
        return False

    old_count = get_doc_count(db_path)
    new_count = get_doc_count(tmp_path)
    print(f"Документов в старом индексе: {old_count}, в новом: {new_count}")

    # Проверка порога документов (не меньше 90%)
    if old_count > 0 and not force:
        threshold = int(old_count * min_ratio)
        if new_count < threshold:
            print(
                f"Отказ обновления: новый индекс содержит {new_count} документов, "
                f"что меньше допустимого порога {threshold} ({int(min_ratio * 100)}% от {old_count}).",
                file=sys.stderr,
            )
            if tmp_path.exists():
                tmp_path.unlink()
            return False

    # Проверка пробного FTS-запроса
    print(f"2. Проверка тестового FTS-запроса: {test_query!r}")
    if not verify_fts_query(tmp_path, test_query):
        print("Отказ обновления: тестовый запрос к новому индексу завершился ошибкой.", file=sys.stderr)
        if tmp_path.exists():
            tmp_path.unlink()
        return False

    # Атомарная ротация файлов
    print("3. Атомарное применение нового индекса...")
    if db_path.exists():
        try:
            shutil.copy2(db_path, prev_path)
            print(f"Предыдущая версия сохранена в {prev_path}")
        except Exception as err:
            print(f"Предупреждение: не удалось создать резервную копию {prev_path}: {err}", file=sys.stderr)

    os.replace(tmp_path, db_path)
    print(f"Индекс успешно обновлён: {db_path} ({new_count} документов)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Безопасное автоматическое обновление FTS-индекса зеркал (T907)")
    parser.add_argument("--mirror-dir", type=pathlib.Path, default=mirrors_dir())
    parser.add_argument("--db-path", type=pathlib.Path, default=mirrors_fts_path())
    parser.add_argument("--min-ratio", type=float, default=0.90, help="Минимальная доля документов от старого индекса (по умолч. 0.90)")
    parser.add_argument("--force", action="store_true", help="Игнорировать падение числа документов")
    parser.add_argument("--verify-only", action="store_true", help="Только проверить целостность существующего индекса без пересборки")
    parser.add_argument("--test-query", type=str, default="образование OR приказ OR школа")

    args = parser.parse_args()
    if args.verify_only:
        doc_count = get_doc_count(args.db_path)
        query_ok = verify_fts_query(args.db_path, args.test_query)
        ok = doc_count > 0 and query_ok
        print(f"Проверка индекса {args.db_path}: документов={doc_count}, FTS-запрос={'OK' if query_ok else 'FAIL'}")
        sys.exit(0 if ok else 1)

    ok = safe_update_index(
        args.mirror_dir,
        args.db_path,
        min_ratio=args.min_ratio,
        force=args.force,
        test_query=args.test_query,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
