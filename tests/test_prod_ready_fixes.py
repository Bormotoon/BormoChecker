"""Targeted unit tests for Phase 1-4 production-readiness fixes."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from bormochecker import derived, evaluate, extract
from bormochecker.contract import Label
from bormochecker.costs import Meter
from bormochecker.numerals import Quantity, is_typo_or_minor_numeric_diff
from bormochecker.retrieval import NetworkNotAllowedError, network_allowed_ctx
from bormochecker.search import FreeRuNetSearch, SearchCache
from bormochecker.sources import Doc


def test_is_typo_or_minor_numeric_diff() -> None:
    assert is_typo_or_minor_numeric_diff(Quantity("COUNT", Decimal("966")), Quantity("COUNT", Decimal("926")))
    assert is_typo_or_minor_numeric_diff(Quantity("COUNT", Decimal("47")), Quantity("COUNT", Decimal("74")))
    assert is_typo_or_minor_numeric_diff(Quantity("DURATION", Decimal("90")), Quantity("DURATION", Decimal("100")))
    assert not is_typo_or_minor_numeric_diff(Quantity("COUNT", Decimal("966")), Quantity("MONEY", Decimal("926")))
    assert not is_typo_or_minor_numeric_diff(Quantity("COUNT", Decimal("100")), Quantity("COUNT", Decimal("250")))
    assert not is_typo_or_minor_numeric_diff(Quantity("COUNT", Decimal("5")), Quantity("COUNT", Decimal("5")))


def test_miet_delta_not_confirmed_without_external_source() -> None:
    text = "Количество бюджетных мест увеличилось с 959 до 966, то есть всего на семь мест, или менее чем на 1%."
    assert derived.check_delta_and_parts_consistency(text) == []
    claims = [{"text": "с 959 до 966", "start": 38, "end": 50, "kind": "web_checkable"}]
    derived.audit_derived_claims(text, claims)
    assert claims[0].get("in_text_arithmetic_consistent") is True
    assert claims[0].get("label") is None


def test_scalar_and_mean_range_deduplicated_in_audit() -> None:
    text = "Средний балл ЕГЭ у первокурсников составил от 60 до 115 баллов."
    claims = [{"text": text, "start": 0, "end": len(text), "kind": "web_checkable"}]
    res = derived.audit_derived_claims(text, claims)
    error_claims = [c for c in res if c.get("label") == Label.error]
    assert len(error_claims) == 1


def test_verify_contradiction_cpu_rejects_non_money_atom_for_money_quote() -> None:
    res_no_price = evaluate.verify_contradiction_cpu(
        "Разовое посещение творческой мастерской по абонементу",
        "Стоимость разового занятия составляет 600 рублей.",
    )
    assert not res_no_price.is_valid
    assert res_no_price.downgraded_label == Label.clarify

    res_with_price = evaluate.verify_contradiction_cpu(
        "Стоимость разового занятия — 450 рублей.",
        "Стоимость разового занятия составляет 600 рублей.",
    )
    assert res_with_price.is_valid
    assert res_with_price.downgraded_label == Label.error


def test_editorial_note_suggestion_on_in_text_arithmetic_consistent() -> None:
    doc = Doc(id="d1", url="https://example.org/miet", title="МИЭТ приём", kind="official", text="В этом году выделено 926 бюджетных мест.")
    mock_raw = {
        "n": 1,
        "comparison_note": "966 в тексте vs 926 в источнике",
        "verdict": "contradicted",
        "exact_source_quote": "В этом году выделено 926 бюджетных мест.",
        "explanation": "Число бюджетных мест отличается.",
        "suggestion": None,
    }
    with patch.object(evaluate, "_ask", return_value=mock_raw):
        v, _, _, sug = evaluate.evaluate_claim(
            "Количество бюджетных мест увеличилось с 959 до 966",
            [doc],
            Meter(),
            [],
            timeout=10.0,
            in_text_arithmetic_consistent=True,
        )
    assert v == "partially_supported"
    assert sug is not None
    assert "926 — прим. редакции" in sug
    assert "966" in sug


def test_absorb_redundant_subspans_keeps_homogeneous_predicates() -> None:
    text = "Коллектив получил три премии, выпустил новый альбом и продолжает записывать песни."
    c1_start = text.index("выпустил новый альбом")
    c1_end = c1_start + len("выпустил новый альбом")
    c2_start = text.index("продолжает записывать песни")
    c2_end = c2_start + len("продолжает записывать песни")
    claims = [
        {"text": "выпустил новый альбом", "quote": "выпустил новый альбом", "start": c1_start, "end": c1_end, "kind": "web_checkable"},
        {"text": "продолжает записывать песни", "quote": "продолжает записывать песни", "start": c2_start, "end": c2_end, "kind": "web_checkable"},
    ]
    res = extract.absorb_redundant_subspans(claims, text)
    texts = [c["text"] for c in res]
    assert "выпустил новый альбом" in texts
    assert "продолжает записывать песни" in texts


def test_free_runet_search_reads_sqlite_cache_in_offline_mode(tmp_path) -> None:
    cache = SearchCache(db_path=tmp_path / "cache.sqlite")
    cache.put_search("МИЭТ приём", "searxng", [{"title": "МИЭТ", "url": "https://miet.ru", "snippet": "926 мест"}])
    searcher = FreeRuNetSearch(cache=cache)
    token = network_allowed_ctx.set(False)
    try:
        hits = searcher.search("МИЭТ приём")
        assert len(hits) == 1
        assert hits[0].url == "https://miet.ru"
        with pytest.raises(NetworkNotAllowedError):
            searcher.search("некэшированный запрос")
    finally:
        network_allowed_ctx.reset(token)
