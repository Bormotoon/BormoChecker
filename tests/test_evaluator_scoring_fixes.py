from datetime import datetime, timezone

from bormochecker.contract import Claim, Label, Report
from bormochecker.scripts.eval_factcheck_benchmark import (
    ItemResult,
    generate_markdown_report,
    score_item,
)


def _make_dummy_report(claims: list[Claim], text: str = "Тестовый текст для фактчекера.") -> Report:
    return Report(
        text=text,
        status="ok",
        summary="summary",
        claims=claims,
        limitations=[],
        costs={
            "provider": "local",
            "model": "gemma",
            "started_at": datetime.now(timezone.utc),
        },
    )


def test_partially_supported_and_not_checkable_accounting():
    # 1. Gold: partially_supported
    text = "В 2026 году планируют ремонт."
    item_part = {
        "id": "case_part",
        "split": "dev",
        "gold": {
            "claims": [{"quote": "В 2026 году", "verdict": "partially_supported"}],
        },
    }

    # If system gave confirmed -> false confirmation!
    rep_conf = _make_dummy_report([Claim(start=0, end=11, text="В 2026 году", label=Label.confirmed, explanation="ok")], text)
    res_conf = score_item(item_part, rep_conf, 1.0)
    assert res_conf.false_confirmations == 1
    assert res_conf.false_accusations == 0

    # If system gave error -> false accusation!
    rep_err = _make_dummy_report([Claim(start=0, end=11, text="В 2026 году", label=Label.error, explanation="err")], text)
    res_err = score_item(item_part, rep_err, 1.0)
    assert res_err.false_confirmations == 0
    assert res_err.false_accusations == 1

    # If system gave clarify -> acceptable (no false conf, no false acc)
    rep_cla = _make_dummy_report([Claim(start=0, end=11, text="В 2026 году", label=Label.clarify, explanation="part")], text)
    res_cla = score_item(item_part, rep_cla, 1.0)
    assert res_cla.false_confirmations == 0
    assert res_cla.false_accusations == 0

    # 2. Gold: not_checkable
    item_nc = {
        "id": "case_nc",
        "split": "dev",
        "gold": {
            "claims": [{"quote": "В 2026 году", "verdict": "not_checkable"}],
        },
    }
    # confirmed -> false confirmation
    assert score_item(item_nc, rep_conf, 1.0).false_confirmations == 1
    # error -> false accusation
    assert score_item(item_nc, rep_err, 1.0).false_accusations == 1
    # opinion -> OK
    rep_op = _make_dummy_report([Claim(start=0, end=11, text="В 2026 году", label=Label.opinion, explanation="op")], text)
    res_op = score_item(item_nc, rep_op, 1.0)
    assert res_op.false_confirmations == 0
    assert res_op.false_accusations == 0


def test_overlap_threshold_50_percent():
    # Gold length is 10 chars: "0123456789"
    # Pred length is 10 chars: "......4567" -> intersection length 4 (40%)
    full_text = "0123456789_abcdefghij"
    item = {
        "id": "case_overlap",
        "split": "dev",
        "gold": {
            "claims": [{"quote": "0123456789", "verdict": "supported"}],
        },
    }
    # Intersection "6789" is 4 chars -> 40% of gold (10) and 40% of pred (10)
    rep = _make_dummy_report([Claim(start=6, end=16, text="6789_abcde", label=Label.confirmed, explanation="ok")], full_text)

    # With default threshold 0.50 (50%), it should NOT match
    res_50 = score_item(item, rep, 1.0, overlap_threshold=0.50)
    assert res_50.matched_count == 0
    assert res_50.unmatched_preds == 1

    # With looser threshold 0.35 (35%), it matches
    res_35 = score_item(item, rep, 1.0, overlap_threshold=0.35)
    assert res_35.matched_count == 1
    assert res_35.unmatched_preds == 0


def test_unmatched_predictions_counted_in_precision():
    text = "Первое утверждение. Второе утверждение."
    item = {
        "id": "case_unmatched",
        "split": "dev",
        "gold": {
            "claims": [{"quote": "Первое утверждение.", "verdict": "supported"}],
        },
    }
    # System extracted 2 claims, but gold only has 1
    claims = [
        Claim(start=0, end=19, text="Первое утверждение.", label=Label.confirmed, explanation="ok"),
        Claim(start=20, end=39, text="Второе утверждение.", label=Label.clarify, explanation="extra"),
    ]
    rep = _make_dummy_report(claims, text)
    res = score_item(item, rep, 1.0)

    assert res.pred_count == 2
    assert res.matched_count == 1
    assert res.unmatched_preds == 1


def test_pred_claims_range_violation_in_report(tmp_path):
    report_file = tmp_path / "report_range.md"

    # Создаем фиктивный ItemResult с 184 извлеченными утверждениями (> 160)
    res_184 = ItemResult(
        item_id="r01",
        split="dev",
        title="Случай 184 утверждения",
        duration_s=20.0,
        status="ok",
        gold_count=146,
        pred_count=184,  # Вне диапазона [120, 160]
        matched_count=140,
        false_confirmations=0,
        false_accusations=0,
        caught_errors=10,
        gold_contradictions=10,
        gold_supported=100,
        pred_confirmed=110,
        pred_errors=10,
        search_queries=5,
        cost_usd=0.0,
        unmatched_preds=44,
    )

    report_text = generate_markdown_report(
        benchmark_results=[res_184],
        reference_results=[],
        output_path=report_file,
        git_commit="test_commit",
    )

    assert "Нарушение диапазона (184 > 160)" in report_text
    assert "Не сопоставленные предсказания (Unmatched)" in report_text
