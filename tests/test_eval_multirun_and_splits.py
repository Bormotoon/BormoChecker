import datetime
import json
import pytest
from unittest.mock import patch

from bormochecker.agreement import cohen_kappa
from bormochecker.contract import Claim, Costs, Label, Report
from bormochecker.scripts import eval_factcheck_benchmark


def test_cohen_kappa_computation():
    """T409 / [E02]: Проверка вычисления коэффициента каппа Коэна."""
    # 1. Полное согласие
    r1 = ["supported", "contradicted", "clarify"]
    r2 = ["supported", "contradicted", "clarify"]
    assert cohen_kappa(r1, r2) == 1.0

    # 2. Частичное согласие
    r1 = ["supported", "supported", "contradicted", "contradicted"]
    r2 = ["supported", "contradicted", "contradicted", "supported"]
    k = cohen_kappa(r1, r2)
    assert -1.0 <= k <= 1.0
    assert k == 0.0  # 50% observed, 50% expected by chance

    # 3. Пустые списки
    assert cohen_kappa([], []) == 1.0

    # 4. Разная длина
    with pytest.raises(ValueError, match="Длины списков разметки должны совпадать"):
        cohen_kappa(["a"], ["a", "b"])


def test_splits_json_and_split_filtering(tmp_path):
    """T409: Фильтрация по splits.json (--split dev / --split holdout / --split test)."""
    eval_dir = tmp_path / "eval"
    items_dir = eval_dir / "items"
    items_dir.mkdir(parents=True)

    for cid in ["case_a", "case_b", "case_c"]:
        c_dir = items_dir / cid
        c_dir.mkdir()
        (c_dir / "text.txt").write_text(f"Текст для {cid}", encoding="utf-8")
        (c_dir / "gold.json").write_text(json.dumps({"event": cid, "claims": []}), encoding="utf-8")

    # Создаем splits.json: case_a, case_b в dev, case_c в holdout
    splits_data = {
        "seed": 42,
        "dev": ["case_a", "case_b"],
        "holdout": ["case_c"],
    }
    (eval_dir / "splits.json").write_text(json.dumps(splits_data), encoding="utf-8")

    # 1. Загрузка dev
    items_dev = eval_factcheck_benchmark.load_benchmark_items(eval_dir, split_filter="dev")
    assert sorted(i["id"] for i in items_dev) == ["case_a", "case_b"]
    assert all(i["split"] == "dev" for i in items_dev)

    # 2. Загрузка holdout
    items_holdout = eval_factcheck_benchmark.load_benchmark_items(eval_dir, split_filter="holdout")
    assert [i["id"] for i in items_holdout] == ["case_c"]
    assert items_holdout[0]["split"] == "holdout"

    # 3. Загрузка test (алиас holdout)
    items_test = eval_factcheck_benchmark.load_benchmark_items(eval_dir, split_filter="test")
    assert [i["id"] for i in items_test] == ["case_c"]

    # 4. Загрузка all
    items_all = eval_factcheck_benchmark.load_benchmark_items(eval_dir, split_filter="all")
    assert len(items_all) == 3


def test_multirun_evaluation_and_statistics(tmp_path, monkeypatch):
    """T409: Прогон с --runs 3 вычисляет мин/среднее/макс и долю расхождений."""
    eval_dir = tmp_path / "eval"
    items_dir = eval_dir / "items"
    c1 = items_dir / "case1"
    c1.mkdir(parents=True)
    (c1 / "text.txt").write_text("Текст новости для проверки стабильности.", encoding="utf-8")
    (c1 / "gold.json").write_text(
        json.dumps({
            "event": "Тест стабильности",
            "claims": [
                {
                    "quote": "Текст новости",
                    "verdict": "contradicted",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    run_dir = tmp_path / "run"
    report_file = tmp_path / "report.md"

    call_count = 0

    def fake_check(text, **kwargs):
        nonlocal call_count
        call_count += 1
        # Имитируем небольшую вариативность между прогонами:
        # В прогоне 1 и 2 - поймана ошибка (Label.error)
        # В прогоне 3 - статус clarify (Label.clarify)
        lbl = Label.error if call_count <= 2 else Label.clarify
        return Report(
            text=text,
            status="ok",
            summary="Проверка стабильности",
            costs=Costs(started_at=datetime.datetime.now(datetime.timezone.utc)),
            claims=[
                Claim(
                    start=0,
                    end=len("Текст новости"),
                    text="Текст новости",
                    label=lbl,
                    explanation="Проверка",
                )
            ],
        )

    monkeypatch.setattr(eval_factcheck_benchmark.pipeline, "check", fake_check)

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--runs", "3",
        "--eval-dir", str(eval_dir),
        "--run-dir", str(run_dir),
        "--report-file", str(report_file),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()
        assert exc_info.value.code == 0

    # Должно быть ровно 3 вызова (3 прогона по 1 тексту)
    assert call_count == 3

    # Проверяем scores.json
    scores_file = run_dir / "scores.json"
    scores = json.loads(scores_file.read_text("utf-8"))
    assert "multirun" in scores
    mrun = scores["multirun"]
    assert mrun["runs_count"] == 3
    assert mrun["disagreement_rate"] == 100.0
    assert "stats" in mrun
    assert "caught_errors" in mrun["stats"]
    assert mrun["stats"]["caught_errors"]["min"] == 0
    assert mrun["stats"]["caught_errors"]["max"] == 1
    assert round(mrun["stats"]["caught_errors"]["mean"], 2) == 0.67

    # Проверяем отчет
    report_text = report_file.read_text("utf-8")
    assert "## 6. Анализ стабильности повторных прогонов (Repeated Runs)" in report_text
    assert "- **Число повторов**: `3`" in report_text


def test_invalid_runs_count_fails(tmp_path):
    """T409: --runs 0 завершается с кодом ошибки 2."""
    eval_dir = tmp_path / "eval"
    case_dir = eval_dir / "items" / "case1"
    case_dir.mkdir(parents=True)
    (case_dir / "text.txt").write_text("Текст", encoding="utf-8")

    test_argv = [
        "eval_factcheck_benchmark.py",
        "--runs", "0",
        "--eval-dir", str(eval_dir),
        "--no-reference-cases",
    ]

    with patch("sys.argv", test_argv):
        with pytest.raises(SystemExit) as exc_info:
            eval_factcheck_benchmark.main()
        assert exc_info.value.code == 2
