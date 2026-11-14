"""Модуль манифеста прогона и сохранения сырых результатов оценки (T403 / [E01])."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import pathlib
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from bormochecker.paths import mirrors_fts_path, repo_root


def compute_file_sha256(path: pathlib.Path) -> str | None:
    """Вычисляет sha256 хеш файла."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_dir_sha256_map(directory: pathlib.Path) -> dict[str, str]:
    """Рекурсивно вычисляет sha256 для всех файлов в каталоге."""
    result: dict[str, str] = {}
    if not directory.is_dir():
        return result
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel_path = str(p.relative_to(directory))
            file_hash = compute_file_sha256(p)
            if file_hash:
                result[rel_path] = file_hash
    return result


def get_git_info() -> tuple[str, bool]:
    """Возвращает (commit_hash, dirty_tree)."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        commit = "unknown"

    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        dirty = bool(status)
    except Exception:
        dirty = False

    return commit, dirty


def get_key_package_versions() -> dict[str, str]:
    """Возвращает версии установленных ключевых пакетов."""
    packages = ["pydantic", "httpx", "fastapi", "pytest", "ruff", "anyio", "starlette"]
    versions: dict[str, str] = {}
    for pkg in packages:
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except Exception:
            versions[pkg] = "not_installed"
    return versions


@dataclass
class RunManifest:
    commit: str
    dirty_tree: bool
    python_version: str
    package_versions: dict[str, str]
    model: str
    model_params: dict[str, Any]
    provider: str
    mode: str
    reference_datetime: str
    settings: dict[str, Any]
    eval_sha256: dict[str, str]
    fts_index_sha256: str | None
    start_time: str
    end_time: str
    cmdline: list[str]
    isolation: str = "live"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_run_directory(
    base_runs_dir: pathlib.Path | None = None,
    commit: str | None = None,
    dt: datetime | None = None,
) -> pathlib.Path:
    """Создает каталог runs/<UTC-время>_<короткий хеш>/ и подкаталог raw/."""
    runs_dir = base_runs_dir or (repo_root() / "runs")
    runs_dir.mkdir(parents=True, exist_ok=True)

    utc_dt = dt or datetime.now(timezone.utc)
    time_str = utc_dt.strftime("%Y%m%dT%H%M%SZ")
    commit_str = commit or get_git_info()[0]

    run_path = runs_dir / f"{time_str}_{commit_str}"
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "raw").mkdir(parents=True, exist_ok=True)
    return run_path


def build_manifest(
    model: str,
    provider: str,
    mode: str,
    reference_datetime: str,
    eval_dir: pathlib.Path,
    start_time: datetime,
    end_time: datetime,
    model_params: dict[str, Any] | None = None,
    extra_settings: dict[str, Any] | None = None,
    cmdline: list[str] | None = None,
    isolation: str = "live",
) -> RunManifest:
    """Формирует объект манифеста прогона."""
    commit, dirty = get_git_info()
    fts_path = mirrors_fts_path()
    fts_sha = compute_file_sha256(fts_path) if fts_path.is_file() else None

    # Собираем настройки окружения FACTCHECK_* и LLM_*
    settings: dict[str, Any] = {}
    for k, v in os.environ.items():
        if k.startswith(("FACTCHECK_", "LLM_")):
            # Маскируем секреты в настройках
            if any(s in k.lower() for s in ("key", "token", "secret", "password")):
                settings[k] = "***"
            else:
                settings[k] = v
    if extra_settings:
        settings.update(extra_settings)

    # Гарантируем, что provider и model не null
    final_provider = provider or os.environ.get("LLM_PROVIDER") or "local"
    final_model = model or os.environ.get("LLM_MODEL") or "gemma4-26b-mtp"

    params = dict(model_params or {})
    params.setdefault("temperature", float(os.environ.get("FACTCHECK_EVAL_TEMPERATURE", "0.0")))
    params.setdefault("seed", int(os.environ.get("FACTCHECK_EVAL_SEED", "42")))

    return RunManifest(
        commit=commit,
        dirty_tree=dirty,
        python_version=sys.version,
        package_versions=get_key_package_versions(),
        model=final_model,
        model_params=params,
        provider=final_provider,
        mode=mode,
        reference_datetime=reference_datetime,
        settings=settings,
        eval_sha256=compute_dir_sha256_map(eval_dir / "items") if (eval_dir / "items").is_dir() else {},
        fts_index_sha256=fts_sha,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
        cmdline=cmdline or sys.argv,
        isolation=isolation,
    )


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def save_run_artifacts(
    run_dir: pathlib.Path,
    manifest: RunManifest,
    raw_reports: dict[str, dict[str, Any]],
    scores: dict[str, Any] | list[Any],
    report_markdown: str,
    stdout_content: str | None = None,
) -> None:
    """Сохраняет все артефакты прогона: manifest.json, raw/<case_id>.json, scores.json, report.md, stdout.log."""
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. manifest.json
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
    )

    # 2. raw/<case_id>.json
    for case_id, report_data in raw_reports.items():
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in case_id)
        (raw_dir / f"{safe_id}.json").write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
        )

    # 3. scores.json
    (run_dir / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
    )

    # 4. report.md
    (run_dir / "report.md").write_text(report_markdown, encoding="utf-8")

    # 5. stdout.log
    if stdout_content is not None:
        (run_dir / "stdout.log").write_text(stdout_content, encoding="utf-8")


class LogTee:
    """Дублирует sys.stdout в файл stdout.log."""

    def __init__(self, log_path: pathlib.Path) -> None:
        self.log_path = log_path
        self._stdout = sys.stdout
        self._file = None

    def __enter__(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "w", encoding="utf-8")
        sys.stdout = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout
        if self._file:
            self._file.close()

    def write(self, data: str) -> int:
        self._stdout.write(data)
        if self._file:
            self._file.write(data)
            self._file.flush()
        return len(data)

    def flush(self) -> None:
        self._stdout.flush()
        if self._file:
            self._file.flush()
