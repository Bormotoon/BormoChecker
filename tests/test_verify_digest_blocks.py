from datetime import datetime

from bormochecker.contract import Claim, Costs, Label, Report
from bormochecker.pipeline import verify_digest_blocks


def _make_report(status="ok", claims=None):
    text = "Текст блока новости."
    if claims is None:
        claims = [
            Claim(
                start=0,
                end=len(text),
                text=text,
                label=Label.confirmed,
                explanation="Подтверждено",
            )
        ]
    return Report(
        status=status,
        summary="Сводка отчёта",
        text=text,
        claims=claims,
        costs=Costs(started_at=datetime.now()),
    )


def test_verify_digest_blocks_exception(monkeypatch):
    # 1. Исключение
    def dummy_check(*args, **kwargs):
        raise RuntimeError("LLM connection crashed")

    monkeypatch.setattr("bormochecker.pipeline.check", dummy_check)
    res = verify_digest_blocks([{"draft_text": "Какой-то текст"}])
    assert res[0]["verified"] is False
    assert "reason" in res[0]
    assert "LLM connection crashed" in res[0]["reason"]


def test_verify_digest_blocks_timeout_partial(monkeypatch):
    # 2. Таймаут / частичный отчёт (status != ok)
    partial_report = _make_report(status="partial", claims=[])
    monkeypatch.setattr("bormochecker.pipeline.check", lambda *args, **kwargs: partial_report)
    res = verify_digest_blocks([{"draft_text": "Какой-то текст"}])
    assert res[0]["verified"] is False
    assert "reason" in res[0]


def test_verify_digest_blocks_error_claim(monkeypatch):
    # 3. Отчёт с ошибкой (error claim)
    text = "Текст блока новости."
    err_claims = [
        Claim(
            start=0,
            end=len(text),
            text=text,
            label=Label.error,
            explanation="Опровергнуто источниками",
        )
    ]
    err_report = _make_report(status="ok", claims=err_claims)
    monkeypatch.setattr("bormochecker.pipeline.check", lambda *args, **kwargs: err_report)
    res = verify_digest_blocks([{"draft_text": text}])
    assert res[0]["verified"] is False
    assert "reason" in res[0]


def test_verify_digest_blocks_clean(monkeypatch):
    # 4. Чистый отчёт
    clean_report = _make_report(status="ok")
    monkeypatch.setattr("bormochecker.pipeline.check", lambda *args, **kwargs: clean_report)
    res = verify_digest_blocks([{"draft_text": "Текст блока новости."}])
    assert res[0]["verified"] is True
    assert res[0]["needs_rewrite"] is False
