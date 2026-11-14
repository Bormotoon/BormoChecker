"""Тест для проверки фильтрации копипаста при чтении из кеша (T206 / [Q05])."""
from bormochecker.sources import Sources



class DummyMeter:
    def count(self, **kwargs):
        pass

    def add_cache_hit(self):
        pass

    def add_fetch(self):
        pass


def test_copypaste_in_cache_is_rejected(tmp_path, monkeypatch):
    news_text = (
        "Министерство образования утвердило новые правила проведения всероссийских олимпиад школьников. "
        "В текущем учебном году олимпиада пройдет в четыре традиционных этапа: школьный, муниципальный, "
        "региональный и заключительный этапы соревнований для всех категорий участников. "
        "Все победители и призеры заключительного этапа получат особые льготы при поступлении в ведущие вузы страны."
    )
    meter = DummyMeter()
    sources = Sources(meter=meter, checked_text=news_text)

    # Записываем в кеш страницу, чей текст является полной копией проверяемой новости
    copypaste_url = "https://media.example.com/mirror-news"
    sources.cache.put_page(copypaste_url, "Копия новости", news_text)

    # Чтение из кеша должно отклонить источник из-за копипаста
    doc = sources._load(copypaste_url, is_linked=False)
    assert doc.error == "копия проверяемого текста — не независимый источник"
    assert doc.text == ""
