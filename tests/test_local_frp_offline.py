from __future__ import annotations

import socket
import pytest

from bormochecker.contract import CheckMode
from bormochecker.pipeline import check
from bormochecker.retrieval import NetworkNotAllowedError, SafeDocumentFetcher, network_allowed_ctx


def test_network_not_allowed_in_fetcher():
    # Проверяем, что SafeDocumentFetcher.fetch выбрасывает NetworkNotAllowedError при network_allowed=False
    fetcher = SafeDocumentFetcher()
    token = network_allowed_ctx.set(False)
    try:
        with pytest.raises(NetworkNotAllowedError, match="Сетевые запросы запрещены"):
            fetcher.fetch("https://example.com/test-article")
    finally:
        network_allowed_ctx.reset(token)


def test_local_frp_runs_with_socket_blocked(monkeypatch):
    # Блокируем любые сетевые соединения через socket.socket
    def guarded_socket(*args, **kwargs):
        raise RuntimeError("Сетевой запрос строго запрещён в тесте local_frp!")

    monkeypatch.setattr(socket, "socket", guarded_socket)

    # Заглушка для LLM (chat), возвращающая детерминированные ответы
    from bormochecker import extract as fc_extract
    from bormochecker import evaluate as fc_evaluate
    import bormochecker.extract as np_extract
    import bormochecker.evaluate as np_evaluate
    from bormochecker import llm

    def dummy_chat(meter, messages, **kwargs):
        content = messages[-1]["content"]
        if "Разбей новость на атомарные проверяемые утверждения" in content:
            return (
                '{"claims": [{"quote": "В школе открылся новый класс", "claim": "В школе открылся новый класс", '
                '"kind": "web_checkable", "type": "fact", "importance": "high", "entity_cluster": "школа", '
                '"query": "школа новый класс", "reason": "факт"}]}'
            )
        return '{"comparison_note": "ok", "verdict": "unresolved", "evidence": [], "evidence_strength": "low", "reason": "local", "limitations": [], "suggestion": null}'

    monkeypatch.setattr(llm, "chat", dummy_chat)
    monkeypatch.setattr(llm._default_client, "chat", dummy_chat)
    monkeypatch.setattr(fc_extract.llm, "chat", dummy_chat)
    monkeypatch.setattr(np_extract.llm, "chat", dummy_chat)
    monkeypatch.setattr(fc_evaluate.llm, "chat", dummy_chat)
    monkeypatch.setattr(np_evaluate.llm, "chat", dummy_chat)

    text = "В школе открылся новый класс. Ссылка на новость: https://example.com/news/123"
    note = "Подробнее на https://example.com/note/456"

    # Вызов check в режиме local_frp не должен делать сетевых запросов и не должен упасть
    report = check(text, note, mode=CheckMode.local_frp)
    assert report is not None
    assert report.status in ("ok", "partial")
    assert len(report.claims) >= 1
