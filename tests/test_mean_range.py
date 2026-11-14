"""Тесты для проверки логики среднего как диапазона (T203 / [Q02])."""
from bormochecker import derived


def test_mean_as_range_false_positive_regular_range():
    # 1. Обычный диапазон без упоминания среднего
    text = "Проходные баллы для поступления на бюджет составили от 180 до 240 баллов."
    findings = derived.check_mean_as_range_error(text)
    assert not any(f.get("verdict") == "refuted" for f in findings)


def test_mean_as_range_false_positive_mean_without_range():
    # 2. Среднее значение без диапазона
    text = "Средний балл абитуриентов, поступивших на факультет, составил 78 баллов."
    findings = derived.check_mean_as_range_error(text)
    assert not any(f.get("verdict") == "refuted" for f in findings)


def test_mean_as_range_false_positive_two_metrics():
    # 3. Два разных показателя (среднее и отдельный диапазон конкурсных/проходных баллов)
    text = "Средний балл составил 75, а конкурсные баллы варьировались от 150 до 220."
    findings = derived.check_mean_as_range_error(text)
    assert not any(f.get("verdict") == "refuted" for f in findings)


def test_mean_as_range_across_groups_is_not_refuted():
    # Диапазон средних по группам/факультетам не является логической ошибкой
    text = "Средние баллы по разным факультетам составили от 60 до 85."
    findings = derived.check_mean_as_range_error(text)
    assert not any(f.get("verdict") == "refuted" for f in findings)


def test_mean_as_range_true_error():
    # 4. Истинный пример: среднее приписано диапазону без указания групп
    text = "Средние баллы для поступления на магистратуру составили от 60 до 115 баллов."
    findings = derived.check_mean_as_range_error(text)
    refuted = [f for f in findings if f.get("verdict") == "refuted"]
    assert len(refuted) == 1
    assert "диапазон" in refuted[0]["explanation"]


def test_scalar_statistic_as_range_universal():
    err_text = "Средний возраст участников олимпиады составил от 12 до 24 лет."
    findings = derived.check_scalar_statistic_as_range(err_text)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "refuted"
    assert findings[0]["type"] == "scalar_statistic_as_range"

    ok_text = "Средний возраст участников по разным направлениям варьируется от 12 до 24 лет."
    assert derived.check_scalar_statistic_as_range(ok_text) == []
