from __future__ import annotations

import pathlib
from unittest.mock import patch

from bormochecker.scripts.build_mirrors_fts import init_db
from bormochecker.scripts.update_mirrors_index import (
    get_doc_count,
    safe_update_index,
    verify_fts_query,
)


def _populate_test_db(db_path: pathlib.Path, count: int) -> None:
    conn = init_db(db_path)
    for i in range(count):
        conn.execute(
            "INSERT INTO mirror_documents (corpus, doc_id, title, url, raw_text) "
            "VALUES ('official', ?, ?, 'https://test.edu.ru', ?)",
            (f"doc_{i}", f"Приказ № {i}", f"Текст приказа об образовании {i}"),
        )
        conn.execute(
            "INSERT INTO mirrors_fts (corpus, doc_id, title, url, content, content_lemmatized) "
            "VALUES ('official', ?, ?, 'https://test.edu.ru', ?, ?)",
            (f"doc_{i}", f"Приказ № {i}", f"Текст приказа об образовании {i}", "приказ образование"),
        )
    conn.commit()
    conn.close()


def test_get_doc_count_and_verify_fts(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "test.sqlite"
    assert get_doc_count(db) == 0

    _populate_test_db(db, 10)
    assert get_doc_count(db) == 10
    assert verify_fts_query(db, "образование") is True
    assert verify_fts_query(db, "несуществующее_слово_12345") is True


def test_safe_update_index_rejects_count_drop_below_threshold(tmp_path: pathlib.Path) -> None:
    mirror_dir = tmp_path / "mirrors"
    mirror_dir.mkdir()
    target_db = tmp_path / "index.sqlite"

    # Создаём старую базу на 100 документов
    _populate_test_db(target_db, 100)
    assert get_doc_count(target_db) == 100

    # Мокаем сборку нового индекса, возвращающего только 50 документов (порог 90% не пройден)
    def mock_build_low(m_dir, out_db, limit=None):
        _populate_test_db(out_db, 50)
        return 50

    with patch("bormochecker.scripts.update_mirrors_index.build_mirrors_index", side_effect=mock_build_low):
        success = safe_update_index(mirror_dir, target_db, min_ratio=0.90)
        assert success is False
        # Старая база не должна быть перезаписана
        assert get_doc_count(target_db) == 100
        # Временный файл должен быть удалён
        tmp_file = target_db.with_name(f"{target_db.name}.tmp.sqlite")
        assert not tmp_file.exists()


def test_safe_update_index_atomic_rotation_success(tmp_path: pathlib.Path) -> None:
    mirror_dir = tmp_path / "mirrors"
    mirror_dir.mkdir()
    target_db = tmp_path / "index.sqlite"

    # Старая база на 10 документов
    _populate_test_db(target_db, 10)

    # Мокаем сборку нового индекса на 12 документов
    def mock_build_ok(m_dir, out_db, limit=None):
        _populate_test_db(out_db, 12)
        return 12

    with patch("bormochecker.scripts.update_mirrors_index.build_mirrors_index", side_effect=mock_build_ok):
        success = safe_update_index(mirror_dir, target_db, min_ratio=0.90)
        assert success is True
        # Целевая база теперь содержит 12 документов
        assert get_doc_count(target_db) == 12
        # Резервная копия prev.sqlite содержит 10 документов
        prev_file = target_db.with_name(f"{target_db.name}.prev.sqlite")
        assert prev_file.exists()
        assert get_doc_count(prev_file) == 10
        # Временный файл перемещён (не существует)
        tmp_file = target_db.with_name(f"{target_db.name}.tmp.sqlite")
        assert not tmp_file.exists()
