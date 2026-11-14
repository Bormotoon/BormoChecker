import pytest
from unittest.mock import patch

from bormochecker.scripts.eval_factcheck_benchmark import (
    generate_markdown_report,
    main,
)


def test_empty_results_no_forbidden_phrases(tmp_path):
    report_file = tmp_path / "report.md"
    report_text = generate_markdown_report(
        benchmark_results=[],
        reference_results=[],
        output_path=report_file,
        git_commit="test_commit",
    )

    # Запрещены зашитые зелёные статусы согласно T404 / [E03]
    assert "100%" not in report_text
    assert "Полный охват" not in report_text
    assert "$0.00" not in report_text
    assert "Нет данных" in report_text


def test_empty_run_exits_code_2(tmp_path):
    eval_dir = tmp_path / "eval"
    case_dir = eval_dir / "items" / "case1"
    case_dir.mkdir(parents=True)
    (case_dir / "text.txt").write_text("Какой-то текст", encoding="utf-8")

    run_dir = tmp_path / "run"

    # При фильтре по несуществующему кейсу список items пуст
    with patch("sys.argv", [
        "eval_factcheck_benchmark.py",
        "--eval-dir", str(eval_dir),
        "--run-dir", str(run_dir),
        "--only", "non_existent_case",
        "--no-reference-cases",
        "--report-file", str(tmp_path / "rep.md"),
    ]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
