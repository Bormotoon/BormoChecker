from bormochecker.costs import Meter
from bormochecker.scripts.eval_factcheck_benchmark import (
    ItemResult,
    generate_markdown_report,
)


def test_costs_search_provider_not_yandex_for_free_cascade():
    meter = Meter(provider="local", model="gemma4-26b-mtp", settings={"search": "FreeRuNetSearch", "mode": "education"})
    meter.add_search(1, paid=False, provider="searxng")
    meter.add_search(1, paid=False, provider="ddgs_ru")

    costs = meter.finish()
    # Реально использованные провайдеры должны быть отражены в settings['search']
    assert "ddgs_ru" in costs.settings["search"]
    assert "searxng" in costs.settings["search"]
    assert "Yandex Search API v2" not in costs.settings["search"]


def test_unknown_cost_renders_net_dannyh(tmp_path):
    report_file = tmp_path / "report_unknown_cost.md"
    res = ItemResult(
        item_id="test1",
        split="dev",
        title="Тест неизвестной цены",
        duration_s=10.0,
        status="ok",
        gold_count=2,
        pred_count=2,
        matched_count=2,
        false_confirmations=0,
        false_accusations=0,
        caught_errors=0,
        gold_contradictions=0,
        gold_supported=2,
        pred_confirmed=2,
        pred_errors=0,
        search_queries=1,
        cost_usd=None,  # Неизвестная стоимость
    )

    report_text = generate_markdown_report(
        benchmark_results=[res],
        reference_results=[],
        output_path=report_file,
        git_commit="test_commit",
    )

    assert "нет данных" in report_text
    assert "Финансовые затраты на поиск** | **`нет данных`**" in report_text


def test_zero_cost_distinguished_as_free_cascade(tmp_path):
    report_file = tmp_path / "report_zero_cost.md"
    res = ItemResult(
        item_id="test1",
        split="dev",
        title="Тест нулевой цены",
        duration_s=10.0,
        status="ok",
        gold_count=2,
        pred_count=2,
        matched_count=2,
        false_confirmations=0,
        false_accusations=0,
        caught_errors=0,
        gold_contradictions=0,
        gold_supported=2,
        pred_confirmed=2,
        pred_errors=0,
        search_queries=1,
        cost_usd=0.0,  # Известный нулевой расход
    )

    report_text = generate_markdown_report(
        benchmark_results=[res],
        reference_results=[],
        output_path=report_file,
        git_commit="test_commit",
    )

    assert "бесплатный каскад" in report_text
    assert "$0.00" in report_text
