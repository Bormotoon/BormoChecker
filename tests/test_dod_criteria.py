import datetime
import json
import pytest
from unittest.mock import patch

from bormochecker.contract import Claim, Costs, Label, Report
from bormochecker.scripts import eval_factcheck_benchmark
from bormochecker.scripts.eval_factcheck_benchmark import (
    compute_duration_metrics,
    load_dod_criteria,
)


def test_load_dod_criteria_defaults_and_yaml():
    """T410: Проверка загрузки порогов Definition of Done из dod.yaml."""
    dod = load_dod_criteria()
    assert dod["min_contradictions_caught"] == 11
    assert dod["total_benchmark_contradictions"] == 12
    assert dod["max_false_confirmations"] == 0
    assert dod["max_false_accusations"] == 2
    assert dod["clarify_counts_as_confirmed"] is False


def test_duration_metrics_uses_benchmark_and_falls_back_to_reference_cases():
    """Расчет среднего времени, p95 и максимума по эталонным текстам (и fallback на контрольные кейсы)."""
    class MockRes:
        def __init__(self, dur):
            self.duration_s = dur

    bench_results = [MockRes(10.0), MockRes(20.0), MockRes(30.0)]
    ref_results = [{"duration_s": 400.0}, {"duration_s": 500.0}]

    avg_d, p95_d, max_d = compute_duration_metrics(bench_results, ref_results)
    assert avg_d == 20.0
    assert max_d == 30.0
    assert 20.0 <= p95_d <= 30.0

    # Если эталонных текстов нет — считаем по контрольным кейсам
    avg_h, p95_h, max_h = compute_duration_metrics([], ref_results)
    assert avg_h == 450.0
    assert max_h == 500.0
    assert p95_h >= 450.0


def test_dod_criteria_9_of_12_fails_with_exit_code_1(tmp_path, monkeypatch):
    """T410: При результате 9 из 12 (75% >= 70%, но < 11 DoD) раннер сообщает «не выполнено» (exit code 1)."""
    eval_dir = tmp_path / "eval"
    items_dir = eval_dir / "items"
    items_dir.mkdir(parents=True)

    # Создаем 12 кейсов, в каждом по 1 эталонному опровержению (итого 12)
    for i in range(1, 13):
        c_dir = items_dir / f"case_{i:02d}"
        c_dir.mkdir()
        (c_dir / "text.txt").write_text(f"Ложный факт {i} в тексте.", encoding="utf-8")
        (c_dir / "gold.json").write_text(
            json.dumps({
                "event": f"Кейс {i}",
                "claims": [
                    {
                        "quote": f"Ложный факт {i}",
                        "verdict": "contradicted",
                    }
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    # dod.yaml требует минимум 11 из 12
    (eval_dir / "dod.yaml").write_text("""
definition_of_done:
  min_contradictions_caught: 11
  total_benchmark_contradictions: 12
  max_false_confirmations: 0
  max_false_accusations: 2
  target_avg_duration_seconds: 45.0
  time_limit_per_case_seconds: 600.0
""", encoding="utf-8")

    run_dir = tmp_path / "run"
    report_file = tmp_path / "report.md"

    # Подменяем check: первые 9 кейсов ловят ошибку (Label.error), оставшиеся 3 пропускают (Label.clarify)
    call_idx = 0

    def fake_check(text, **kwargs):
        nonlocal call_idx
        call_idx += 1
        lbl = Label.error if call_idx <= 9 else Label.clarify
        q = f"Ложный факт {call_idx}"
        return Report(
            text=text,
            status="ok",
            summary="Проверка DoD",
            costs=Costs(started_at=datetime.datetime.now(datetime.timezone.utc)),
            claims=[
                Claim(
                    start=0,
                    end=len(q),
                    text=q,
                    label=lbl,
                    explanation="DoD тест",
                )
            ],
        )

    monkeypatch.setattr(eval_factcheck_benchmark.pipeline, "check", fake_check)

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--eval-dir", str(eval_dir),
        "--run-dir", str(run_dir),
        "--report-file", str(report_file),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()
        # Должен быть код 1, так как 9 < 11 (критерий DoD не выполнен)
        assert exc_info.value.code == 1

    report_content = report_file.read_text("utf-8")
    assert "9 / 12" in report_content
    assert "не выполнено" in report_content
