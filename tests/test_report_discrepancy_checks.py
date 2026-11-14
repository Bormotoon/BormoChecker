import pathlib
from bormochecker.contract import Label
from bormochecker.scripts.eval_factcheck_benchmark import (
    ItemResult,
    evaluate_key_discrepancies,
    generate_markdown_report,
)


def _make_mock_benchmark():
    return [
        ItemResult(
            item_id="m07-avtobusy-data",
            split="dev",
            title="Автобусы",
            duration_s=24.2,
            status="ok",
            gold_count=5,
            pred_count=5,
            matched_count=4,
            false_confirmations=0,
            false_accusations=0,
            caught_errors=0,
            gold_contradictions=1,
            gold_supported=4,
            pred_confirmed=4,
            pred_errors=0,
            search_queries=0,
            cost_usd=0.0,
            unmatched_preds=0,
            is_failed=False,
        )
    ]


def _make_mock_reference_cases(
    dabro_date_label=Label.confirmed,
    dabro_price_label=Label.confirmed,
    miet_47_label=Label.confirmed,
    miet_261_label=Label.confirmed,
    miet_duration_s=45.0,
):
    dabro_rows = [
        (True, "should", "1 октября (это четверг) в 19:00", {dabro_date_label}, "афиша"),
        (True, "must", "Билеты от 2500 до 7000 рублей", {dabro_price_label}, "цена"),
    ]
    miet_rows = [
        (True, "should", "на 47%", {miet_47_label}, "рост заявлений"),
        (True, "should", "261 заявление", {miet_261_label}, "колледж"),
    ]
    return [
        {
            "id": "dabro",
            "title": "Dabro",
            "duration_s": 35.0,
            "status": "ok",
            "rows": dabro_rows,
            "must_passed": 1,
            "must_total": 1,
            "should_passed": 1,
            "should_total": 1,
        },
        {
            "id": "miet",
            "title": "МИЭТ",
            "duration_s": miet_duration_s,
            "status": "ok",
            "rows": miet_rows,
            "must_passed": 0,
            "must_total": 0,
            "should_passed": 2,
            "should_total": 2,
        },
    ]


def test_evaluate_key_discrepancies_all_pass():
    bench = _make_mock_benchmark()
    ref = _make_mock_reference_cases()
    checks = evaluate_key_discrepancies(bench, ref, time_limit_seconds=600.0)

    assert len(checks) == 4
    for c in checks:
        assert c.passed is True, f"Check {c.check_id} failed: {c.actual}"


def test_m07_missing_fails():
    bench = [
        ItemResult(
            item_id="r01-podjezdy",
            split="dev",
            title="Подъезды",
            duration_s=15.0,
            status="ok",
            gold_count=3,
            pred_count=3,
            matched_count=3,
            false_confirmations=0,
            false_accusations=0,
            caught_errors=1,
            gold_contradictions=1,
            gold_supported=2,
            pred_confirmed=2,
            pred_errors=1,
            search_queries=0,
            cost_usd=0.0,
            unmatched_preds=0,
            is_failed=False,
        )
    ]
    ref = _make_mock_reference_cases()
    checks = evaluate_key_discrepancies(bench, ref)
    m07_check = next(c for c in checks if c.check_id == "m07-avtobusy-data")
    assert m07_check.passed is False
    assert "не найден" in m07_check.actual.lower()


def test_dabro_clarify_counts_as_failure():
    bench = _make_mock_benchmark()
    # Задаем clarify вместо confirmed
    ref = _make_mock_reference_cases(dabro_price_label=Label.clarify)
    checks = evaluate_key_discrepancies(bench, ref)
    dabro_check = next(c for c in checks if c.check_id == "dabro_date_price")
    assert dabro_check.passed is False
    assert "незачёт" in dabro_check.actual.lower()


def test_miet_clarify_counts_as_failure():
    bench = _make_mock_benchmark()
    # Задаем clarify вместо confirmed для 47%
    ref = _make_mock_reference_cases(miet_47_label=Label.clarify)
    checks = evaluate_key_discrepancies(bench, ref)
    miet_check = next(c for c in checks if c.check_id == "miet_47_and_261")
    assert miet_check.passed is False
    assert "незачёт" in miet_check.actual.lower()


def test_miet_time_limit_exceeded_fails():
    bench = _make_mock_benchmark()
    # Превышение лимита 600 с (например 625 с)
    ref = _make_mock_reference_cases(miet_duration_s=625.0)
    checks = evaluate_key_discrepancies(bench, ref, time_limit_seconds=600.0)
    dur_check = next(c for c in checks if c.check_id == "miet_duration")
    assert dur_check.passed is False
    assert "625.0 с" in dur_check.actual


def test_generate_markdown_report_displays_discrepancies(tmp_path: pathlib.Path):
    bench = _make_mock_benchmark()
    ref = _make_mock_reference_cases(dabro_price_label=Label.clarify)
    report_file = tmp_path / "test_report.md"

    report_text = generate_markdown_report(
        benchmark_results=bench,
        reference_results=ref,
        output_path=report_file,
        git_commit="test_commit",
    )

    # Раздел 5.1 должен явно отображать все 4 пункта
    assert "Контроль ключевых расхождений отчёта (T411)" in report_text
    assert "Полнота набора: кейс `m07-avtobusy-data`" in report_text
    assert "Кейс Dabro: дата и цена (строго confirmed)" in report_text
    assert "Кейс МИЭТ: 47% и 261 заявление (строго confirmed)" in report_text
    assert "Кейс МИЭТ: соблюдение лимита времени" in report_text

    # Проверка отображения фактического статуса
    assert "✅ ВЫПОЛНЕНО" in report_text
    assert "❌ НЕ ВЫПОЛНЕНО" in report_text
    assert "Ключевые расхождения (T411)" in report_text
