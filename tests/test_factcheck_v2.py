"""Тесты ядра верификации BormoChecker."""

from __future__ import annotations

import unittest

from bormochecker import derived, evaluate, local_index, sources, spans

from bormochecker.contract import Claim, Label
from bormochecker.retrieval import SafeDocumentFetcher, UnsafeUrlError, extract_title_and_text


class FactcheckV2Tests(unittest.TestCase):
    def test_morphology_and_duration_normalization(self):
        """1. Нормализация длительности и лемматизация: '1,5 часа' vs '1 час 40 минут'."""
        stems_1 = evaluate._stems("Продолжительность концерта: 1,5 часа")
        stems_2 = evaluate._stems("Продолжительность: 1 час 40 минут")

        # Общие леммы единиц времени
        self.assertIn("час", stems_1)
        self.assertIn("час", stems_2)
        self.assertTrue("минут" in stems_1 or "минута" in stems_1)
        self.assertTrue("минут" in stems_2 or "минута" in stems_2)

        # Канонические минуты (90 vs 100)
        self.assertIn("90", stems_1)
        self.assertIn("100", stems_2)

        # find_candidates поднимает строку с '1 час 40 минут' в топ-1 для утверждения про длительность
        doc = sources.Doc(
            id="d1",
            url="https://example.com/event",
            title="Концерт Dabro",
            kind="official",
            text="Большой зал ДК.\nПродолжительность: 1 час 40 минут.\nБилеты в кассах.",
        )
        candidates = evaluate.find_candidates("Продолжительность концерта: 1,5 часа", [doc])
        self.assertTrue(len(candidates) > 0)
        self.assertIn("1 час 40 минут", candidates[0]["quote"])

    def test_derived_weekday_and_percentages(self):
        """2. Детерминированный CPU-аудит: день недели, проценты и приказ № 729."""
        # 1 октября 2026 года — четверг (weekday 3), а не пятница (weekday 4)
        weekday_text = "Приём заявок завершается 1 октября 2026 года (это пятница)."
        wd_findings = derived.check_weekday_dates(weekday_text)
        self.assertTrue(len(wd_findings) > 0)
        self.assertEqual(wd_findings[0]["verdict"], "refuted")
        self.assertIn("четверг", wd_findings[0]["explanation"])

        # 303 из 344 подъездов — это 88.1%, а не 93% (атом с ошибкой получает Label.error)
        claims = [{"text": "Отремонтировано 303 из 344 подъездов (93%).", "label": Label.clarify}]
        derived.audit_derived_claims(claims[0]["text"], claims)
        error_claims = [c for c in claims if c.get("label") == Label.error]
        self.assertTrue(len(error_claims) > 0)
        self.assertIn("88.1%", error_claims[0]["explanation"])

        # Приказ № 729: обществознание исключено из 6-8 классов
        order_text = "По приказу № 729 обществознание преподаётся в 8–9 классах."
        order_findings = derived.check_known_education_orders(order_text)
        self.assertTrue(len(order_findings) > 0)
        self.assertIn("исключено", order_findings[0]["explanation"])

    def test_l4_substring_quote_resolver(self):
        """3. Фикс бага L4: автоподмена скопированного утверждения на реальную подстроку источника."""
        claim_text = "Спектакль начинается в 19:30"
        candidate_text = "Начало спектакля «Чайка» в 19:00 в Большом зале."

        # Если модель ошибочно скопировала claim_text в exact_source_quote:
        resolved = evaluate._resolve_source_quote(claim_text, claim_text, candidate_text)
        self.assertNotEqual(resolved, claim_text)
        self.assertIn(resolved, candidate_text)

        # Если модель вернула корректную цитату источника:
        good_quote = "в 19:00"
        resolved_good = evaluate._resolve_source_quote(good_quote, claim_text, candidate_text)
        self.assertEqual(resolved_good, good_quote)

    def test_table_header_preservation(self):
        """4. Сохранение заголовков <th> в каждую строку <tr> HTML-таблицы."""
        html_table = (
            "<table>"
            "  <thead>"
            "    <tr><th>Предмет</th><th>Класс</th><th>Часов в год</th></tr>"
            "  </thead>"
            "  <tbody>"
            "    <tr><td>Математика</td><td>5 класс</td><td>170</td></tr>"
            "    <tr><td>Информатика</td><td>7 класс</td><td>34</td></tr>"
            "  </tbody>"
            "</table>"
        )
        _, text = extract_title_and_text(html_table)
        self.assertIn("Предмет: Математика", text)
        self.assertIn("Класс: 5 класс", text)
        self.assertIn("Часов в год: 170", text)
        self.assertIn("Предмет: Информатика", text)

    def test_ssrf_and_cdn_guardrails(self):
        """5. Защита от SSRF и 0 внешних CDN."""
        fetcher = SafeDocumentFetcher()

        # Попытка запроса к защищённым доменам выбрасывает UnsafeUrlError
        for domain in sources.BLOCKED_DOMAINS:
            with self.assertRaises(UnsafeUrlError):
                fetcher.fetch(f"https://{domain}/news/123")

        # В static/index.html нет обращений к внешним CDN и Google Fonts
        from bormochecker.paths import repo_root
        index_html_path = repo_root() / "bormochecker/static/index.html"
        self.assertTrue(index_html_path.exists())
        content = index_html_path.read_text(encoding="utf-8")

        banned_cdns = (
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "cdn.jsdelivr.net",
            "unpkg.com",
            "cdnjs.cloudflare.com",
        )
        for cdn in banned_cdns:
            self.assertNotIn(cdn, content, f"Обнаружена ссылка на внешний CDN {cdn} в static/index.html")

    def test_local_fts_does_not_block_web_search_on_non_edu_claims(self):
        """6. Проверка, что запросы про Dabro и лосей не классифицируются как образовательные нормы."""
        dabro_query = "Концерт Dabro КЦ Зеленоград 1 октября"
        moose_query = "Лось гон август сентябрь Зеленоград"
        edu_query = "Приказ Минпросвещения 729 федеральная программа обществознание"

        self.assertFalse(local_index.is_education_or_normative_query(dabro_query))
        self.assertFalse(local_index.is_education_or_normative_query(moose_query))
        self.assertTrue(local_index.is_education_or_normative_query(edu_query))

    def test_linked_afisha_url_not_dropped_as_copy(self):
        """7. URL из текста новости (афиша КЦ) защищен от отбрасывания как копипаст."""
        news_text = "Подробнее на странице https://dkzelenograd.ru/afisha/123. Билеты в кассе."
        afisha_text = "Большой зал. Билеты в кассе. 1,5 часа без антракта."
        # При is_linked=True даже полное совпадение не считается копипастом
        self.assertFalse(sources.is_copy_paste_source(afisha_text, news_text, is_linked=True))

    def test_mean_as_range_caught_by_derived_cpu(self):
        """8. 'Средние баллы ... составили от 60 до 115' ловится CPU-аудитом со 100% точностью."""
        text = "Средние баллы для поступления на магистратуру составили от 60 до 115 баллов."
        findings = derived.check_mean_as_range_error(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["verdict"], "refuted")
        self.assertIn("диапазоном", findings[0]["explanation"])

    def test_relative_year_and_subset_guards_prevent_false_accusations(self):
        """9. Защита относительного года и кванторов подмножества от False Accusations."""
        # 1. Относительный год (r04-tykvy): в утверждении прошлый год, в цитате только текущий
        res_year = evaluate._verify_contradiction(
            claim_text="в прошлом году — 2 тысячи участников из 30 городов",
            selected_quote="Фестиваль тыквы 2026 года собрал 3500 участников.",
            note="Момент проверки: 26.09.2026.",
        )
        self.assertFalse(res_year.is_genuine_contradiction)
        self.assertEqual(res_year.downgraded_label, Label.clarify)

        # 2. Квантор подмножества (m06): в утверждении 'с первого раза', в источнике общая цифра
        res_subset = evaluate._verify_contradiction(
            claim_text="С первого раза приняли 303 из 344 подъездов",
            selected_quote="В рамках программы всего отремонтируют 344 подъезда.",
            note="Момент проверки: 26.09.2026.",
        )
        self.assertFalse(res_subset.is_genuine_contradiction)
        self.assertEqual(res_subset.downgraded_label, Label.clarify)

    def test_confirmation_guard_blocks_unbacked_numbers_and_wrong_year(self):
        """10. Гард False Confirmations понижает supported до clarify при отсутствии числа/года в цитате."""
        # Утверждение содержит 2027 год, а цитата — 2026
        res_year = evaluate._verify_confirmation(
            claim_text="В 2027-м году ремонт запланирован в 340 подъездах",
            selected_quote="В плане на 2026 год значится ремонт 340 подъездов.",
        )
        self.assertFalse(res_year.is_genuine_confirmation)
        self.assertEqual(res_year.downgraded_label, Label.clarify)

        # Утверждение содержит 850 мест, а цитата содержит 820
        res_num = evaluate._verify_confirmation(
            claim_text="Количество бюджетных мест составило 850",
            selected_quote="Всего в университете выделено 820 бюджетных мест.",
        )
        self.assertFalse(res_num.is_genuine_confirmation)
        self.assertEqual(res_num.downgraded_label, Label.clarify)

    def test_editorial_label_preserved_on_span_merge(self):
        """11. При слиянии спанов Label.editorial сохраняется, если соседний clarify не имеет ошибки."""
        c1 = Claim(
            start=0,
            end=50,
            text="Сразу двух диких зверей сняли ночью возле домов",
            label=Label.editorial,
            origin="stated",
            explanation="Собственное видео редакции",
            sources=[],
        )
        c2 = Claim(
            start=20,
            end=80,
            text="сняли ночью возле домов: лося — в 11-м микрорайоне",
            label=Label.clarify,
            explanation="В найденных источниках нет подходящего фрагмента",
            sources=[],
        )
        full_text = "Сразу двух диких зверей сняли ночью возле домов: лося — в 11-м микрорайоне"
        merged = spans.resolve_span_overlaps([c1, c2], full_text)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].label, Label.editorial)
        self.assertEqual(merged[0].origin, "stated")
