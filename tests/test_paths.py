from pathlib import Path


def test_repo_root_normal():
    from bormochecker.paths import repo_root
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "bormochecker").is_dir()


def test_repo_root_with_env(monkeypatch):
    from bormochecker.paths import repo_root
    monkeypatch.setenv("BORMOCHECKER_ROOT", "/tmp/x")
    root = repo_root()
    assert root == Path("/tmp/x").resolve()


def test_helper_paths_respect_env(monkeypatch, tmp_path):
    from bormochecker.paths import (
        fact_ledger_path,
        knowledge_fts_path,
        mirrors_dir,
        mirrors_fts_path,
        search_cache_path,
    )

    monkeypatch.setenv("FACTCHECK_MIRRORS_DIR", str(tmp_path / "mirrors"))
    monkeypatch.setenv("FACTCHECK_LOCAL_FTS_DB", str(tmp_path / "k.sqlite"))
    monkeypatch.setenv("FACTCHECK_MIRRORS_FTS_DB", str(tmp_path / "m.sqlite"))
    monkeypatch.setenv("FACTCHECK_CACHE_DB", str(tmp_path / "c.sqlite"))
    monkeypatch.setenv("FACTCHECK_FACT_LEDGER_PATH", str(tmp_path / "fl.jsonl"))

    assert mirrors_dir() == (tmp_path / "mirrors").resolve()
    assert knowledge_fts_path() == (tmp_path / "k.sqlite").resolve()
    assert mirrors_fts_path() == (tmp_path / "m.sqlite").resolve()
    assert search_cache_path() == (tmp_path / "c.sqlite").resolve()
    assert fact_ledger_path() == (tmp_path / "fl.jsonl").resolve()
