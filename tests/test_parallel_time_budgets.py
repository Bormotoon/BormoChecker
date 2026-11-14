import concurrent.futures
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bormochecker import pipeline
from bormochecker.contract import CheckMode, Costs, Report


def test_parallel_check_different_budgets_do_not_interfere():
    """T501 / [P06]: Два параллельных вызова с разными бюджетами не влияют друг на друга."""
    def mock_extract(*args, **kwargs):
        return []

    orig_check = pipeline.check

    # Создаем вызовы в 2 потоках с разными бюджетами
    def run_check(budget: float):
        # Проверяем начальное состояние os.environ
        initial_env = os.environ.get("FACTCHECK_TIME_BUDGET_SECONDS")
        report = orig_check(
            "Тестовый текст для проверки бюджета",
            mode=CheckMode.local_frp,
            time_budget_seconds=budget,
        )
        # Убеждаемся, что os.environ не был изменен
        assert os.environ.get("FACTCHECK_TIME_BUDGET_SECONDS") == initial_env
        cost_budget = report.costs.settings.get("time_budget_s")
        return cost_budget

    with patch.object(pipeline.extract, "extract_claims", side_effect=mock_extract):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(run_check, 12.5)
            fut2 = executor.submit(run_check, 45.0)
            b1 = fut1.result()
            b2 = fut2.result()

    assert b1 == 12.5
    assert b2 == 45.0


def test_verify_digest_blocks_does_not_mutate_environ():
    """T501 / [P06]: verify_digest_blocks передает бюджет параметром и не меняет os.environ."""
    clusters = [
        {"draft_text": "Кластер 1 новостей", "title": "Заголовок 1"},
        {"draft_text": "Кластер 2 новостей", "title": "Заголовок 2"},
    ]

    captured_budgets = []

    def fake_check(text, **kwargs):
        captured_budgets.append(kwargs.get("time_budget_seconds"))
        return Report(
            text=text,
            status="ok",
            summary="Проверено",
            costs=Costs(started_at=datetime.now(timezone.utc)),
            claims=[],
        )

    orig_env = os.environ.get("FACTCHECK_TIME_BUDGET_SECONDS")
    with patch.object(pipeline, "check", side_effect=fake_check):
        res = pipeline.verify_digest_blocks(
            clusters,
            time_budget_per_block=25.0,
        )
        assert len(res) == 2
        assert captured_budgets == [25.0, 25.0]

    # os.environ не изменился
    assert os.environ.get("FACTCHECK_TIME_BUDGET_SECONDS") == orig_env


def test_no_environ_assignments_in_prod_code():
    """T501 / [P06]: grep -rn 'os.environ\\[' factcheck/ не содержит присваиваний в продовом коде."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    factcheck_dir = repo_root / "factcheck"

    assignment_re = re.compile(r"os\.environ\[.+\]\s*=")

    violations = []
    for py_file in factcheck_dir.glob("*.py"):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, 1):
            # Пропускаем комментарии
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if assignment_re.search(line):
                violations.append(f"{py_file.name}:{idx}: {line}")

    assert not violations, f"Обнаружены мутации os.environ в продовом коде factcheck/*.py: {violations}"
