"""Тесты для проверки видов источников (T205 / [Q04])."""
from bormochecker.pipeline import _preload_fact_ledger
from bormochecker.sources import Sources, publisher_kind


class DummyMeter:
    def count(self, **kwargs):
        pass


def test_user_attachment_kind():
    sources = Sources(meter=DummyMeter(), checked_text="Текст")
    attachments = [{
        "file_name": "spravka.pdf",
        "extracted_text": "Текст справки от пользователя",
    }]
    sources.preload_local_docs(attachments)
    docs = sources.pool()
    assert len(docs) == 1
    assert docs[0].kind == "user_attachment"


def test_fact_ledger_kind():
    sources = Sources(meter=DummyMeter(), checked_text="Текст")
    ledger_entries = [{
        "fact_id": "F01",
        "exact_quote": "Проверенная цитата из реестра фактов",
        "source_id": "S01",
    }]
    _preload_fact_ledger(sources, ledger_entries)
    docs = sources.pool()
    ledger_doc = next(d for d in docs if d.id == "fact_ledger")
    assert ledger_doc.kind == "ledger"


def test_official_domain_kind():
    assert publisher_kind("edu.gov.ru") == "official"
    assert publisher_kind("obrnadzor.gov.ru") == "official"
    assert publisher_kind("some-blog.ru") == "unknown"
    assert publisher_kind("tass.ru") == "media"
