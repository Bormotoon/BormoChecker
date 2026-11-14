"""Factcheck package."""

try:
    from dotenv import load_dotenv

    from bormochecker.paths import repo_root

    _repo_root = repo_root()
    _env_path = _repo_root / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
    else:
        load_dotenv()
except ImportError:
    pass
