import os
from pathlib import Path


def repo_root() -> Path:
    env_root = os.environ.get("BORMOCHECKER_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file() or (parent / ".git").is_dir():
            return parent

    raise RuntimeError(
        "Cannot determine BormoChecker repository root. "
        "Ensure pyproject.toml or .git exists in parent directories, or set BORMOCHECKER_ROOT."
    )


def mirrors_dir() -> Path:
    env_val = os.environ.get("FACTCHECK_MIRRORS_DIR", "").strip() or os.environ.get("MIRROR_DEST", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "data" / "site_mirrors"


def knowledge_fts_path() -> Path:
    env_val = os.environ.get("FACTCHECK_LOCAL_FTS_DB", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "knowledge_fts.sqlite"


def mirrors_fts_path() -> Path:
    env_val = os.environ.get("FACTCHECK_MIRRORS_FTS_DB", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "mirrors_fts.sqlite"


def search_cache_path() -> Path:
    env_val = os.environ.get("FACTCHECK_CACHE_DB", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "factcheck_search_cache.sqlite"


def fact_ledger_path() -> Path:
    env_val = os.environ.get("FACTCHECK_FACT_LEDGER_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "fact_ledger.jsonl"


def official_domains_path() -> Path:
    env_val = os.environ.get("FACTCHECK_OFFICIAL_DOMAINS_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "official_domains.txt"


def norms_path() -> Path:
    env_val = os.environ.get("FACTCHECK_NORMS_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "norms.yaml"


def opinion_markers_path() -> Path:
    env_val = os.environ.get("FACTCHECK_OPINION_MARKERS_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "opinion_markers.txt"


def aggregator_patterns_path() -> Path:
    env_val = os.environ.get("FACTCHECK_AGGREGATOR_PATTERNS_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "aggregator_patterns.txt"


def gpu_lock_path() -> Path:
    env_val = os.environ.get("FACTCHECK_LOCK_PATH", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "bormochecker" / "data" / "gpu_process.lock"


def eval_dir() -> Path:
    env_val = os.environ.get("FACTCHECK_EVAL_DIR", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return repo_root() / "eval"


def data_dir() -> Path:
    return repo_root() / "bormochecker" / "data"
