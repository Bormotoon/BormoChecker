import datetime
import json
import os
import pytest
from unittest.mock import patch

from bormochecker.contract import Claim, Costs, Label, Report
from bormochecker.retrieval import NetworkNotAllowedError, SafeDocumentFetcher
from bormochecker.scripts import eval_factcheck_benchmark
from bormochecker.search_cache import SearchCache


def test_offline_mode_manifest_and_file_isolation(tmp_path, monkeypatch):
    """T408 / [E07]: Офлайн-прогон не создает и не меняет файлы вне временного каталога; isolation=offline."""
    eval_dir = tmp_path / "eval"
    items_dir = eval_dir / "items"
    c1 = items_dir / "case1"
    c1.mkdir(parents=True)
    (c1 / "text.txt").write_text("Проверочный текст для офлайн-теста.", encoding="utf-8")
    (c1 / "gold.json").write_text(
        json.dumps({
            "event": "Офлайн тест",
            "claims": [
                {
                    "quote": "Проверочный текст",
                    "verdict": "supported",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    run_dir = tmp_path / "run"
    report_file = tmp_path / "report.md"

    # Запоминаем состояние каталога factcheck/data до запуска
    repo_root = eval_factcheck_benchmark.REPO_ROOT
    data_dir = repo_root / "factcheck" / "data"
    data_snapshot_before = {}
    if data_dir.is_dir():
        for p in data_dir.rglob("*"):
            if p.is_file():
                data_snapshot_before[str(p)] = p.stat().st_mtime

    def fake_check(text, **kwargs):
        # Проверяем, что в момент проверки режим офлайн активен
        assert os.environ.get("FACTCHECK_OFFLINE") == "1"
        return Report(
            text=text,
            status="ok",
            summary="Проверено по фикстурам",
            costs=Costs(started_at=datetime.datetime.now(datetime.timezone.utc)),
            claims=[
                Claim(
                    start=0,
                    end=len("Проверочный текст"),
                    text="Проверочный текст",
                    label=Label.confirmed,
                    explanation="Подтверждено локальным документом",
                )
            ],
        )

    monkeypatch.setattr(eval_factcheck_benchmark.pipeline, "check", fake_check)

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--offline",
        "--eval-dir", str(eval_dir),
        "--run-dir", str(run_dir),
        "--report-file", str(report_file),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()
        assert exc_info.value.code == 0

    # Проверяем манифест
    manifest_file = run_dir / "manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text("utf-8"))
    assert manifest["isolation"] == "offline"

    # Проверяем, что отчет содержит пометку об изоляции
    report_text = report_file.read_text("utf-8")
    assert "Изоляция: `offline`" in report_text

    # Проверяем, что файлы в bormochecker/data не изменились и новые не появились
    if data_dir.is_dir():
        for p in data_dir.rglob("*"):
            if p.is_file():
                if str(p) in data_snapshot_before:
                    assert p.stat().st_mtime == data_snapshot_before[str(p)], f"Файл {p} был изменен во время офлайн-прогона!"
                else:
                    pytest.fail(f"Новый файл {p} появился в боевом каталоге data во время офлайн-прогона!")


def test_offline_mode_forbids_prod_cache(monkeypatch):
    """T408: В режиме offline запрещено чтение боевого кеша."""
    monkeypatch.setenv("FACTCHECK_OFFLINE", "1")
    monkeypatch.delenv("FACTCHECK_CACHE_DB", raising=False)

    with pytest.raises(RuntimeError, match="Запрещено чтение боевого кеша в режиме offline"):
        SearchCache()


def test_offline_mode_blocks_network(monkeypatch):
    """T408: В режиме offline запрещены сетевые запросы."""
    monkeypatch.setenv("FACTCHECK_OFFLINE", "1")
    fetcher = SafeDocumentFetcher()
    with pytest.raises(NetworkNotAllowedError, match="Сетевые запросы запрещены"):
        fetcher.fetch("https://example.com/test")


def test_offline_and_live_benchmark_incompatible(tmp_path):
    """T408: Флаги --offline и --live-benchmark несовместимы (код 2)."""
    eval_dir = tmp_path / "eval"
    case_dir = eval_dir / "items" / "case1"
    case_dir.mkdir(parents=True)
    (case_dir / "text.txt").write_text("Текст", encoding="utf-8")

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--offline",
        "--live-benchmark",
        "--eval-dir", str(eval_dir),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()
        assert exc_info.value.code == 2
