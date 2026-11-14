"""Юнит-тесты для 5 целевых исправлений (конфликты, изоляция фикстур, модальность, спаны, МИЭТ)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from bormochecker import evaluate, extract, origin, sources
from bormochecker.contract import CheckMode, Label
from bormochecker.costs import Meter
from bormochecker.retrieval import network_allowed_ctx
from bormochecker.scripts import eval_factcheck_benchmark
from bormochecker.sources import Doc, Sources


def test_single_source_date_mismatch_is_not_official_conflict() -> None:
    doc = Doc(
        id="att1",
        url="attachment://m01",
        title="Объявление школы",
        text="Мероприятие состоится 24 октября в актовом зале школы.",
        kind="official",
    )
    has_conflict, _ = evaluate._detect_official_numeric_conflict(
        "Мероприятие состоится 25 октября в актовом зале школы.",
        [doc],
        [{"doc": doc, "quote": doc.text}],
    )
    assert has_conflict is False


def test_incompatible_year_and_identifier_never_paired_as_conflict() -> None:
    doc = Doc(
        id="att2",
        url="attachment://r12",
        title="Приказ Департамента",
        text="Согласно приказу № 1222 от 2025 года, работы на объекте (1852 кв. м) завершены.",
        kind="official",
    )
    has_conflict, _ = evaluate._detect_official_numeric_conflict(
        "В 2026 году работы в 20 микрорайоне завершены.",
        [doc],
        [{"doc": doc, "quote": doc.text}],
    )
    assert has_conflict is False
    assert evaluate._find_conflicting_quantity_pair(
        "В 2026 году работы в 20 микрорайоне завершены.",
        [doc.text],
    ) is None


def test_confirmed_vs_independent_official_source_is_conflict() -> None:
    doc_a = Doc(
        id="doc_a",
        url="https://gov.example.org/a",
        title="Сводка А",
        text="По данным ведомства, мероприятие состоится 25 октября на главной площади.",
        kind="media",
    )
    doc_b = Doc(
        id="doc_b",
        url="https://gov.example.org/b",
        title="Сводка Б",
        text="По уточненным данным ведомства, мероприятие состоится 24 октября на главной площади.",
        kind="official",
    )
    has_conflict, reason = evaluate._detect_official_numeric_conflict(
        "Мероприятие состоится 25 октября на главной площади.",
        [doc_a, doc_b],
        [{"doc": doc_a, "quote": doc_a.text}, {"doc": doc_b, "quote": doc_b.text}],
    )
    assert has_conflict is True
    assert "25 октября" in reason and "24 октября" in reason


def test_find_candidates_gives_attachment_source_prior() -> None:
    web_doc = Doc(
        id="web1",
        url="https://example.org/web",
        title="Веб новость",
        text="Прием заявок завершится 15 ноября в здании лицея.",
        kind="official",
    )
    att_doc = Doc(
        id="att1",
        url="attachment://source",
        title="Приложенный документ",
        text="Прием заявок завершится 15 ноября в здании лицея.",
        kind="user_attachment",
    )
    cands = evaluate.find_candidates("Прием заявок завершится 15 ноября.", [web_doc, att_doc])
    assert cands
    assert cands[0]["doc"].kind == "user_attachment"


def test_future_modality_novelty_and_miet_relative_year_guards() -> None:
    ref = date(2026, 9, 26)

    # r03: безусловное будущее "построят" vs концепция -> clarify
    r03 = evaluate.verify_confirmation_cpu(
        "В районе возведут и построят две 30-этажные башни.",
        "Архитектурную концепцию двух башен согласовали городские власти.",
        reference_datetime=ref,
    )
    assert not r03.is_valid
    assert r03.downgraded_label == Label.clarify

    # m09: расхождение только модальности без числового конфликта -> clarify (не error)
    m09 = evaluate.verify_contradiction_cpu(
        "На месте пустыря построили две 30-этажные башни.",
        "Согласовали архитектурную концепцию двух 30-этажных башен.",
        reference_datetime=ref,
    )
    assert not m09.is_valid
    assert m09.downgraded_label == Label.clarify

    # dabro: "новый альбом" при подтверждении за прошлый год -> clarify
    dabro = evaluate.verify_confirmation_cpu(
        "Группа Dabro выпустила новый альбом.",
        "В 2025 году группа Dabro выпустила альбом «Случайная».",
        reference_datetime=ref,
    )
    assert not dabro.is_valid
    assert dabro.downgraded_label == Label.clarify

    # miet: относительное "годом ранее" + точное совпадение чисел в документе 2026 года
    miet = evaluate.verify_confirmation_cpu(
        "Годом ранее колледж получил 261 заявление.",
        "В колледж «Твердотельная электроника» было подано 261 заявление.",
        doc_title="Приемная кампания НИУ МИЭТ 2026",
        doc_text="Итоги приема 2026 года в сравнении с предыдущей кампанией: в 2025 году колледж получил 261 заявление.",
        reference_datetime=ref,
    )
    assert miet.is_valid
    assert miet.downgraded_label == Label.confirmed


def test_r06_announcement_and_dabro_tail_extracted() -> None:
    r06_line = "Встреча пройдет 25 сентября (это пятница) в 11:00 в библиотеке в корпусе 1462"
    r06_text = f"Анонс мероприятия\n\n{r06_line}\n\nЖдем всех гостей."
    s_pos = r06_text.index(r06_line)
    assert origin.is_heading_without_claim(r06_line, full_text=r06_text, span=(s_pos, s_pos + len(r06_line))) is False

    dabro_text = (
        "Коллектив получил три премии, выпустил новый альбом "
        "и продолжает записывать песни."
    )
    existing = [{
        "text": "Коллектив получил три премии, выпустил новый альбом",
        "quote": "Коллектив получил три премии, выпустил новый альбом",
        "start": 0,
        "end": 51,
        "span": (0, 51),
        "kind": "web_checkable",
        "origin": None,
        "type": "fact",
        "importance": "high",
    }]
    assert any("продолжает записывать песни" in g for g in extract.gaps(dabro_text, existing))

    with patch.object(extract, "_ask", return_value=[]):
        out = extract.extract_claims(dabro_text, meter=Meter(), note="", warnings=[])
    texts = [c["text"] for c in out]
    assert any("продолжает записывать песни" in t for t in texts)

    with patch.object(extract, "_ask", return_value=[]):
        out_r06 = extract.extract_claims(r06_text, meter=Meter(), note="", warnings=[])
    assert any("корпусе 1462" in c["text"] for c in out_r06)


def test_sources_pool_requires_exact_claim_numbers_and_anchors_miet() -> None:
    src = Sources(Meter(), checked_text="Итоги приемной кампании НИУ МИЭТ в 2026 году\nТогда вуз фиксировал рост на 47%.")
    src._docs["https://miet.ru/summary"] = Doc(
        id="d1",
        url="https://miet.ru/summary",
        title="МИЭТ: итоги приемной кампании",
        kind="official",
        text="В 2026 году выделено 966 бюджетных мест.",
    )
    assert src._is_high_confidence_pool_match("Тогда вуз также фиксировал рост числа заявлений на 47% — до 5763 человек.") is False
    assert src._is_high_confidence_pool_match("Годом ранее колледж получил 261 заявление.") is False

    qs = sources.build_queries("Годом ранее колледж получил 261 заявление.", anchor_subject="МИЭТ")
    assert any("МИЭТ" in q and "261" in q for q in qs)
    assert all("Минпросвещения" not in q for q in qs)


def test_reference_cases_force_live_search_when_not_offline(tmp_path, monkeypatch) -> None:
    eval_dir = tmp_path / "eval"
    ref_dir = eval_dir / "reference_cases"
    ref_dir.mkdir(parents=True)
    (ref_dir / "02_Открытый_пример_Dabro.txt").write_text("Группа Dabro выступит в Зеленограде.", encoding="utf-8")

    observed: list[tuple[CheckMode, bool]] = []

    def fake_check(text, **kwargs):
        observed.append((kwargs["mode"], network_allowed_ctx.get()))
        from bormochecker.contract import Costs, Report
        return Report(
            text=text,
            status="ok",
            summary="ok",
            costs=Costs(started_at=datetime.now(timezone.utc)),
            claims=[],
        )

    monkeypatch.setattr(eval_factcheck_benchmark.pipeline, "check", fake_check)
    monkeypatch.delenv("FACTCHECK_OFFLINE", raising=False)
    token = network_allowed_ctx.set(False)
    try:
        eval_factcheck_benchmark.run_reference_cases(
            eval_dir,
            mode=CheckMode.local_frp,
            only_case="dabro",
        )
    finally:
        network_allowed_ctx.reset(token)

    assert observed == [(CheckMode.education_searxng, True)]


def test_searxng_fallback_runs_on_non_cyrillic_degraded_hits_and_skips_social_walls() -> None:
    from bormochecker.search import SearchResult, SearXNGSearch

    calls: list[dict[str, str]] = []

    def fake_fetch(self, params: dict[str, str]) -> dict:
        calls.append(params)
        if "engines" not in params:
            return {
                "results": [{"url": "https://www.facebook.com/post/1", "title": "English post", "content": "text"}],
                "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["google", "access denied"]],
            }
        return {
            "results": [
                {
                    "url": "https://miet.ru/news/174311",
                    "title": "Итоги приемной кампании МИЭТ",
                    "content": "Колледж получил 261 заявление на бюджет.",
                }
            ]
        }

    engine = SearXNGSearch("http://127.0.0.1:8088")
    token = network_allowed_ctx.set(True)
    try:
        with patch.object(SearXNGSearch, "_fetch_searxng_json", fake_fetch):
            hits = engine.search("МИЭТ итоги приемной кампании 2025", limit=5)
    finally:
        network_allowed_ctx.reset(token)

    assert len(calls) == 2
    assert calls[1]["engines"] == "yahoo,bing"
    assert [h.url for h in hits] == ["https://miet.ru/news/174311", "https://www.facebook.com/post/1"]

    seen: set[str] = set()
    to_fetch = sources._select_urls_to_fetch(
        [
            SearchResult(url="https://www.facebook.com/post/1", title="fb", snippet=""),
            SearchResult(url="https://instagram.com/p/1", title="ig", snippet=""),
            SearchResult(url="https://example.org/doc.pdf", title="pdf", snippet=""),
            SearchResult(url="https://blocked.example/news", title="self", snippet=""),
            SearchResult(url="https://miet.ru/news/174311", title="miet", snippet=""),
        ],
        seen,
        limit=3,
    )
    assert to_fetch == ["https://miet.ru/news/174311"]


def test_class_number_not_official_conflict_and_hard_dimensional_promotion() -> None:
    ref = date(2026, 9, 26)
    doc_a = Doc(
        id="z1",
        url="https://zelenograd.ru/news/59010",
        title="Итоги приема МИЭТ 2025",
        text="В Колледже МИЭТ впервые появились 15 бюджетных мест для поступающих после 9 класса. Подано 261 заявление.",
        kind="media",
    )
    doc_b = Doc(
        id="m1",
        url="https://old.miet.ru/news/198519",
        title="Колледж МИЭТ 2026",
        text="В 2026 году колледж МИЭТ проводит набор поступающих после 9 и 11 класса на бюджетные места, 12 месяцев обучения.",
        kind="official",
    )
    has_conf, _ = evaluate._detect_official_numeric_conflict(
        "Годом ранее колледж впервые получил 15 бюджетных мест и 261 заявление.",
        [doc_a, doc_b],
        [{"doc": doc_a, "quote": doc_a.text}],
        reference_datetime=ref,
    )
    assert has_conf is False

    # PERCENT без совпадения метрики ("47% заявлений" vs "87 процентов выпускников") не даёт конфликт
    doc_p1 = Doc(id="p1", url="https://miet.ru/news/174717", title="Итоги МИЭТ", text="В МИЭТ получено на 47% больше заявлений.", kind="official")
    doc_p2 = Doc(id="p2", url="https://miet.ru/news/999", title="Итоги МИЭТ", text="В МИЭТ 87 процентов выпускников работают по специальности.", kind="official")
    has_pct_conf, _ = evaluate._detect_official_numeric_conflict(
        "Тогда в МИЭТ фиксировали рост числа заявлений — на 47%",
        [doc_p1, doc_p2],
        [{"doc": doc_p1, "quote": doc_p1.text}, {"doc": doc_p2, "quote": doc_p2.text}],
        reference_datetime=ref,
    )
    assert has_pct_conf is False

    # Дельта ("на 8 бюджетных мест меньше в бакалавриате") не конфликтует с абсолютным ("15 мест в колледже")
    doc_delta = Doc(
        id="p3",
        url="https://miet.ru/news/174717",
        title="Итоги МИЭТ",
        text="В 2025 году в МИЭТ выделено на 8 бюджетных мест меньше в бакалавриате и на 29 мест меньше в магистратуре.",
        kind="official",
    )
    has_delta_conf, _ = evaluate._detect_official_numeric_conflict(
        "Годом ранее колледж получил 15 бюджетных мест и 261 заявление.",
        [doc_a, doc_delta],
        [{"doc": doc_a, "quote": doc_a.text}, {"doc": doc_delta, "quote": doc_delta.text}],
        reference_datetime=ref,
    )
    # Код специальности 11.02.16 и пункт правил 9.5 не конфликтуют с числом мест
    doc_code = Doc(
        id="p4",
        url="https://koledzh.com/msk/miet",
        title="Колледж МИЭТ",
        text="Специальность 11.02.16 Твердотельная электроника в колледже МИЭТ, п. 9.5 правил приема.",
        kind="official",
    )
    has_code_conf, _ = evaluate._detect_official_numeric_conflict(
        "Годом ранее колледж получил 15 бюджетных мест по специальности «Твердотельная электроника» и 261 заявление.",
        [doc_a, doc_code],
        [{"doc": doc_a, "quote": doc_a.text}, {"doc": doc_code, "quote": doc_code.text}],
        reference_datetime=ref,
    )
    # Табличные числа производственного календаря без текстовых единиц ("вт: 18 | ср: 19") не конфликтуют
    doc_cal = Doc(
        id="p5",
        url="https://www.consultant.ru/law/ref/calendar/proizvodstvennye/2025/",
        title="Производственный календарь 2025",
        text="Август 2025 год колледж бюджетные дни и рабочие недели по Трудовому кодексу РФ [Таблица: пн: 1 | вт: 18 | ср: 19 | чт: 20]",
        kind="official",
    )
    has_cal_conf, _ = evaluate._detect_official_numeric_conflict(
        "Годом ранее колледж получил 15 бюджетных мест к 8 августа.",
        [doc_a, doc_cal],
        [{"doc": doc_a, "quote": doc_a.text}, {"doc": doc_cal, "quote": doc_cal.text}],
        reference_datetime=ref,
    )
    assert has_cal_conf is False
    assert has_code_conf is False
    assert has_delta_conf is False

    assert evaluate._has_hard_dimensional_conflict(
        "Билеты от 1500 до 6000 рублей появились в продаже",
        "Стоимость билетов от 2000 до 6000 ₽",
        ref,
    ) is True
    assert evaluate._has_hard_dimensional_conflict(
        "Фестиваль проходит до 30 сентября",
        "с 10 по 27 сентября 2026 года",
        ref,
    ) is True
    assert evaluate._has_hard_dimensional_conflict(
        "Продолжительность концерта: 1,5 часа без антракта",
        "Продолжительность: 1 час 40 минут без антракта.",
        ref,
    ) is True
    # Округление "тонну" vs "969 кг" не должно проходить verify_contradiction_cpu
    assert not evaluate.verify_contradiction_cpu(
        "в прошлом году удивили тыквой весом в тонну",
        "В 2025 году вырастил тыкву весом 969 кг",
        reference_datetime=ref,
    ).is_valid
    # Этажность и вложенный в диапазон срок
    assert evaluate._has_hard_dimensional_conflict(
        "Внутри бизнес-центров на 41 и 38 этажей предусмотрены офисы",
        "В одном бизнес-центре 28 этажей, другой 41-этажный",
        ref,
    ) is True
    assert evaluate._has_hard_dimensional_conflict(
        "Внутри бизнес-центров на 41 и 28 этажей предусмотрены офисы, на первых этажах — магазины",
        "Площадь зданий составит 100 тыс. кв. м, на первых этажах разместятся магазины",
        ref,
    ) is False
    assert evaluate._has_hard_dimensional_conflict(
        "Фестиваль урожая «Битва гигантов» проходит на ВДНХ до 27 сентября",
        "На «Городской ферме» ВДНХ пройдет фестиваль. С 10 по 27 сентября покажут овощи. Взвешивание — 10 сентября.",
        ref,
    ) is False


def test_unresolved_promotes_on_candidate_dimensional_conflict() -> None:
    from datetime import datetime
    from unittest.mock import patch
    from bormochecker.llm import Meter

    ref = datetime(2026, 9, 25, 12, 0)
    doc = Doc(
        id="d1.txt",
        url="file://d1.txt",
        title="ТПУ Петровско-Разумовская",
        kind="user_attachment",
        text=(
            "Рядом с ТПУ «Петровско-Разумовская» построят два бизнес-центра высотой 41 и 28 этажей.\n\n"
            "В зданиях предусмотрены офисы, на первых этажах откроются магазины и кафе."
        ),
    )
    meter = Meter()
    warnings: list[str] = []
    # Имитируем ситуацию, когда LLM ошибся и взял нейтральный второй фрагмент с unresolved
    with patch.object(
        evaluate,
        "_ask",
        return_value={
            "verdict": "unresolved",
            "evidence": [{"n": 2, "relation": "supports"}],
            "reason": "Этажность во фрагменте не указана.",
            "suggestion": None,
        },
    ):
        verdict, _, ev, _ = evaluate.evaluate_claim(
            "Внутри бизнес-центров на 41 и 38 этажей предусмотрены офисы, на первых этажах — магазины.",
            [doc],
            meter,
            warnings,
            timeout=10.0,
            reference_datetime=ref,
        )
    # "Все работы написаны маслом" vs "в своей практике" (общая практика != эта выставка)
    assert not evaluate.verify_contradiction_cpu(
        "Все работы написаны маслом",
        "Светлана использует в своей практике разные живописные и графические техники, мастерски сочетая их на своих холстах.",
        reference_datetime=ref,
    ).is_valid
    # Дата отчёта в заголовке/шапке документа (08.08.2025) подтверждает "к 8 августа"
    res_lead_date = evaluate.verify_confirmation_cpu(
        "Годом ранее колледж получил 15 бюджетных мест: к 8 августа было подано 261 заявление",
        "В Колледже МИЭТ впервые в этом году появились 15 бюджетных мест. На них подано уже 261 заявление.",
        doc_title="Итоги приема МИЭТ",
        reference_datetime=ref,
        doc_text="08.08.2025 Зеленоград\n\nВ Колледже МИЭТ впервые в этом году появились 15 бюджетных мест.",
    )
    assert res_lead_date.is_valid is True
    assert verdict == "contradicted"
    assert any("28 этажей" in q for _, q, rel in ev if rel == "contradicts")


def test_dabro_pure_conf_promotion_and_miet_261_candidate_recovery() -> None:
    from datetime import datetime
    from unittest.mock import patch
    from bormochecker.llm import Meter

    ref = datetime(2026, 9, 26, 12, 0)
    dk_doc = Doc(
        id="dk",
        url="https://dkzelenograd.ru/afisha/kontserti/kontsert-gruppi-dabro",
        title="Концерт группы Dabro",
        kind="official",
        text="Официальная афиша КЦ «Зеленоград» (Центральная площадь, дом 1). Концертный тур группы Dabro. Дата и время: 1 октября (четверг) в 19:00.",
    )
    meter = Meter()
    warnings: list[str] = []
    with patch.object(
        evaluate,
        "_ask",
        return_value={
            "verdict": "partially_supported",
            "evidence": [{"n": 1, "relation": "partially_supports"}],
            "reason": "Фрагмент [1] подтверждает, что концерт группы Dabro состоится в КЦ «Зеленоград» по адресу: Центральная площадь, дом 1.",
            "suggestion": None,
        },
    ):
        v_dabro, _, _, _ = evaluate.evaluate_claim(
            "Концерт группы Dabro состоится в Культурном центре «Зеленоград» по адресу: Центральная площадь, дом 1",
            [dk_doc],
            meter,
            warnings,
            timeout=10.0,
            reference_datetime=ref,
        )
    assert v_dabro == "supported"

    z_doc = Doc(
        id="z59010",
        url="https://www.zelenograd.ru/news/59010/",
        title="Число желающих поступить в МИЭТ выросло на 1000 человек",
        kind="media",
        text=(
            "Число желающих поступить в МИЭТ выросло на 1000 человек 08.08.2025 ZELENOGRAD.RU\n\n"
            "В Колледже электроники и информатики МИЭТ впервые в этом году появились 15 бюджетных мест "
            "(специальность Твердотельная электроника) для поступающих после 9 класса. На них подано уже 261 заявление.\n\n"
            "Станьте нашим подписчиком\n\nКвиз по Гарри Поттеру проведут 18 ноября в центре Зеленограда."
        ),
    )
    cal_doc = Doc(
        id="cal",
        url="https://www.consultant.ru/law/ref/calendar/proizvodstvennye/2025/",
        title="Производственный календарь 2025",
        kind="official",
        text="В мае 2025 года по специальности колледж бюджетных мест отдыхать будут с 1 по 4 мая и 8 августа.",
    )
    cands = evaluate.find_candidates(
        "Годом ранее колледж только впервые получил бюджетные места: на 15 мест по специальности «Твердотельная электроника» к 8 августа было подано 261 заявление",
        [z_doc, cal_doc],
        reference_datetime=ref,
    )
    assert cands[0]["doc"].id == "z59010"
    cal_idx = next(i + 1 for i, c in enumerate(cands) if c["doc"].id == "cal")
    with patch.object(
        evaluate,
        "_ask",
        return_value={
            "verdict": "contradicted",
            "evidence": [{"n": cal_idx, "relation": "contradicts"}],
            "reason": "В календаре указаны общие даты.",
            "suggestion": None,
        },
    ):
        v_261, _, _, _ = evaluate.evaluate_claim(
            "Годом ранее колледж только впервые получил бюджетные места: на 15 мест по специальности «Твердотельная электроника» к 8 августа было подано 261 заявление",
            [z_doc, cal_doc],
            meter,
            warnings,
            timeout=10.0,
            reference_datetime=ref,
        )
        v_261_norm, _, _, _ = evaluate.evaluate_claim(
            "В 2025 году колледж МИЭТ впервые получил бюджетные места: на 15 мест по специальности «Твердотельная электроника» к 8 августа было подано 261 заявление.",
            [z_doc, cal_doc],
            meter,
            warnings,
            timeout=10.0,
            reference_datetime=ref,
        )
    assert v_261 == "supported"
    assert v_261_norm == "supported"


def test_surgical_polish_fixes(tmp_path):
    """Проверка расщепления контрастных этажей, фильтрации нумерации 5)/17.6.5, единиц времени/процентов и вложений."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from bormochecker import costs, evaluate as ev_mod, extract, numerals, sources
    from bormochecker.contract import Claim, Costs, Label, Report
    from bormochecker.scripts.eval_factcheck_benchmark import score_item

    # 1. Расщепление составного спана про этажи + сохранение спана с числами справа
    prefix = "A" * 212
    sent = "Внутри бизнес-центров на 41 и 38 этажей предусмотрены офисы, на первых этажах — помещения для магазинов."
    full_text = prefix + sent
    claims = [{
        "quote": sent,
        "text": sent,
        "span": (212, 212 + len(sent)),
        "start": 212,
        "end": 212 + len(sent),
        "type": "fact",
        "kind": "web_checkable",
        "origin": None,
        "importance": "high",
        "entity_cluster": "c1",
        "query": "этажи",
        "reason": "",
    }]
    split_claims = extract.absorb_redundant_subspans(claims, full_text)
    assert [c["span"] for c in split_claims] == [(212, 271), (273, 316)]

    miet_sent = "Количество очных бюджетников выросло на 15 мест, тогда как на очно-заочной форме их стало меньше — 11 вместо 23."
    miet_claims = [{
        "quote": miet_sent,
        "text": miet_sent,
        "span": (0, len(miet_sent)),
        "start": 0,
        "end": len(miet_sent),
        "type": "fact",
        "kind": "web_checkable",
        "origin": None,
        "importance": "high",
        "entity_cluster": "c1",
        "query": "бюджетники",
        "reason": "",
    }]
    assert len(extract.absorb_redundant_subspans(miet_claims, miet_sent)) == 1

    # 1b. Удаление оторванного числового префикса "19%" и строчного 2-словного СТА "нужно записаться", с сохранением "93%."
    frag_txt = "С первого раза приняли 303 из 344 — это 93%. Более 19% подъездов в 10-12 микрорайонах оказались с недостатками. нужно записаться."
    frag_claims = [
        {"quote": "93%", "text": "93%", "span": (frag_txt.index("93%"), frag_txt.index("93%") + 3), "start": frag_txt.index("93%"), "end": frag_txt.index("93%") + 3, "type": "numeric", "kind": "web_checkable", "origin": None, "importance": "high", "entity_cluster": "c1", "query": "93%", "reason": ""},
        {"quote": "19%", "text": "19%", "span": (frag_txt.index("19%"), frag_txt.index("19%") + 3), "start": frag_txt.index("19%"), "end": frag_txt.index("19%") + 3, "type": "numeric", "kind": "web_checkable", "origin": None, "importance": "high", "entity_cluster": "c1", "query": "19%", "reason": ""},
        {"quote": " подъездов в 10-12 микрорайонах оказались с недостатками.", "text": " подъездов в 10-12 микрорайонах оказались с недостатками.", "span": (frag_txt.index("19%") + 3, frag_txt.index(" нужно")), "start": frag_txt.index("19%") + 3, "end": frag_txt.index(" нужно"), "type": "fact", "kind": "web_checkable", "origin": None, "importance": "high", "entity_cluster": "c1", "query": "подъезды", "reason": ""},
        {"quote": "нужно записаться", "text": "нужно записаться", "span": (frag_txt.index("нужно"), len(frag_txt) - 1), "start": frag_txt.index("нужно"), "end": len(frag_txt) - 1, "type": "fact", "kind": "web_checkable", "origin": None, "importance": "low", "entity_cluster": "c1", "query": "запись", "reason": ""},
    ]
    cleaned_frags = extract.absorb_redundant_subspans(frag_claims, frag_txt)
    assert [c["quote"] for c in cleaned_frags] == ["93%", " подъездов в 10-12 микрорайонах оказались с недостатками."]

    # 1c. Удаление заголовка до двоеточия без глагола ("«Конь на e4», «Шах и мат»:") и короткой переходной клаузы ("но сами проекты...")
    title_txt = "«Конь на e4», «Шах и мат»: в КЦ открылась выставка. Работы продлятся, но сами проекты заметно отличаются."
    title_claims = [
        {"quote": "«Конь на e4»", "text": "«Конь на e4»", "span": (0, 12), "start": 0, "end": 12, "type": "fact", "kind": "web_checkable", "origin": None, "importance": "low", "entity_cluster": "c1", "query": "конь", "reason": ""},
        {"quote": "«Конь на e4», «Шах и мат»", "text": "«Конь на e4», «Шах и мат»", "span": (0, 25), "start": 0, "end": 25, "type": "fact", "kind": "web_checkable", "origin": None, "importance": "low", "entity_cluster": "c1", "query": "конь", "reason": ""},
        {"quote": "но сами проекты заметно отличаются", "text": "но сами проекты заметно отличаются", "span": (title_txt.index("но сами"), len(title_txt) - 1), "start": title_txt.index("но сами"), "end": len(title_txt) - 1, "type": "opinion", "kind": "opinion", "origin": None, "importance": "low", "entity_cluster": "c1", "query": "проекты", "reason": ""},
    ]
    assert extract.absorb_redundant_subspans(title_claims, title_txt) == []

    # 2. extract_quantities не берёт 5), 6), 17.6.5, 11.02.16 как COUNT
    rules_txt = "17.6.5 При приёме по 11.02.16 действуют условия: 5) экзамен; 6) собеседование."
    assert [q for q in numerals.extract_quantities(rules_txt) if q.dim == "COUNT"] == []

    # 3. Несопоставимые единицы времени (45 минут vs 35 часов) не дают конфликт, а 1,5 часа vs 1 час 40 минут — дают
    doc_a = Doc(id="a", url="https://a.example/1", title="Афиша", kind="official", text="Спектакль длится 45 минут без антракта.")
    doc_b = Doc(id="b", url="https://b.example/2", title="Норма", kind="official", text="Спектакль и недельная норма длится 35 часов в неделю.")
    has_dur_conf, _ = evaluate._detect_official_numeric_conflict(
        "Спектакль длится 45 минут без антракта.",
        [doc_a, doc_b],
        [{"doc": doc_a, "quote": doc_a.text}, {"doc": doc_b, "quote": doc_b.text}],
    )
    assert has_dur_conf is False

    doc_dabro_a = Doc(id="da", url="https://da.example/1", title="Афиша 1", kind="official", text="Концерт группы Dabro длится 1,5 часа.")
    doc_dabro_b = Doc(id="db", url="https://db.example/2", title="Афиша 2", kind="official", text="Концерт группы Dabro длится 1 час 40 минут.")
    has_dabro_conf, _ = evaluate._detect_official_numeric_conflict(
        "Концерт группы Dabro длится 1,5 часа.",
        [doc_dabro_a, doc_dabro_b],
        [{"doc": doc_dabro_a, "quote": doc_dabro_a.text}, {"doc": doc_dabro_b, "quote": doc_dabro_b.text}],
    )
    assert has_dabro_conf is True

    # 4. PERCENT: аппозитив "менее чем на 1%" не конфликтует с "на 23 процента выросло количество бюджетных мест"
    p_doc1 = Doc(
        id="p1",
        url="https://miet.example/1",
        title="МИЭТ",
        kind="official",
        text="Количество бюджетных мест в МИЭТ увеличилось на 1% и составило 966.",
    )
    p_doc2 = Doc(
        id="p2",
        url="https://mos.example/2",
        title="Мос",
        kind="official",
        text="За последние пять лет в МИЭТ на 23 процента выросло количество бюджетных мест по техническим специальностям.",
    )
    has_miet_pct_conf, _ = evaluate._detect_official_numeric_conflict(
        "Количество бюджетных мест увеличилось совсем немного — с 959 до 966, то есть всего на семь мест, или менее чем на 1%.",
        [p_doc1, p_doc2],
        [{"doc": p_doc1, "quote": p_doc1.text}, {"doc": p_doc2, "quote": p_doc2.text}],
    )
    assert has_miet_pct_conf is False

    # Один и тот же документ с разным регистром www/слешем не конфликтует сам с собой
    same_1 = Doc(id="s1", url="https://www.example.org/page/", title="Официально", kind="official", text="Доля выпускников составила 45%.")
    same_2 = Doc(id="s2", url="http://example.org/page", title="Официально", kind="official", text="Доля выпускников составила 30%.")
    has_self_conf, _ = evaluate._detect_official_numeric_conflict(
        "Доля выпускников составила 45%.",
        [same_1, same_2],
        [{"doc": same_1, "quote": same_1.text}, {"doc": same_2, "quote": same_2.text}],
    )
    assert has_self_conf is False

    # Настоящий конфликт PERCENT по тому же существительному в одном предложении сохраняется
    real_pct_2 = Doc(id="rp2", url="https://other.org/stat", title="Отчёт", kind="official", text="Доля выпускников составила 30%.")
    has_real_pct_conf, _ = evaluate._detect_official_numeric_conflict(
        "Доля выпускников составила 45%.",
        [same_1, real_pct_2],
        [{"doc": same_1, "quote": same_1.text}, {"doc": real_pct_2, "quote": real_pct_2.text}],
    )
    assert has_real_pct_conf is True

    # 5. Sources.gather_for не опрашивает local_index, если уже предзагружены attachments
    att_file = tmp_path / "afisha.txt"
    att_file.write_text("Спектакль Тучка длится 45 минут в КЦ Зеленоград.", encoding="utf-8")
    src = sources.Sources(costs.Meter(), "Школьный спектакль для учеников длится 45 минут.")
    assert ev_mod is evaluate
    src.preload_local_docs([SimpleNamespace(id="att1", url="attachment://afisha.txt", title="Афиша", extracted_text_path=str(att_file), mime_type="text/plain")])
    src.local_index = MagicMock()
    src.search_engine = MagicMock()
    src.search_engine.search.return_value = []
    src.gather_for("Школьный урок на свежем воздухе", query="Школьный урок", mode="local_frp")
    src.local_index.search_docs.assert_not_called()

    # 6. score_item берёт tools_cost_usd ($0.00), когда total_cost_usd == None
    rep = Report(
        status="ok",
        summary="ok",
        text="Факт.",
        claims=[Claim(start=0, end=5, text="Факт.", label=Label.confirmed, explanation="ok")],
        costs=Costs(
            started_at=datetime.now(timezone.utc),
            paid_search_requests=0,
            tools_cost_usd=0.0,
            total_cost_usd=None,
        ),
    )
    scored = score_item(
        {"id": "t", "text": "Факт.", "gold": {"claims": [{"id": "g1", "span": [0, 5], "quote": "Факт.", "verdict": "supported"}]}, "split": "dev"},
        rep,
        12.0,
    )
    assert scored.cost_usd == 0.0

    # 7. Декларация «утверждение не содержит деталей, присутствующих в источниках» не мешает supported
    dk_doc = Doc(
        id="dk",
        url="https://dkzelenograd.ru/afisha/kontserti/kontsert-gruppi-dabro",
        title="Концерт группы Dabro",
        kind="official",
        text="Официальная афиша КЦ «Зеленоград» (Центральная площадь, дом 1). Дата и время: 1 октября (четверг) в 19:00.",
    )
    with patch.object(
        evaluate,
        "_ask",
        return_value={
            "verdict": "partially_supported",
            "evidence": [{"n": 1, "relation": "partially_supports"}],
            "reason": (
                "Фрагменты подтверждают, что концерт группы Dabro состоится в КЦ «Зеленоград» "
                "по адресу Центральная площадь, дом 1. Однако утверждение не содержит даты и времени, "
                "которые присутствуют в источниках."
            ),
            "suggestion": None,
        },
    ):
        v_subclaim, _, _, _ = evaluate.evaluate_claim(
            "Концерт группы Dabro состоится в Культурном центре «Зеленоград» по адресу: Центральная площадь, дом 1",
            [dk_doc],
            costs.Meter(),
            [],
            timeout=10.0,
            reference_datetime=datetime(2026, 9, 26, 12, 0),
        )
    assert v_subclaim == "supported"
