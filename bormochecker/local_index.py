"""Ступень 0.2 поиска: мгновенный локальный поиск (0 ₽, 5–10 мс) по SQLite FTS5 индексам
129 ФРП ЕДСОО, ФГОС, СанПиН, писем Роструда и локальных зеркал (fipi.ru, edsoo.ru, rcoi.mcko.ru, ikp-rao.ru)."""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
from dataclasses import dataclass

from bormochecker.paths import knowledge_fts_path, mirrors_fts_path
from bormochecker.textnorm import NORMALIZER_VERSION, normalize_text, normalize_token


class IndexOutdatedError(RuntimeError):
    """Исключение, возникающее при несовпадении версии нормализатора индекса."""
    pass


def validate_index_version(conn: sqlite3.Connection, db_path: pathlib.Path | str | None = None) -> None:
    """Проверяет версию нормализатора в таблице index_metadata."""
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_metadata'"
    ).fetchone()
    if not has_meta:
        raise IndexOutdatedError(
            f"индекс устарел, пересоберите: {db_path or 'database'} (отсутствует таблица index_metadata)"
        )
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'normalizer_version'"
    ).fetchone()
    if not row or str(row[0]).strip() != NORMALIZER_VERSION:
        curr = row[0] if row else "None"
        raise IndexOutdatedError(
            f"индекс устарел, пересоберите: {db_path or 'database'} (версия {curr} != {NORMALIZER_VERSION})"
        )


_STOPWORDS = frozenset({
    "это", "как", "так", "ибо", "или", "при", "для", "без", "под", "над",
    "про", "все", "вся", "всё", "они", "она", "оно", "его", "ему", "них",
    "был", "была", "были", "быть", "есть", "будет", "год", "года", "году",
})

_EDU_NORMATIVE_RE = re.compile(
    r"\b(?:фрп|фгос|фоп|санпин\w*|минпросвещ\w*|минобрнаук\w*|рособрнадзор\w*|роструд\w*|"
    r"едсоо|фипи|мцко|цмоко|икп\s*рао|егэ|огэ|впр|гиа|урок\w*|класс\w*|школ\w*|учител\w*|"
    r"педагог\w*|учебн\w*|программ\w*|аттестац\w*|приказ\w*|постановлен\w*|закон\w*|"
    r"овз|аооп|колледж\w*|вуз\w*|университет\w*|институт\w*|абитуриент\w*|магистратур\w*|"
    r"бакалавриат\w*|бюджетн\w+\s+мест\w*|стипенди\w*|каникул\w*|семестр\w*|четверт\w*)\b",
    re.IGNORECASE,
)

_SCHOOL_FRP_RE = re.compile(
    r"\b(?:фрп|фгос|фоп|санпин\w*|минпросвещ\w*|рособрнадзор\w*|роструд\w*|"
    r"едсоо|фипи|мцко|цмоко|икп\s*рао|егэ|огэ|впр|гиа|урок\w*|школ\w*|учител\w*|"
    r"педагог\w*|овз|аооп|приказ\w*\s*№|1[-–]11\s*класс\w*|\d{1,2}\s*класс\w*)\b",
    re.IGNORECASE,
)

_UNIVERSITY_ADMISSION_RE = re.compile(
    r"\b(?:бакалавриат\w*|магистратур\w*|абитуриент\w*|при[её]мн\w+\s+кампан\w*|"
    r"проходн\w+\s+балл\w*|конкурсн\w+\s+сумм\w*|бюджетн\w+\s+мест\w*)\b",
    re.IGNORECASE,
)

_INTERNAL_TOOL_RE = re.compile(
    r"\b(?:генератор\s+грамот|музыкальный\s+театр|векторный\s+редактор|конструктор\s+уроков|тренажер)\b",
    re.IGNORECASE,
)


def _fts_lemma(word: str) -> str:
    return normalize_token(word)


def is_education_or_normative_query(text: str, *, mode: str = "education") -> bool:
    t = text or ""
    if mode != "local_frp" and _UNIVERSITY_ADMISSION_RE.search(t) and not _SCHOOL_FRP_RE.search(t):
        return False
    return bool(_EDU_NORMATIVE_RE.search(t))


def build_fts_match_query(query: str) -> str:
    """Строит безопасный FTS5 запрос: сначала точное пересечение ключевых термов, fallback на OR."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", normalize_text(query or ""))
    terms: list[str] = []
    for w in words:
        if w in _STOPWORDS:
            continue
        if w.isdigit():
            if len(w) >= 2:
                terms.append(f'"{w}"')
        elif len(w) >= 3:
            lemma = _fts_lemma(w)
            if len(lemma) >= 3:
                terms.append(f"{lemma}*")
    if not terms:
        return ""
    unique_terms = list(dict.fromkeys(terms))[:8]
    return " OR ".join(unique_terms)


@dataclass(frozen=True)
class LocalDocHit:
    url: str
    title: str
    text: str
    kind: str = "official"


class LocalKnowledgeIndex:
    def __init__(self, db_paths: list[pathlib.Path] | None = None) -> None:
        default_paths = [
            knowledge_fts_path(),
            mirrors_fts_path(),
        ]
        env_db = os.environ.get("FACTCHECK_LOCAL_FTS_DB", "").strip()
        if env_db:
            default_paths.insert(0, pathlib.Path(env_db))
        self.db_paths = [p for p in (db_paths or default_paths) if p.exists()]

    def search_docs(self, query: str, *, limit: int = 4) -> list[LocalDocHit]:
        if not is_education_or_normative_query(query):
            return []

        fts_query = build_fts_match_query(query)
        if not fts_query or not self.db_paths:
            return []

        # Извлекаем нечисловые леммы запроса для фильтрации ложных совпадений
        query_words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", (query or "").casefold().replace("ё", "е"))
        query_lemmas = {
            _fts_lemma(w)
            for w in query_words
            if w not in _STOPWORDS and len(_fts_lemma(w)) >= 3
        }

        # Проверяем наличие номеров приказов или СанПиН в запросе
        required_numbers = re.findall(r"(?:№\s*\d+|санпин\s*[\d\.\-]+)", query, flags=re.IGNORECASE)
        norm_req_numbers = [re.sub(r"\s+", "", n.lower()) for n in required_numbers]

        query_mentions_internal_tool = bool(_INTERNAL_TOOL_RE.search(query))

        hits: list[LocalDocHit] = []
        seen_urls: set[str] = set()

        for db_path in self.db_paths:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
                try:
                    validate_index_version(conn, db_path)
                    tables = {
                        r[0]
                        for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
                        ).fetchall()
                    }
                    for tbl in sorted(tables):
                        # Динамически считываем колонки таблицы
                        cols_info = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                        col_names = {c[1] for c in cols_info}
                        if not col_names:
                            continue

                        sel_title = "title" if "title" in col_names else "'' AS title"
                        sel_url = "url" if "url" in col_names else "'' AS url"
                        sel_content = "content" if "content" in col_names else (
                            "raw_text" if "raw_text" in col_names else "'' AS content"
                        )
                        sel_corpus = "corpus" if "corpus" in col_names else "'' AS corpus"
                        sel_lem = "content_lemmatized" if "content_lemmatized" in col_names else "'' AS content_lemmatized"

                        try:
                            sql = (
                                f"SELECT {sel_title}, {sel_url}, {sel_content}, {sel_corpus}, {sel_lem} "
                                f"FROM {tbl} WHERE {tbl} MATCH ? ORDER BY rank LIMIT ?"
                            )
                            rows = conn.execute(sql, (fts_query, limit * 4)).fetchall()
                        except sqlite3.OperationalError:
                            continue

                        for title, url, content, corpus, content_lem in rows:
                            if not content:
                                continue
                            doc_url = url or f"https://edsoo.ru/local/{corpus or tbl}"
                            if doc_url in seen_urls:
                                continue

                            # Исключаем страницы внутренних тренажеров/редакторов портала
                            combined_text = f"{title} {content} {content_lem}".casefold().replace("ё", "е")
                            if not query_mentions_internal_tool and _INTERNAL_TOOL_RE.search(combined_text):
                                continue

                            # Проверка наличия номера приказа / СанПиН, если он был в запросе
                            if norm_req_numbers:
                                clean_combined = re.sub(r"\s+", "", combined_text)
                                if not any(num in clean_combined for num in norm_req_numbers):
                                    continue

                            # Документ принимается, только если содержит не менее 2 смысловых лемм запроса
                            if query_lemmas:
                                matched_lemmas = sum(1 for lem in query_lemmas if lem in combined_text)
                                if matched_lemmas < min(2, len(query_lemmas)):
                                    continue

                            seen_urls.add(doc_url)
                            hits.append(
                                LocalDocHit(
                                    url=doc_url,
                                    title=title or "База ФРП и нормативов РФ",
                                    text=content,
                                    kind="official",
                                )
                            )
                            if len(hits) >= limit:
                                return hits
                finally:
                    conn.close()
            except IndexOutdatedError:
                raise
            except Exception:
                continue
        return hits

