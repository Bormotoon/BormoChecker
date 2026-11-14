import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from bormochecker.contract import Report
from bormochecker.scripts.eval_factcheck_benchmark import main


def test_manifest_building_and_artifacts(tmp_path):
    eval_dir = tmp_path / "eval"
    case1_dir = eval_dir / "items" / "case1"
    case2_dir = eval_dir / "items" / "case2"
    case1_dir.mkdir(parents=True)
    case2_dir.mkdir(parents=True)

    (case1_dir / "text.txt").write_text("Текст первого кейса для проверки.", encoding="utf-8")
    (case1_dir / "gold.json").write_text(
        json.dumps({"split": "dev", "event": "Кейс 1", "claims": []}), encoding="utf-8"
    )

    (case2_dir / "text.txt").write_text("Текст второго кейса для проверки.", encoding="utf-8")
    (case2_dir / "gold.json").write_text(
        json.dumps({"split": "test", "event": "Кейс 2", "claims": []}), encoding="utf-8"
    )

    run_dir = tmp_path / "run_output"

    dummy_report = Report(
        text="Текст первого кейса для проверки.",
        status="ok",
        summary="Все факты подтверждены",
        claims=[],
        limitations=[],
        costs={
            "provider": "mock_provider",
            "model": "mock_model",
            "started_at": datetime.now(timezone.utc),
        },
    )

    with patch("sys.argv", [
        "eval_factcheck_benchmark.py",
        "--eval-dir", str(eval_dir),
        "--run-dir", str(run_dir),
        "--no-reference-cases",
        "--report-file", str(tmp_path / "test_report.md"),
    ]), patch("bormochecker.scripts.eval_factcheck_benchmark.pipeline.check", return_value=dummy_report), \
       patch.dict("os.environ", {"LLM_PROVIDER": "local_test", "LLM_MODEL": "test-gemma"}):

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    # Проверяем наличие всех обязательных файлов согласно T403 / [E01]
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "scores.json").is_file()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "stdout.log").is_file()
    assert (run_dir / "raw" / "case1.json").is_file()
    assert (run_dir / "raw" / "case2.json").is_file()

    # Проверяем манифест
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] is not None
    assert manifest["provider"] != "null"
    assert manifest["provider"] == "local_test"

    assert manifest["model"] is not None
    assert manifest["model"] != "null"
    assert manifest["model"] == "test-gemma"

    assert "commit" in manifest
    assert "dirty_tree" in manifest
    assert "package_versions" in manifest
    assert "python_version" in manifest
    assert "reference_datetime" in manifest
    assert "eval_sha256" in manifest
    assert len(manifest["eval_sha256"]) >= 4  # 2 text.txt + 2 gold.json

    # Проверяем сырые отчеты
    c1_raw = json.loads((run_dir / "raw" / "case1.json").read_text(encoding="utf-8"))
    assert c1_raw["status"] == "ok"
    assert c1_raw["summary"] == "Все факты подтверждены"

    # Проверяем scores.json
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    assert "benchmark" in scores
    assert len(scores["benchmark"]) == 2

    # Проверяем stdout.log
    stdout_content = (run_dir / "stdout.log").read_text(encoding="utf-8")
    assert "Запуск комплексного фактчек-тестирования" in stdout_content
