import datetime
import json
import pytest
from unittest.mock import patch

from bormochecker.contract import Claim, Costs, Label, Report
from bormochecker.scripts import eval_factcheck_benchmark


def test_eval_failures_remain_in_denominator_and_exit_code_1(tmp_path, monkeypatch):
    """T407 / [E06]: Прогон с 1 сбоем из 2 кейсов оставляет сбой в знаменателе (1/2) и возвращает exit code 1."""
    eval_dir = tmp_path / "eval"
    items_dir = eval_dir / "items"
    items_dir.mkdir(parents=True)

    text1 = "Первый факт ложный в тексте."
    # Кейс 1: 1 опровержение
    c1 = items_dir / "case1"
    c1.mkdir()
    (c1 / "text.txt").write_text(text1, encoding="utf-8")
    (c1 / "gold.json").write_text(
        json.dumps({
            "event": "Событие 1",
            "claims": [
                {
                    "quote": "Первый факт ложный",
                    "verdict": "contradicted",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    text2 = "Второй текст для проверки фактов."
    # Кейс 2: 1 опровержение
    c2 = items_dir / "case2"
    c2.mkdir()
    (c2 / "text.txt").write_text(text2, encoding="utf-8")
    (c2 / "gold.json").write_text(
        json.dumps({
            "event": "Событие 2",
            "claims": [
                {
                    "quote": "Второй факт ложный",
                    "verdict": "contradicted",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    run_dir = tmp_path / "run"
    report_file = tmp_path / "report.md"

    claim_text = "Первый факт ложный"
    # Подменяем check: для case1 возвращаем успех, для case2 бросаем исключение
    def fake_check(text, **kwargs):
        if "Первый" in text:
            return Report(
                text=text,
                status="ok",
                summary="Успешная проверка",
                costs=Costs(started_at=datetime.datetime.now(datetime.timezone.utc)),
                claims=[
                    Claim(
                        start=0,
                        end=len(claim_text),
                        text=claim_text,
                        label=Label.error,
                        explanation="Обнаружено опровержение факта",
                    )
                ],
            )
        raise RuntimeError("Искусственный сбой пайплайна (Artificial crash)")

    monkeypatch.setattr(eval_factcheck_benchmark.pipeline, "check", fake_check)

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--eval-dir",
        str(eval_dir),
        "--run-dir",
        str(run_dir),
        "--report-file",
        str(report_file),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()

    # Должен завершиться с кодом 1 (критерии не выполнены из-за сбоя)
    assert exc_info.value.code == 1

    # Проверяем scores.json
    scores_file = run_dir / "scores.json"
    assert scores_file.exists()
    scores = json.loads(scores_file.read_text("utf-8"))
    bench = scores["benchmark"]
    assert len(bench) == 2

    res1 = next(r for r in bench if r["item_id"] == "case1")
    res2 = next(r for r in bench if r["item_id"] == "case2")

    assert res1["is_failed"] is False
    assert res1["gold_contradictions"] == 1
    assert res1["caught_errors"] == 1

    # Сбойный кейс:
    assert res2["is_failed"] is True
    # Знаменатель сохранен!
    assert res2["gold_contradictions"] == 1
    assert res2["caught_errors"] == 0

    # Общий знаменатель должен быть 2 (1+1)
    total_gold_contr = sum(r["gold_contradictions"] for r in bench)
    total_caught = sum(r["caught_errors"] for r in bench)
    assert total_gold_contr == 2
    assert total_caught == 1

    # Проверяем отчет
    report_content = report_file.read_text("utf-8")
    assert "| **Сбои при проверке (Failures)** | **`1`** | `0` (критично) | ❌ 1 |" in report_content
    assert "1 / 2" in report_content
    assert "❌ Да" in report_content
    assert "❌ Сбой" in report_content
