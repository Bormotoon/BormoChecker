import json
from datetime import datetime, timezone
from unittest.mock import patch

from bormochecker import bench, check_json
from bormochecker.contract import CheckMode, Costs, Report


def test_bench_main_passes_mode_explicitly(tmp_path):
    dummy_report = Report(
        text="Концерт Dabro 1 октября в 19:00",
        status="ok",
        summary="Все подтверждено",
        costs=Costs(started_at=datetime.now(timezone.utc)),
        claims=[],
    )

    with patch.object(bench, "check", return_value=dummy_report) as mock_check, \
         patch.object(bench, "score", return_value=[]):
        ret = bench.main(
            ["dabro"],
            runs=1,
            output=tmp_path / "runs",
            mode=CheckMode.local_frp,
        )
        assert ret == 0
        assert mock_check.called
        assert mock_check.call_args.kwargs["mode"] == CheckMode.local_frp


def test_bench_cli_accepts_mode(tmp_path):
    dummy_report = Report(
        text="Концерт Dabro 1 октября в 19:00",
        status="ok",
        summary="Все подтверждено",
        costs=Costs(started_at=datetime.now(timezone.utc)),
        claims=[],
    )

    with patch("sys.argv", [
        "bench.py",
        "dabro",
        "--runs", "1",
        "--output", str(tmp_path / "runs"),
        "--mode", "local_frp",
    ]), patch.object(bench, "check", return_value=dummy_report) as mock_check, \
        patch.object(bench, "score", return_value=[]):
        with patch("sys.exit"):
            # Вызов блока __main__ логики
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument('names', nargs='*', choices=tuple(bench.CASES))
            parser.add_argument('--runs', type=int, default=1)
            parser.add_argument('--output', type=bench.Path)
            parser.add_argument(
                '--mode',
                choices=[m.value for m in CheckMode],
                default=CheckMode.education_searxng.value,
            )
            args = parser.parse_args(["dabro", "--mode", "local_frp"])
            bench.main(args.names, runs=args.runs, output=args.output, mode=CheckMode(args.mode))
            assert mock_check.called
            assert mock_check.call_args.kwargs["mode"] == CheckMode.local_frp


def test_check_json_passes_mode_and_cli_override(tmp_path):
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"
    input_file.write_text(
        json.dumps({
            "text": "Тестовая новость",
            "note": "Контекст",
            "mode": CheckMode.education_searxng.value,
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    dummy_report = Report(
        text="Тестовая новость",
        status="ok",
        summary="Подтверждено",
        costs=Costs(started_at=datetime.now(timezone.utc)),
        claims=[],
    )

    # 1. Без CLI --mode берется request.mode
    with patch("sys.argv", [
        "check_json.py",
        str(input_file),
        "--output", str(output_file),
    ]), patch("bormochecker.check_json.check", return_value=dummy_report) as mock_check:
        ret = check_json.main()
        assert ret == 0
        assert mock_check.called
        assert mock_check.call_args.kwargs["mode"] == CheckMode.education_searxng

    # 2. С явным CLI --mode переопределяется
    with patch("sys.argv", [
        "check_json.py",
        str(input_file),
        "--output", str(output_file),
        "--mode", "local_frp",
    ]), patch("bormochecker.check_json.check", return_value=dummy_report) as mock_check:
        ret = check_json.main()
        assert ret == 0
        assert mock_check.called
        assert mock_check.call_args.kwargs["mode"] == CheckMode.local_frp
