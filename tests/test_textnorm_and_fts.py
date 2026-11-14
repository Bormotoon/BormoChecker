from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from bormochecker.local_index import IndexOutdatedError, LocalKnowledgeIndex
from bormochecker.textnorm import NORMALIZER_VERSION, lemmatize_text, normalize_tokens


def test_textnorm_lemmatization():
    tokens = normalize_tokens("Правилами пожарной безопасности в школах")
    assert "школ" in tokens or "школа" in tokens
    assert "правил" in tokens or "правило" in tokens

    lem_text = lemmatize_text("Педагогические работники и учителя")
    assert "педагогическ" in lem_text or "педагог" in lem_text
    assert "учител" in lem_text


def test_local_index_fts_different_word_forms(tmp_path: Path):
    db_path = tmp_path / "test_mirrors.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE VIRTUAL TABLE mirrors_fts USING fts5(
            corpus,
            doc_id UNINDEXED,
            title,
            url UNINDEXED,
            content,
            content_lemmatized,
            tokenize = 'unicode61'
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO index_metadata (key, value) VALUES ('normalizer_version', ?)",
        (NORMALIZER_VERSION,),
    )

    docs = [
        (
            "edsoo",
            "doc1",
            "Безопасность в школах",
            "https://edsoo.ru/doc1",
            "Правила пожарной безопасности в школах и образовательных учреждениях.",
            lemmatize_text("Правила пожарной безопасности в школах и образовательных учреждениях."),
        ),
        (
            "edsoo",
            "doc2",
            "Федеральные программы",
            "https://edsoo.ru/doc2",
            "Федеральные рабочие программы по учебным предметам начального образования.",
            lemmatize_text("Федеральные рабочие программы по учебным предметам начального образования."),
        ),
        (
            "edsoo",
            "doc3",
            "Аттестация учителей",
            "https://edsoo.ru/doc3",
            "Порядок проведения аттестации педагогических работников школ.",
            lemmatize_text("Порядок проведения аттестации педагогических работников школ."),
        ),
    ]

    for row in docs:
        conn.execute(
            """
            INSERT INTO mirrors_fts (corpus, doc_id, title, url, content, content_lemmatized)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            row,
        )
    conn.commit()
    conn.close()

    index = LocalKnowledgeIndex(db_paths=[db_path])
    hits = index.search_docs("правилами пожарной безопасности в школе", limit=3)
    assert len(hits) > 0
    assert hits[0].url == "https://edsoo.ru/doc1"
    assert "пожарной безопасности" in hits[0].text


def test_local_index_outdated_version_raises(tmp_path: Path):
    db_path = tmp_path / "outdated.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE VIRTUAL TABLE test_fts USING fts5(
            corpus,
            title,
            content,
            tokenize = 'unicode61'
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO index_metadata (key, value) VALUES ('normalizer_version', '0.1')"
    )
    conn.commit()
    conn.close()

    index = LocalKnowledgeIndex(db_paths=[db_path])
    with pytest.raises(IndexOutdatedError, match="индекс устарел, пересоберите"):
        index.search_docs("школьные программы")
