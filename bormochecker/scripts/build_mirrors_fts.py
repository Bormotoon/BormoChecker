#!/usr/bin/env python3
"""Индексатор локальных зеркал официальных образовательных сайтов в mirrors_fts.sqlite (FTS5 + WAL).

Зачем: локальные зеркала edsoo.ru, fipi.ru, rcoi.mcko.ru,
fgosreestr.edsoo.ru и др. индексируются в FTS5, что позволяет фактчекеру находить цитаты
из первоисточников за 5–10 мс (0 ₽), реконструируя оригинальный публичный URL https://<host>/<path>.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import sqlite3
import sys

if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

from bormochecker.paths import mirrors_dir, mirrors_fts_path, repo_root
from bormochecker.textnorm import NORMALIZER_VERSION, lemmatize_text

BASE_DIR = repo_root()
DEFAULT_MIRROR_DEST = mirrors_dir()
DEFAULT_DB_PATH = mirrors_fts_path()

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_html_content(raw_bytes: bytes) -> tuple[str, str]:
    """Извлекает заголовок и чистый текст из HTML-страницы."""
    for enc in ("utf-8", "windows-1251", "cp1251", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw_bytes.decode("utf-8", errors="ignore")

    title_match = _TITLE_RE.search(text)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""

    # Удаляем скрипты и стили
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    clean = _TAG_RE.sub(" ", clean)
    clean = html.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return title, clean


def extract_pdf_content(file_path: pathlib.Path, max_pages: int = 250) -> tuple[str, str]:
    """Извлекает текст из PDF с разметкой [page: N] между страницами."""
    import shutil
    import subprocess

    title = file_path.stem
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(file_path))
        title = doc.metadata.get("title") or title
        pages = []
        for i in range(min(len(doc), max_pages)):
            p_text = doc[i].get_text().strip()
            if p_text:
                pages.append(f"[page: {i + 1}]\n{p_text}")
        if pages:
            return title, "\n\n".join(pages)
    except Exception:
        pass

    try:
        import pypdf
        reader = pypdf.PdfReader(str(file_path))
        pages = []
        for i, p in enumerate(reader.pages[:max_pages], start=1):
            p_text = (p.extract_text() or "").strip()
            if p_text:
                pages.append(f"[page: {i}]\n{p_text}")
        if pages:
            return title, "\n\n".join(pages)
    except Exception:
        pass

    if shutil.which("pdftotext"):
        try:
            res = subprocess.run(
                ["pdftotext", "-layout", str(file_path), "-"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            raw_pages = res.stdout.split("\x0c")
            pages = []
            for idx, p in enumerate(raw_pages[:max_pages], start=1):
                p_text = p.strip()
                if p_text:
                    pages.append(f"[page: {idx}]\n{p_text}")
            if pages:
                return title, "\n\n".join(pages)
        except Exception:
            pass

    try:
        raw_bytes = file_path.read_bytes()
        streams = re.findall(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw_bytes, flags=re.DOTALL)
        if streams:
            pages = []
            for idx, s in enumerate(streams[:max_pages], start=1):
                m = re.findall(r"\((.*?)\)\s*Tj", s.decode("latin1", errors="ignore"))
                plain = " ".join(m).strip()
                if plain:
                    pages.append(f"[page: {idx}]\n{plain}")
            if pages:
                return title, "\n\n".join(pages)
    except Exception:
        pass

    return title, ""


def init_db(db_path: pathlib.Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mirror_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corpus TEXT NOT NULL,
            doc_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS mirrors_fts USING fts5(
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
        CREATE TABLE IF NOT EXISTS index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO index_metadata (key, value) VALUES ('normalizer_version', ?)",
        (NORMALIZER_VERSION,),
    )
    return conn


def build_mirrors_index(
    mirror_dir: pathlib.Path,
    db_path: pathlib.Path,
    limit: int | None = None,
) -> int:
    if not mirror_dir.exists():
        print(f"Каталог зеркал не найден: {mirror_dir}", file=sys.stderr)
        return 0

    conn = init_db(db_path)
    count = 0

    # Пропускаем служебные каталоги
    skip_dirs = {"_mirrors", "_logs"}

    for host_dir in sorted(mirror_dir.iterdir()):
        if not host_dir.is_dir() or host_dir.name in skip_dirs or host_dir.name.startswith("."):
            continue
        host = host_dir.name

        for root, dirs, files in os.walk(host_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                fpath = pathlib.Path(root) / f
                rel_path = fpath.relative_to(host_dir).as_posix()
                if rel_path.endswith("index.html"):
                    rel_url_path = rel_path[:-10]
                else:
                    rel_url_path = rel_path
                public_url = f"https://{host}/{rel_url_path.lstrip('/')}"
                doc_id = f"{host}:{rel_path}"

                ext = fpath.suffix.lower()
                title, content = "", ""
                if ext in (".html", ".htm"):
                    try:
                        title, content = extract_html_content(fpath.read_bytes())
                    except Exception:
                        continue
                elif ext == ".json":
                    try:
                        data = json.loads(fpath.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            title = str(data.get("title") or data.get("name") or fpath.stem)
                            content = " ".join(f"{k}: {v}" for k, v in data.items() if isinstance(v, (str, int, float)))
                    except Exception:
                        continue
                elif ext == ".pdf":
                    title, content = extract_pdf_content(fpath)
                elif ext in (".txt", ".md"):
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                        title = fpath.stem
                    except Exception:
                        continue
                else:
                    continue

                if not content or len(content.strip()) < 100:
                    continue

                chunk_size = 12000
                chunk_overlap = 1500
                chunks = []
                if len(content) > chunk_size:
                    start = 0
                    step = chunk_size - chunk_overlap
                    while start < len(content):
                        c = content[start : start + chunk_size]
                        if c.strip():
                            chunks.append(c)
                        start += step
                else:
                    chunks = [content]

                for idx, chunk in enumerate(chunks):
                    chunk_doc_id = f"{doc_id}#chunk{idx+1}" if len(chunks) > 1 else doc_id
                    chunk_title = f"{title} (часть {idx+1}/{len(chunks)})" if len(chunks) > 1 else (title or public_url)
                    chunk_lem = lemmatize_text(chunk)
                    try:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO mirror_documents (corpus, doc_id, title, url, raw_text)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (host, chunk_doc_id, chunk_title, public_url, chunk),
                        )
                        conn.execute(
                            """
                            INSERT INTO mirrors_fts (corpus, doc_id, title, url, content, content_lemmatized)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (host, chunk_doc_id, chunk_title, public_url, chunk, chunk_lem),
                        )
                        count += 1
                        if count % 100 == 0:
                            conn.commit()
                    except Exception as e:
                        print(f"Ошибка индексации {fpath} (чанк {idx}): {e}", file=sys.stderr)

                if limit and count >= limit:
                    break
            if limit and count >= limit:
                break

    conn.commit()
    conn.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборщик SQLite FTS5 индекса из локальных зеркал образовательных сайтов")
    parser.add_argument("--mirror-dir", type=pathlib.Path, default=DEFAULT_MIRROR_DEST, help="Путь к зеркалам сайтов")
    parser.add_argument("--db-path", type=pathlib.Path, default=DEFAULT_DB_PATH, help="Выходной SQLite файл")
    parser.add_argument("--limit", type=int, default=None, help="Лимит индексируемых документов")
    args = parser.parse_args()

    indexed = build_mirrors_index(args.mirror_dir, args.db_path, limit=args.limit)
    print(f"Успешно проиндексировано документов: {indexed} -> {args.db_path}")


if __name__ == "__main__":
    main()
