"""Тесты для проверки подтверждения editorial по локальным вложениям (T204 / [Q03])."""
from bormochecker.contract import Label
from bormochecker.pipeline import _judge
from bormochecker.sources import Doc, Sources


class DummyMeter:
    def count(self, **kwargs):
        pass


def test_negation_in_attachment_prevents_confirmed():
    # Утверждение положительное, в документе отрицание: не должно давать confirmed
    atom = {
        "text": "школа получила лицензию",
        "kind": "editorial",
        "origin": "stated",
    }
    doc = Doc(
        id="lic1",
        url="attachment://doc1.pdf",
        title="Официальный ответ",
        text="По результатам проверки школа не получила лицензию на ведение деятельности.",
        kind="official",
    )
    meter = DummyMeter()
    sources = Sources(meter=meter, checked_text="Текст новости")
    sources._docs[doc.url] = doc

    _judge(atom, sources, deadline=10.0, meter=DummyMeter(), notes=[])
    assert atom["label"] != Label.confirmed
    assert atom["label"] == Label.clarify
    assert "отрицание" in atom["explanation"]


def test_quote_matching_filename_or_title_gives_clarify():
    # Цитата совпадает с именем файла или заголовком: метка clarify
    atom = {
        "text": "школа получила лицензию",
        "kind": "editorial",
        "origin": "stated",
    }
    doc = Doc(
        id="lic2",
        url="attachment://школа получила лицензию.pdf",
        title="школа получила лицензию",
        text="школа получила лицензию",
        kind="official",
    )
    meter = DummyMeter()
    sources = Sources(meter=meter, checked_text="Текст новости")
    sources._docs[doc.url] = doc

    _judge(atom, sources, deadline=10.0, meter=DummyMeter(), notes=[])
    assert atom["label"] != Label.confirmed
    assert atom["label"] == Label.clarify
    assert "заголовком или именем файла" in atom["explanation"]


def test_valid_body_quote_confirms_with_location():
    # Цитата из тела документа с совпадением полярности подтверждает факт с указанием диапазона
    atom = {
        "text": "школа получила лицензию",
        "kind": "editorial",
        "origin": "stated",
    }
    doc = Doc(
        id="lic3",
        url="attachment://report.pdf",
        title="Годовой отчет",
        text="В соответствии с приказом министерства, школа получила лицензию в установленный срок.",
        kind="official",
    )
    meter = DummyMeter()
    sources = Sources(meter=meter, checked_text="Текст новости")
    sources._docs[doc.url] = doc

    _judge(atom, sources, deadline=10.0, meter=DummyMeter(), notes=[])
    assert atom["label"] == Label.confirmed
    assert "символы" in atom["explanation"]
    assert len(atom["sources"]) == 1
    assert "школа получила лицензию" in atom["sources"][0].quote
