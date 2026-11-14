"""Тесты для закрытия пробелов покрытия (T604 / astra §9.5)."""

from datetime import datetime, timezone
import json
import logging
from unittest.mock import MagicMock, patch
import pytest

from bormochecker import (
    bench,
    derived,
    direct_apis,
    extract,
    llm,
    log_masking,
    numerals,
    origin,
    retrieval,
    search,
    sources,
    spans,
    textnorm,
)
from bormochecker.contract import Claim, Costs, Label, Report


# ==========================================
# 1. origin.py
# ==========================================

def test_origin_from_text_and_note() -> None:
    assert origin.from_text("в ответе на наш запрос сообщили данные") == "stated"
    assert origin.from_text("судя по видео, инцидент произошёл утром") == "unclear"
    assert origin.from_text("сообщает пресс-служба министерства") is None
    assert origin.from_text("Заголовок новости\n") is None

    assert origin.from_note("Материал редакции, видео получено от очевидцев") == "stated"
    assert origin.from_note("Обычная новость") is None


def test_origin_is_heading_without_claim() -> None:
    assert origin.is_heading_without_claim("Короткий заголовок") is False
    assert origin.is_heading_without_claim("Заголовок с точкой.") is False
    assert origin.is_heading_without_claim("Очень длинный текст " * 10) is False
    assert origin.is_heading_without_claim("Строка 1\nСтрока 2") is False

    full = "Главная тема\n\nТекст новости начинается здесь."
    assert origin.is_heading_without_claim("Главная тема", full_text=full, span=(0, 12)) is True


def test_origin_propagate_and_labels() -> None:
    class Frag:
        def __init__(self, quote, start, end, origin=None):
            self.quote = quote
            self.start = start
            self.end = end
            self.origin = origin

    text = "На видео сняли лося. Лось пересёк проезжую часть и скрылся в лесу."
    f1 = Frag("На видео сняли лося", 0, 19)
    f2 = Frag("Лось пересёк проезжую часть", 21, 48)

    origin.propagate([f1, f2], text, note="видео редакции")
    assert f1.origin == "stated"
    assert f2.origin == "stated"


    assert origin.label_for(None, "editorial", "stated") == Label.editorial
    assert origin.label_for(Label.confirmed, "factual", "stated") == Label.editorial
    assert origin.label_for(Label.opinion, "opinion", "stated") == Label.opinion
    assert origin.label_for(Label.error, "factual", "stated") == Label.error

    exp = origin.explanation_for(Label.error, "factual", "stated")
    assert "Сведение из материала редакции" in exp
    assert origin.explanation_for(Label.opinion, "opinion", None) != ""
    assert origin.explanation_for(Label.confirmed, "not_checkable", None) != ""


# ==========================================
# 2. spans.py
# ==========================================

def test_spans_find_span_and_snap() -> None:
    text = "Программа включает 120 часов занятий."
    quote = "120 часов"
    found = spans.find_span(text, quote)
    assert found == (19, 28)

    # Токенизированный поиск при типографических различиях
    text_fold = '«Программа» — включает 120 часов.'
    quote_fold = '"Программа" - включает'
    found_fold = spans.find_span(text_fold, quote_fold)
    assert found_fold is not None

    assert spans.find_span(text, "") is None
    assert spans.snap(text, "   120 часов   ") == (19, 28, "120 часов")


def test_spans_resolve_overlaps() -> None:
    text = "Большой текст с несколькими пересекающимися утверждениями."
    c1 = Claim(start=0, end=13, text="Большой текст", label=Label.clarify, explanation="в найденных источниках нет подходящего фрагмента")
    c2 = Claim(start=8, end=27, text="текст с несколькими", label=Label.error, explanation="Фактическая ошибка")

    merged = spans.resolve_span_overlaps([c1, c2], text)
    assert len(merged) == 1
    assert merged[0].label == Label.error


# ==========================================
# 3. derived.py
# ==========================================

def test_derived_evaluate_operations() -> None:
    # 1. Percentage
    req_pct = {
        "operation": "percentage",
        "operands": [
            {"value": "50", "unit": "руб", "scope": {"period": "2026", "metric": "выплаты"}},
            {"value": "200", "unit": "руб", "scope": {"period": "2026", "metric": "выплаты"}},
        ],
        "claimed_result": "25",
        "tolerance": "0.5",
    }
    res_pct = derived.evaluate(req_pct, claim_text="50 из 200 это 25%", quotes=["в 2026 выплаты составили 50 и 200"])
    assert res_pct.status == "matches"
    assert res_pct.same_scope_verified is True

    # 2. Mean
    req_mean = {
        "operation": "mean",
        "operands": [
            {"value": "10", "unit": "кг", "scope": {"metric": "вес"}},
            {"value": "20", "unit": "кг", "scope": {"metric": "вес"}},
        ],
        "claimed_result": "15",
    }
    res_mean = derived.evaluate(req_mean, claim_text="среднее 15", quotes=["вес 10 и 20"])
    assert res_mean.status == "matches"

    # 3. Ratio
    req_ratio = {
        "operation": "ratio",
        "operands": [
            {"value": "100", "unit": "чел", "scope": {"metric": "люди"}},
            {"value": "50", "unit": "чел", "scope": {"metric": "люди"}},
        ],
        "result_unit": "раза",
        "claimed_result": "2",
    }
    res_ratio = derived.evaluate(req_ratio, claim_text="в 2 раза", quotes=["люди 100 и 50"])
    assert res_ratio.status == "matches"

    # 4. Compare
    req_cmp = {
        "operation": "compare",
        "operands": [
            {"value": "30", "unit": "баллов", "scope": {"metric": "баллы"}},
            {"value": "20", "unit": "баллов", "scope": {"metric": "баллы"}},
        ],
        "claimed_result": "1",
    }
    res_cmp = derived.evaluate(req_cmp, claim_text="выше", quotes=["баллы 30 и 20"])
    assert res_cmp.status == "matches"

    # 5. Ошибочные случаи (деление на ноль, несовпадение области)
    req_zero = {
        "operation": "percentage",
        "operands": [
            {"value": "10", "unit": "р", "scope": {"metric": "m"}},
            {"value": "0", "unit": "р", "scope": {"metric": "m"}},
        ],
        "claimed_result": "10",
    }
    assert derived.evaluate(req_zero, claim_text="10 и 0", quotes=["10 и 0"]).status == "insufficient_data"

    req_scope_diff = {
        "operation": "mean",
        "operands": [
            {"value": "10", "unit": "р", "scope": {"metric": "m1"}},
            {"value": "20", "unit": "р", "scope": {"metric": "m2"}},
        ],
        "claimed_result": "15",
    }
    assert derived.evaluate(req_scope_diff, claim_text="15", quotes=["10 и 20"]).status == "insufficient_data"


# ==========================================
# 4. llm.py
# ==========================================

def test_llm_parse_json_fallbacks() -> None:
    # 1. Thought block
    raw_thought = "<thought>Thinking process...</thought>\n\n```json\n{\"key\": \"value\"}\n```"
    assert llm.parse_llm_json(raw_thought) == {"key": "value"}

    # 2. Markdown block without json tag
    raw_md = "Here is the response:\n```\n{\"number\": 42}\n```"
    assert llm.parse_llm_json(raw_md) == {"number": 42}

    # 3. Raw json with prefix text
    raw_prefix = "Ответ: [1, 2, 3] дополнительный текст"
    assert llm.parse_llm_json(raw_prefix) == [1, 2, 3]

    # 4. Invalid json raises ValueError
    with pytest.raises(ValueError):
        llm.parse_llm_json("Просто текст без скобок")


# ==========================================
# 5. search.py & direct_apis.py
# ==========================================

def test_search_clean_query_and_blocking() -> None:
    q = search.clean_query_for_free_search("новость (site:a.ru | site:b.ru) про школу")
    assert "site:" not in q
    assert "новость про школу" in q

    assert direct_apis.is_blocked_domain("http://notblocked.ru/test") is False
    assert direct_apis.is_blocked_domain("http://xn--e1afmkfd.xn--p1ai/page") is True
    assert direct_apis.is_blocked_domain("http://sub.internal.example") is True


# ==========================================
# 6. YandexSearch & SearXNGSearch parse_response
# ==========================================

def test_yandex_search_parse_response() -> None:
    xml_data = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<yandexsearch version="1.0"><response date="20261111T120000">'
        '<results><grouping><group><doc id="1">'
        '<url>https://example.com/doc1</url>'
        '<title>Тестовый заголовок</title>'
        '<headline>Сниппет документа</headline>'
        '</doc></group></grouping></results></response></yandexsearch>'
    ).encode("utf-8")
    import base64
    payload = {"rawData": base64.b64encode(xml_data).decode("ascii")}
    res = search.YandexSearch.parse_response(payload, limit=2)
    assert len(res) == 1
    assert res[0].url == "https://example.com/doc1"
    assert res[0].title == "Тестовый заголовок"
    assert res[0].snippet == "Сниппет документа"


def test_searxng_search_parse_response() -> None:
    payload = {
        "results": [
            {
                "url": "https://school.ru/doc",
                "title": "Школьный документ",
                "content": "Содержимое страницы",
                "publishedDate": "2026-09-01",
            }
        ]
    }
    res = search.SearXNGSearch.parse_response(payload, limit=5)
    assert len(res) == 1
    assert res[0].url == "https://school.ru/doc"
    assert res[0].title == "Школьный документ"
    assert res[0].snippet == "Содержимое страницы"


# ==========================================
# 7. direct_apis applicability and cascade
# ==========================================

def test_direct_apis_applicability_and_cascade() -> None:
    pravo = direct_apis.PravoGovAPI()
    assert pravo.is_applicable("приказ Минпросвещения № 456 от 2026 года") is True
    assert pravo.is_applicable("простая новость без законов") is False

    cyber = direct_apis.CyberLeninkaAPI()
    assert cyber.is_applicable("исследование когнитивного развития школьников") is True

    mos = direct_apis.MosRuSuggestAPI()
    assert mos.is_applicable("новости mos.ru для школьников Москвы") is True

    fgos = direct_apis.FgosReestrAPI()
    assert fgos.is_applicable("ФГОС основного общего образования") is True

    cascade = direct_apis.DirectAPIs()
    # При выключенных env (кроме вики) DirectAPIs возвращает документы
    assert isinstance(cascade.pravo, direct_apis.PravoGovAPI)


# ==========================================
# 8. derived checks: curriculum, orders, average
# ==========================================

def test_derived_curriculum_and_orders() -> None:
    # Сумма часов учебного плана
    curr_text = "В плане заложено 100 часов по физике и 120 часов по химии, всего 250 часов."
    curr_res = derived.check_curriculum_hours_sum(curr_text)
    assert len(curr_res) >= 1
    assert curr_res[0]["verdict"] == "refuted"
    assert "220" in curr_res[0]["explanation"]

    # Нормативные приказы
    order_text = "Приказ Минпросвещения России № 1025 от 2026 года."
    order_res = derived.check_known_education_orders(order_text)
    assert isinstance(order_res, list)

    # Средние и доли
    avg_text = "Средняя зарплата учителей выросла на 15%."
    avg_res = derived.check_average_ratio_arithmetic(avg_text)
    assert isinstance(avg_res, list)


# ==========================================
# 9. derived: evaluate edge cases & arithmetic checks
# ==========================================

def test_derived_evaluate_edge_cases() -> None:
    # Неверный тип запроса
    assert derived.evaluate(None, claim_text="100", quotes=[]).status == "insufficient_data"
    assert derived.evaluate({"operation": "unknown"}, claim_text="100", quotes=[]).status == "insufficient_data"
    assert derived.evaluate({"operation": "mean", "operands": ["not-a-dict"]}, claim_text="100", quotes=[]).status == "insufficient_data"
    assert derived.evaluate({"operation": "mean", "operands": [{"value": "abc"}]}, claim_text="100", quotes=[]).status == "insufficient_data"
    
    # Отрицательный допуск
    bad_tol = {
        "operation": "mean",
        "operands": [{"value": "10", "unit": "руб"}, {"value": "20", "unit": "руб"}],
        "claimed_result": "15",
        "tolerance": "-1"
    }
    assert derived.evaluate(bad_tol, claim_text="15 руб", quotes=["10 руб", "20 руб"]).status == "insufficient_data"

    # Несовместимые единицы
    diff_units = {
        "operation": "percentage",
        "operands": [{"value": "10", "unit": "руб"}, {"value": "100", "unit": "кг"}],
        "claimed_result": "10"
    }
    assert derived.evaluate(diff_units, claim_text="10%", quotes=["10 руб", "100 кг"]).status == "insufficient_data"

    # Отсутствует единица результата для ratio
    no_res_unit = {
        "operation": "ratio",
        "operands": [{"value": "100", "unit": "руб"}, {"value": "50", "unit": "чел"}],
        "claimed_result": "2"
    }
    assert derived.evaluate(no_res_unit, claim_text="2", quotes=["100 руб", "50 чел"]).status == "insufficient_data"

    # Числа не найдены в цитатах
    not_found = {
        "operation": "percentage",
        "operands": [{"value": "999", "unit": "чел"}, {"value": "1000", "unit": "чел"}],
        "claimed_result": "99.9"
    }
    assert derived.evaluate(not_found, claim_text="99.9 чел", quotes=["текст без чисел"]).status == "insufficient_data"


def test_derived_percentage_average_and_delta() -> None:
    # check_percentage_arithmetic (pattern 2)
    # Ошибка в проценте: заявлено "более 50% (10 из 100)" -> 10% < 50%
    text_pct_err = "Опрос показал: более 50% (10 из 100) учителей поддержали идею."
    res_pct_err = derived.check_percentage_arithmetic(text_pct_err)
    assert any(r["verdict"] == "refuted" for r in res_pct_err)

    # Верный процент: "менее 15% (10 из 100)" -> 10% <= 15%
    text_pct_ok = "Опрос показал: менее 15% (10 из 100) учителей поддержали идею."
    res_pct_ok = derived.check_percentage_arithmetic(text_pct_ok)
    assert any(r["verdict"] == "confirmed" for r in res_pct_ok)

    # check_average_ratio_arithmetic: 10 млн руб на 5 школ -> в среднем 2 млн руб (верно)
    text_avg_ok = "Выделили 10 млн рублей на 5 школ, в среднем 2 млн рублей на учреждение."
    res_avg_ok = derived.check_average_ratio_arithmetic(text_avg_ok)
    assert any(r["verdict"] == "confirmed" for r in res_avg_ok)

    # 10 млн руб на 5 школ, в среднем 4 млн рублей (ошибка)
    text_avg_err = "Выделили 10 млн рублей на 5 школ, в среднем 4 млн рублей на учреждение."
    res_avg_err = derived.check_average_ratio_arithmetic(text_avg_err)
    assert any(r["verdict"] == "refuted" for r in res_avg_err)

    # check_delta_and_parts_consistency
    # Прирост: с 10 до 25 на 15 мест (арифметика верна -> не ставит confirmed без внешнего источника)
    text_delta_ok = "Число выросло с 10 до 25 на 15 мест."
    res_delta_ok = derived.check_delta_and_parts_consistency(text_delta_ok)
    assert res_delta_ok == []
    claims_delta = [{"text": text_delta_ok, "start": 0, "end": len(text_delta_ok), "kind": "web_checkable"}]
    derived.audit_derived_claims(text_delta_ok, claims_delta)
    assert claims_delta[0].get("in_text_arithmetic_consistent") is True
    assert claims_delta[0].get("label") is None

    # Прирост: с 10 до 25 на 20 мест (ошибка)
    text_delta_err = "Число выросло с 10 до 25 на 20 мест."
    res_delta_err = derived.check_delta_and_parts_consistency(text_delta_err)
    assert any(r["verdict"] == "refuted" for r in res_delta_err)

    # Слагаемые: всего 100 из них 60 и 40 (арифметика верна -> без confirmed)
    text_parts_ok = "Всего 100 участников, из них 60 школьников и 40 студентов."
    res_parts_ok = derived.check_delta_and_parts_consistency(text_parts_ok)
    assert res_parts_ok == []

    # Слагаемые: всего 100 из них 60 и 30 (ошибка)
    text_parts_err = "Всего 100 участников, из них 60 школьников и 30 студентов."
    res_parts_err = derived.check_delta_and_parts_consistency(text_parts_err)
    assert any(r["verdict"] == "refuted" for r in res_parts_err)


def test_derived_apply_findings_splitting() -> None:
    # Проверка сужения ошибочного атома и сохранения посылки
    text = "По данным опроса, более 50% (10 из 100) учителей не согласны с реформой."
    claims = [{
        "text": text,
        "start": 0,
        "end": len(text),
        "kind": "web_checkable",
        "type": "numeric",
        "importance": "high",
        "entity_cluster": "test",
    }]
    updated = derived.audit_derived_claims(text, claims)
    assert len(updated) >= 2
    # Один из атомов стал ошибкой арифметики, другой — исходной посылкой
    labels = [c.get("label") for c in updated]
    assert Label.error in labels


# ==========================================
# 10. search: Yandex, SearXNG, CloudFreeRotator mock coverage
# ==========================================

def test_search_yandex_and_searxng_mock() -> None:
    # YandexSearch validation
    with pytest.raises(ValueError):
        search.YandexSearch(api_key="")

    with pytest.raises(ValueError):
        search.SearXNGSearch(base_url="")

    # Mock Yandex search HTTP call
    yandex = search.YandexSearch(api_key="fake-key", timeout_seconds=2.0)
    xml_data = b"<response><results><grouping><group><doc><url>https://example.com/edu</url><title>Test Title</title><headline>Test Snippet</headline></doc></group></grouping></results></response>"
    import base64
    b64_raw = base64.b64encode(xml_data).decode("ascii")
    fake_json = json.dumps({"rawData": b64_raw}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_json
    mock_resp.__enter__.return_value = mock_resp

    with patch("bormochecker.search.urlopen", return_value=mock_resp):
        res = yandex.search("образование 2026", limit=2)
        assert len(res) == 1
        assert res[0].url == "https://example.com/edu"

    # Mock SearXNG search HTTP call
    searxng = search.SearXNGSearch(base_url="https://searx.example.com", timeout_seconds=2.0)
    fake_searx_json = json.dumps({
        "results": [{"url": "https://school.example.com", "title": "School", "content": "Info"}]
    }).encode("utf-8")
    mock_searx_resp = MagicMock()
    mock_searx_resp.read.return_value = fake_searx_json
    mock_searx_resp.__enter__.return_value = mock_searx_resp

    with patch("bormochecker.search.urlopen", return_value=mock_searx_resp):
        s_res = searxng.search("школа", limit=2)
        assert len(s_res) == 1
        assert s_res[0].url == "https://school.example.com"


# ==========================================
# 11. extract: _span variations, one_origin, and textnorm
# ==========================================

def test_extract_span_variations() -> None:
    text = "Первый абзац текста. Второй абзац с длинной цитатой про школу. Конец текста."
    
    # 1. offset > 0 fallback to 0
    span1 = extract._span(text, "Первый", offset=20)
    assert span1 == (0, 6)

    # 2. whitespace variations
    span2 = extract._span(text, "Второй   абзац  с   длинной")
    assert span2 is not None

    # 3. ellipsis matching
    span3 = extract._span(text, "Второй абзац...про школу")
    assert span3 is not None
    assert text[span3[0]:span3[1]].startswith("Второй")
    assert text[span3[0]:span3[1]].endswith("про школу")

    # 4. empty or not found
    assert extract._span("", "цитата") is None
    assert extract._span(text, "") is None
    assert extract._span(text, "несуществующий кусок") is None


def test_extract_one_origin_named_source() -> None:
    note = "Видео и рассказ корреспондента редакции"
    claims = [
        {"text": "Наш корреспондент заметил очередь у входа.", "kind": "editorial"},
        {"text": "По данным департамента, это важно.", "kind": "editorial"},
        {"text": "Текст автора без ключевых слов.", "kind": "editorial"},
    ]
    res = extract.one_origin(claims, note)
    assert res[0]["kind"] == "editorial"
    assert res[0]["origin"] == "stated"
    assert res[1]["kind"] == "opinion"
    assert res[2]["origin"] == "stated"


def test_textnorm_with_morph_mock() -> None:
    mock_morph = MagicMock()
    mock_parse = MagicMock()
    mock_parse.normal_form = "учитель"
    mock_morph.parse.return_value = [mock_parse]

    with patch("bormochecker.textnorm._MORPH", mock_morph):
        assert textnorm.normalize_token("учителями") == "учитель"


# ==========================================
# 12. log_masking, numerals, bench & sources
# ==========================================

def test_log_masking_filter_and_installer() -> None:
    logger = logging.getLogger("test_masking_logger")
    log_masking.install_secret_masker(logger)
    # Повторная установка не должна дублировать
    log_masking.install_secret_masker(logger)

    filt = log_masking.SecretMaskingFilter()
    rec = logging.LogRecord("test", logging.INFO, "path", 1, "Credentials: %s", ("password=secret123",), None)
    assert filt.filter(rec) is True
    assert "secret123" not in rec.msg
    assert "password=***" in rec.msg


def test_numerals_with_morph_mock() -> None:
    mock_morph = MagicMock()
    mock_parse = MagicMock()
    mock_parse.normal_form = "первый"
    mock_morph.parse.return_value = [mock_parse]

    with patch("bormochecker.numerals._MORPH", mock_morph):
        parsed = numerals.parse_number("первые")
        assert parsed is not None
        res, is_ord = parsed
        assert res == 1
        assert is_ord is True


def test_bench_score_and_validation() -> None:
    rep = Report(
        status="ok",
        summary="Проверено успешно",
        text="В 2026 году открыта новая школа.",
        claims=[
            Claim(
                text="открыта новая школа",
                start=12,
                end=31,
                label=Label.confirmed,
                kind="factual",
                importance="high",
                type="event",
                entity_cluster="edu",
                explanation="Подтверждено",
            )
        ],
        costs=Costs(started_at=datetime.now(timezone.utc)),
    )
    cases = [
        ("открыта новая школа", "must", {Label.confirmed}, "проверка школы"),
    ]
    rows = bench.score(rep, cases)
    assert len(rows) == 1
    assert rows[0][0] is True

    import pytest
    with pytest.raises(ValueError, match="runs must be positive"):
        bench.main([], runs=0)


def test_sources_queries_and_search_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. build_queries с якорями МИЭТ, Минпросвещения, Зеленоград
    q_miet = sources.build_queries("Студенты МИЭТ победили на олимпиаде")
    assert any("МИЭТ" in q for q in q_miet)

    q_school = sources.build_queries("В школе введены новые правила для учителей")
    assert any("Минпросвещения" in q for q in q_school)

    q_zel = sources.build_queries("В ДК Зеленограда пройдет концерт")
    assert any("Зеленоград" in q for q in q_zel)

    # 2. web_search при выключенном поиске возвращает пустой список
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("YANDEX_SEARCH_API_KEY", raising=False)
    assert sources.web_search("запрос", allow_paid=False) == []


def test_sources_find_for_claim_branches() -> None:
    src = sources.Sources(meter=MagicMock(), network_allowed=False)
    
    # 1. mode="local_frp"
    docs_frp = src.gather_for("Любой факт", mode="local_frp")
    assert isinstance(docs_frp, list)

    # 2. _is_high_confidence_pool_match
    src._docs["http://local.doc"] = sources.Doc(
        id="d1",
        url="http://local.doc",
        title="Школьные правила",
        text="Полный текст правил школы с цитатой",
    )
    # mock _is_high_confidence_pool_match returning True
    with patch.object(src, "_is_high_confidence_pool_match", return_value=True):
        res = src.gather_for("Школьные правила")
        assert isinstance(res, list)

    # 3. Hop 1 and Hop 2 mock search
    src.network_allowed = True
    mock_engine = MagicMock()
    mock_engine.search.return_value = [
        search.SearchResult(title="Res 1", url="http://example.com/res1", snippet="Snip 1", rank=1)
    ]
    mock_engine.last_was_paid = False
    mock_engine.last_provider = "sqlite_cache"
    mock_engine.last_outcome = "ok"
    src.search_engine = mock_engine

    with patch.object(src, "_load", return_value=sources.Doc(id="r1", url="http://example.com/res1", text="Текст статьи")):
        with patch.object(src, "_is_high_confidence_pool_match", return_value=False):
            found = src.gather_for("Тестовое утверждение для multi hop поиска", limit=2)
            assert len(found) >= 1


# ==========================================
# 13. spans and sources deeper coverage
# ==========================================

def test_spans_overlap_substantive_issue_and_pydantic() -> None:
    text = "01234567890123456789"
    # 1. Pydantic Claim confirmed overlapping error
    c1 = Claim(
        text=text[0:15],
        start=0,
        end=15,
        label=Label.confirmed,
        kind="factual",
        importance="high",
        type="event",
        entity_cluster="edu",
        explanation="Посылка верна",
    )
    c2 = Claim(
        text=text[10:20],
        start=10,
        end=20,
        label=Label.error,
        kind="factual",
        importance="high",
        type="numeric",
        entity_cluster="edu",
        explanation="Ошибка расчёта",
    )
    resolved = spans.resolve_span_overlaps([c1, c2], text)
    assert len(resolved) == 2
    assert resolved[0].end == 10
    assert resolved[1].start == 10

    # 2. Editorial vs Clarify with and without substantive issue
    c_edit = Claim(
        text=text[0:10],
        start=0,
        end=10,
        label=Label.editorial,
        kind="editorial",
        origin="stated",
        importance="low",
        type="opinion",
        entity_cluster="edu",
        explanation="Редакторский текст",
    )
    c_clarify_nosub = Claim(
        text=text[5:15],
        start=5,
        end=15,
        label=Label.clarify,
        kind="editorial",
        origin="stated",
        importance="low",
        type="opinion",
        entity_cluster="edu",
        explanation="не проверялось в этом запуске",
    )
    res_nosub = spans.resolve_span_overlaps([c_edit, c_clarify_nosub], text)
    assert len(res_nosub) == 1
    assert res_nosub[0].label == Label.editorial

    c_clarify_sub = Claim(
        text=text[5:15],
        start=5,
        end=15,
        label=Label.clarify,
        kind="factual",
        importance="high",
        type="event",
        entity_cluster="edu",
        explanation="Существенное противоречие в дате",
    )
    res_sub = spans.resolve_span_overlaps([c_edit, c_clarify_sub], text)
    assert len(res_sub) == 1
    assert res_sub[0].label == Label.clarify


def test_sources_preload_local_docs_and_pool() -> None:
    src = sources.Sources(meter=MagicMock())
    # 1. Предзагрузка словаря
    dict_att = {
        "text": "Текст методических указаний ФРП",
        "title": "Методичка 2026",
        "storage_path": "local/path/doc.pdf",
    }
    src.preload_local_docs([dict_att])
    assert len(src.linked) == 1
    assert src.linked[0].title == "Методичка 2026"
    assert src.linked[0].kind == "user_attachment"

    # 2. Предзагрузка объекта
    class CustomAtt:
        extracted_text = "Текст приказа"
        file_name = "Приказ.pdf"
        storage_path = "storage/order.pdf"

    src.preload_local_docs([CustomAtt()])
    assert len(src.linked) == 2

    # 3. pool()
    p = src.pool()
    assert len(p) == 2


def test_search_cloud_free_rotator() -> None:
    mock_cache = MagicMock()
    mock_cache.is_within_monthly_limit.return_value = True
    rotator = search.CloudFreeRotator(cache=mock_cache)
    assert rotator is not None


# ==========================================
# 11. direct_apis search methods with mocks
# ==========================================

def test_direct_apis_search_methods_mocked() -> None:
    # 1. PravoGovAPI
    pravo = direct_apis.PravoGovAPI()
    mock_pravo_resp = MagicMock()
    mock_pravo_resp.read.return_value = json.dumps({
        "items": [{"title": "Приказ 123", "url": "http://pravo.gov.ru/123", "date": "2026-01-01"}]
    }).encode("utf-8")
    mock_pravo_resp.__enter__.return_value = mock_pravo_resp

    with patch("urllib.request.urlopen", return_value=mock_pravo_resp):
        docs = pravo.search_acts("приказ", limit=2)
        assert isinstance(docs, list)

    # 2. CyberLeninkaAPI
    cyber = direct_apis.CyberLeninkaAPI()
    mock_cyber_resp = MagicMock()
    mock_cyber_resp.read.return_value = json.dumps({
        "articles": [{"name": "Научная статья", "link": "/article/1", "annotation": "Аннотация"}]
    }).encode("utf-8")
    mock_cyber_resp.__enter__.return_value = mock_cyber_resp

    with patch("urllib.request.urlopen", return_value=mock_cyber_resp):
        c_docs = cyber.search_articles("когнитивное развитие", limit=2)
        assert isinstance(c_docs, list)

    # 3. MosRuSuggestAPI
    mos = direct_apis.MosRuSuggestAPI()
    mock_mos_resp = MagicMock()
    mock_mos_resp.read.return_value = json.dumps([
        {"title": "Новость Москвы", "url": "https://mos.ru/news/1"}
    ]).encode("utf-8")
    mock_mos_resp.__enter__.return_value = mock_mos_resp

    with patch("urllib.request.urlopen", return_value=mock_mos_resp):
        m_docs = mos.search_mos_ru("московское образование", limit=2)
        assert isinstance(m_docs, list)

    # 4. FgosReestrAPI
    fgos = direct_apis.FgosReestrAPI()
    mock_fgos_resp = MagicMock()
    mock_fgos_resp.read.return_value = json.dumps({
        "rows": [{"title": "ФГОС ООО", "id": 10}]
    }).encode("utf-8")
    mock_fgos_resp.__enter__.return_value = mock_fgos_resp

    with patch("urllib.request.urlopen", return_value=mock_fgos_resp):
        f_docs = fgos.search_fgos("ФГОС", limit=2)
        assert isinstance(f_docs, list)


# ==========================================
# 14. retrieval: PDF, HTML, SafeDocumentFetcher
# ==========================================

def test_retrieval_pdf_parse_bytes() -> None:
    raw_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nstream\n(Sample Text from PDF Document) Tj\nendstream\nendobj\ntrailer\n<<>>\n%%EOF"
    title, text = retrieval._parse_pdf_bytes(raw_pdf)
    assert "Sample Text" in text or "PDF" in title


def test_retrieval_safe_document_fetcher_mocked() -> None:
    fetcher = retrieval.SafeDocumentFetcher()
    
    # 1. HTML fetch
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "http://example.com/page"
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.content = b"<html><head><title>Page Title</title></head><body><p>Some clean body text for testing document extraction.</p></body></html>"
    mock_resp.encoding = "utf-8"
    mock_client.get.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        doc = fetcher.fetch("http://example.com/page")
        assert doc.title == "Page Title"
        assert "clean body text" in doc.text

        # 2. PDF fetch
        mock_pdf_resp = MagicMock()
        mock_pdf_resp.status_code = 200
        mock_pdf_resp.url = "http://example.com/doc.pdf"
        mock_pdf_resp.headers = {"content-type": "application/pdf"}
        mock_pdf_resp.content = b"%PDF-1.4 (Document Title) Tj"
        mock_client.get.return_value = mock_pdf_resp

        pdf_doc = fetcher.fetch("http://example.com/doc.pdf")
        assert pdf_doc.status_code == 200

        # 3. HTTP 404 error
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.url = "http://example.com/missing"
        mock_client.get.return_value = mock_404

        import pytest
        with pytest.raises(retrieval.FetchError):
            fetcher.fetch("http://example.com/missing")

        # 4. Unsupported content-type
        mock_img = MagicMock()
        mock_img.status_code = 200
        mock_img.url = "http://example.com/pic.png"
        mock_img.headers = {"content-type": "image/png"}
        mock_client.get.return_value = mock_img

        with pytest.raises(retrieval.FetchError):
            fetcher.fetch("http://example.com/pic.png")


# ==========================================
# 15. search: DDGSSearch and CloudFreeRotator providers
# ==========================================

def test_search_ddgs_and_rotator_providers() -> None:
    token = retrieval.network_allowed_ctx.set(True)
    try:
        ddgs = search.DDGSSearch()
        # 1. Fails >= 2
        ddgs._fails = 2
        assert ddgs.search("школа") == []
        ddgs._fails = 0

        # 2. Empty clean query
        assert ddgs.search("   ") == []

        # 3. HTML fallback
        ddg_html = """
        <html><body>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fschool.example.com%2Fnews">Школьные новости</a>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.read.return_value = ddg_html.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("sys.modules", {"ddgs": None}), patch("urllib.request.urlopen", return_value=mock_resp):
            res = ddgs.search("образование 2026", limit=2)
            assert len(res) >= 1
            assert res[0].url == "https://school.example.com/news"

        # 4. CloudFreeRotator providers
        mock_cache = MagicMock()
        mock_cache.try_consume_quota.return_value = True
        rotator = search.CloudFreeRotator(cache=mock_cache)
        rotator.parallel_key = "test-parallel"
        rotator.tavily_key = "test-tavily"
        rotator.serper_key = "test-serper"
        rotator.firecrawl_key = "test-firecrawl"
        rotator.serpapi_key = "test-serpapi"

        # Parallel
        mock_par_json = json.dumps({
            "results": [{"url": "https://parallel.example.com", "title": "Parallel Title", "excerpts": ["Snippet"]}]
        }).encode("utf-8")
        mock_par_resp = MagicMock()
        mock_par_resp.read.return_value = mock_par_json
        mock_par_resp.__enter__.return_value = mock_par_resp

        with patch("urllib.request.urlopen", return_value=mock_par_resp):
            p_res = rotator._search_parallel("тест", limit=2)
            assert len(p_res) == 1
            assert p_res[0].url == "https://parallel.example.com"
            prov, hits = rotator.search("тест", limit=2)
            assert prov == "parallel_free"
            assert len(hits) == 1

        # Tavily
        mock_tav_json = json.dumps({
            "results": [{"url": "https://tavily.example.com", "title": "Tavily Title", "content": "Content"}]
        }).encode("utf-8")
        mock_tav_resp = MagicMock()
        mock_tav_resp.read.return_value = mock_tav_json
        mock_tav_resp.__enter__.return_value = mock_tav_resp

        with patch("urllib.request.urlopen", return_value=mock_tav_resp):
            t_res = rotator._search_tavily("тест", limit=2)
            assert len(t_res) == 1
            assert t_res[0].url == "https://tavily.example.com"

        # Serper
        mock_serp_json = json.dumps({
            "organic": [{"link": "https://serper.example.com", "title": "Serper Title", "snippet": "Snippet"}]
        }).encode("utf-8")
        mock_serp_resp = MagicMock()
        mock_serp_resp.read.return_value = mock_serp_json
        mock_serp_resp.__enter__.return_value = mock_serp_resp

        with patch("urllib.request.urlopen", return_value=mock_serp_resp):
            s_res = rotator._search_serper("тест", limit=2)
            assert len(s_res) == 1

        # Firecrawl
        mock_fc_json = json.dumps({
            "data": [{"url": "https://firecrawl.example.com", "title": "FC Title", "description": "Desc"}]
        }).encode("utf-8")
        mock_fc_resp = MagicMock()
        mock_fc_resp.read.return_value = mock_fc_json
        mock_fc_resp.__enter__.return_value = mock_fc_resp
        with patch("urllib.request.urlopen", return_value=mock_fc_resp):
            fc_res = rotator._search_firecrawl("тест", limit=2)
            assert len(fc_res) == 1

        # SerpApi
        mock_sa_json = json.dumps({
            "organic_results": [{"link": "https://serpapi.example.com", "title": "SA Title", "snippet": "Snip"}]
        }).encode("utf-8")
        mock_sa_resp = MagicMock()
        mock_sa_resp.read.return_value = mock_sa_json
        mock_sa_resp.__enter__.return_value = mock_sa_resp
        with patch("urllib.request.urlopen", return_value=mock_sa_resp):
            sa_res = rotator._search_serpapi("тест", limit=2)
            assert len(sa_res) == 1
    finally:
        retrieval.network_allowed_ctx.reset(token)


def test_direct_apis_mocked_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from bormochecker import samples
    assert isinstance(samples.SAMPLES, list)

    token = retrieval.network_allowed_ctx.set(True)
    try:
        # 1. RuWikipediaAPI
        wiki = direct_apis.RuWikipediaAPI()
        assert wiki.search_and_extract("а") == []
        wiki_resp1 = MagicMock()
        wiki_resp1.read.return_value = json.dumps({"query": {"search": [{"pageid": 123}]}}).encode("utf-8")
        wiki_resp1.__enter__.return_value = wiki_resp1
        wiki_resp2 = MagicMock()
        wiki_resp2.read.return_value = json.dumps({
            "query": {
                "pages": {
                    "123": {
                        "title": "Образование в России",
                        "extract": "Образование в Российской Федерации регулируется федеральным законом и государственными стандартами.",
                        "fullurl": "https://ru.wikipedia.org/wiki/Образование",
                    }
                }
            }
        }).encode("utf-8")
        wiki_resp2.__enter__.return_value = wiki_resp2
        with patch("urllib.request.urlopen", side_effect=[wiki_resp1, wiki_resp2]):
            w_docs = wiki.search_and_extract("Образование в России", limit=1)
            assert len(w_docs) == 1
            assert w_docs[0].kind == "wikipedia"

        # 2. PravoGovAPI
        pravo = direct_apis.PravoGovAPI()
        pravo_resp = MagicMock()
        pravo_resp.read.return_value = json.dumps({
            "items": [
                {
                    "eoNumber": "0001202609010001",
                    "complexName": "Приказ Министерства просвещения № 704",
                    "documentDate": "2026-09-01",
                    "number": "704",
                }
            ]
        }).encode("utf-8")
        pravo_resp.__enter__.return_value = pravo_resp
        with patch("urllib.request.urlopen", return_value=pravo_resp):
            p_docs = pravo.search_acts("приказ Минпросвещения № 704", limit=2)
            assert len(p_docs) == 1
            assert p_docs[0].kind == "official"

        # 3. CyberLeninkaAPI
        cyber = direct_apis.CyberLeninkaAPI()
        cyber_resp = MagicMock()
        cyber_resp.read.return_value = json.dumps({
            "articles": [{"name": "Педагогическая диагностика", "link": "/article/n/ped", "annotation": "Аннотация статьи"}]
        }).encode("utf-8")
        cyber_resp.__enter__.return_value = cyber_resp
        with patch("urllib.request.urlopen", return_value=cyber_resp):
            c_docs = cyber.search_articles("исследование педагогической диагностики", limit=2)
            assert len(c_docs) == 1
            assert c_docs[0].kind == "scientific"

        # 4. MosRuSuggestAPI
        mos = direct_apis.MosRuSuggestAPI()
        mos_resp = MagicMock()
        mos_resp.read.return_value = json.dumps({
            "items": [{"title": "Школы Москвы", "url": "/news/item/1", "lead": "Лид новости"}]
        }).encode("utf-8")
        mos_resp.__enter__.return_value = mos_resp
        with patch("urllib.request.urlopen", return_value=mos_resp):
            m_docs = mos.search_mos_ru("школы Москвы и МЭШ", limit=2)
            assert len(m_docs) == 1
            assert m_docs[0].kind == "official"

        # 5. FgosReestrAPI
        fgos = direct_apis.FgosReestrAPI()
        fgos_resp = MagicMock()
        fgos_resp.read.return_value = json.dumps({
            "items": [{"id": "10", "title": "ФГОС ООО", "description": "Описание стандарта"}]
        }).encode("utf-8")
        fgos_resp.__enter__.return_value = fgos_resp
        with patch("urllib.request.urlopen", return_value=fgos_resp):
            f_docs = fgos.search_fgos("реестр программ ФГОС", limit=2)
            assert len(f_docs) == 1
            assert f_docs[0].kind == "official"

        # 6. DirectAPIs.fetch_candidates
        monkeypatch.setenv("FACTCHECK_DIRECT_APIS_ENABLED", "1")
        facade = direct_apis.DirectAPIs()
        with patch.object(facade.wiki, "search_and_extract", return_value=w_docs), \
             patch.object(facade.pravo, "search_acts", return_value=p_docs), \
             patch.object(facade.fgos, "search_fgos", return_value=f_docs), \
             patch.object(facade.cyberleninka, "search_articles", return_value=c_docs), \
             patch.object(facade.mos_ru, "search_mos_ru", return_value=m_docs):
            cand = facade.fetch_candidates("приказ ФГОС исследование школы Москвы", limit=5)
            assert len(cand) >= 1
    finally:
        retrieval.network_allowed_ctx.reset(token)


def test_evaluate_claim_and_sources_preload(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import time
    from datetime import datetime, timezone
    from bormochecker.costs import Meter
    from bormochecker import evaluate, sources
    from bormochecker.sources import Doc, Sources

    meter = Meter()
    doc = Doc(
        id="d1",
        url="https://edu.gov.ru/news/100",
        title="Приказ Минпросвещения",
        kind="official",
        text="В 2026 году в школах вводится 340 новых учебных кабинетов физики и информатики.",
    )
    ref_dt = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    # 1. evaluate_claim supported + confirmation check
    responses = [
        {
            "n": 1,
            "verdict": "supported",
            "exact_source_quote": "В 2026 году в школах вводится 340 новых учебных кабинетов физики и информатики.",
            "explanation": "Подтверждается документом.",
        },
        {
            "same_entity_and_event": True,
            "exact_numbers_and_dates": True,
            "modality_matches": True,
            "scope_matches": True,
            "reason": "Полное совпадение.",
        },
    ]
    with patch.object(evaluate, "_ask", side_effect=responses):
        v, reason, ev, _ = evaluate.evaluate_claim(
            "В 2026 году в школах вводится 340 новых учебных кабинетов.",
            [doc],
            note="",
            timeout=30.0,
            deadline=time.monotonic() + 30.0,
            meter=meter,
            warnings=[],
            reference_datetime=ref_dt,
        )
        assert v == "supported"
        assert len(ev) == 1

    # 2. evaluate_claim contradicted with relative past mismatch
    with patch.object(
        evaluate,
        "_ask",
        return_value={
            "n": 1,
            "verdict": "contradicted",
            "exact_source_quote": "В 2026 году в школах вводится 340 новых учебных кабинетов физики и информатики.",
            "explanation": "В документе 340, а не 200.",
        },
    ):
        v2, reason2, _, _ = evaluate.evaluate_claim(
            "В прошлом году в школах ввели 200 новых учебных кабинетов.",
            [doc],
            note="",
            timeout=30.0,
            deadline=time.monotonic() + 30.0,
            meter=meter,
            warnings=[],
            reference_datetime=ref_dt,
        )
        assert v2 == "unresolved"

    # 3. Sources preload & preload_local_docs
    monkeypatch.setenv("FACTCHECK_CACHE_DB", str(tmp_path / "cache.sqlite"))
    src = Sources(meter, network_allowed=True)
    src.preload_local_docs([
        {"file_name": "act.pdf", "extracted_text": "Текст приказа 704.", "storage_path": "/tmp/act.pdf"},
    ])
    assert len(src.linked) == 1

    fake_page = retrieval.FetchedDocument(
        url="https://edu.gov.ru/page1",
        final_url="https://edu.gov.ru/page1-final",
        status_code=200,
        content_type="text/html",
        title="Официальная страница",
        text="Подробный текст официального сообщения Министерства просвещения Российской Федерации от сентября 2026 года.",
    )
    with patch.object(sources, "fetch_page", return_value=fake_page):
        src.preload("Ссылка: https://edu.gov.ru/page1.")
        assert any(d.url == "https://edu.gov.ru/page1-final" for d in src.pool())
        # Повторное чтение из кэша и запрет сети
        doc_cached = src._load("https://edu.gov.ru/page1-final")
        assert doc_cached.title == "Официальная страница"
        doc_agg = src._load("https://example.com/search?q=test")
        assert doc_agg.kind == "aggregator"

    src_offline = Sources(meter, network_allowed=False)
    doc_off = src_offline._load("https://edu.gov.ru/new-page")
    assert "network_allowed=False" in doc_off.error
    src_offline.preload("https://edu.gov.ru/new-page")

    from bormochecker.direct_apis import DirectDocument
    from bormochecker.search import SearchResult

    src2 = Sources(meter, network_allowed=True)
    with (
        patch.object(src2.local_index, "search_docs", return_value=[]),
        patch.object(
            src2.direct_apis,
            "fetch_candidates",
            return_value=[
                DirectDocument(
                    url="https://publication.pravo.gov.ru/doc/1",
                    title="Приказ 704",
                    text="В 2026 году вводится 340 кабинетов.",
                    kind="official",
                )
            ],
        ),
        patch.object(
            src2.search_engine,
            "search",
            return_value=[
                SearchResult(
                    url="https://edu.gov.ru/page1",
                    title="Новость",
                    snippet="Сниппет",
                    rank=1,
                )
            ],
        ),
        patch.object(sources, "fetch_page", return_value=fake_page),
    ):
        gathered = src2.gather_for("В 2026 году в школах вводится 340 кабинетов", limit=2, mode="education")
        assert len(gathered) >= 1
        assert src2._is_high_confidence_pool_match("В 2026 году вводится 340 кабинетов") is True






