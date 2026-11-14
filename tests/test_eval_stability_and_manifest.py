"""Тесты устойчивости результата, детерминизма источников и манифеста (T508)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from bormochecker import eval_manifest, evaluate, limits, llm
from bormochecker.sources import Doc, Sources


def test_llm_chat_passes_seed_and_zero_temperature() -> None:
    """Вызовы оценки передают temperature=0.0 и seed в тело запроса."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"verdict": "supported", "explanation": "ok"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_client.post.return_value = mock_resp

    test_llm = llm.LLM(client=mock_client)
    meter = MagicMock()

    test_llm.chat(
        meter,
        [{"role": "user", "content": "test"}],
        temperature=0.0,
        seed=123,
    )

    assert mock_client.post.called
    call_kwargs = mock_client.post.call_args[1]
    body = call_kwargs.get("json", {})
    assert body.get("temperature") == 0.0
    assert body.get("seed") == 123


def test_evaluate_ask_uses_temperature_zero_and_seed() -> None:
    """_ask в evaluate использует temperature=0.0 и seed."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"verdict": "supported"}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    mock_client.post.return_value = mock_resp

    meter = MagicMock()
    test_llm = llm.LLM(client=mock_client)
    orig_client = evaluate.llm._default_client
    try:
        evaluate.llm._default_client = test_llm
        evaluate._ask(meter, "prompt", max_tokens=100, timeout=10.0)
    finally:
        evaluate.llm._default_client = orig_client

    assert mock_client.post.called
    body = mock_client.post.call_args[1].get("json", {})
    assert body.get("temperature") == 0.0
    assert "seed" in body
    assert isinstance(body["seed"], int)


def test_sources_pool_is_sorted_deterministically() -> None:
    """sources.pool() сортирует источники детерминированно (официальные первыми, затем по url)."""
    meter = MagicMock()
    s = Sources(meter, "текст", network_allowed=False)

    doc_b = Doc(id="b", url="https://b.ru", title="B", text="Текст B", kind="web")
    doc_a = Doc(id="a", url="https://a.ru", title="A", text="Текст A", kind="web")
    doc_off_z = Doc(id="z_gov", url="https://z.gov.ru", title="Z", text="Текст Z", kind="official")
    doc_off_a = Doc(id="a_gov", url="https://a.gov.ru", title="A gov", text="Текст A gov", kind="official")

    # Добавляем в хаотичном порядке
    s._docs["https://b.ru"] = doc_b
    s._docs["https://a.ru"] = doc_a
    s._docs["https://z.gov.ru"] = doc_off_z
    s._docs["https://a.gov.ru"] = doc_off_a

    pool_urls = [d.url for d in s.pool()]
    # Официальные первыми по алфавиту url, затем обычные по алфавиту url
    assert pool_urls == [
        "https://a.gov.ru",
        "https://z.gov.ru",
        "https://a.ru",
        "https://b.ru",
    ]


def test_find_candidates_sorts_deterministically() -> None:
    """find_candidates сортирует кандидатов с одинаковым баллом детерминированно."""
    doc1 = Doc(id="doc1", url="https://z.ru/doc1", title="Z", text="Факт 2026 года в Москве.", kind="web")
    doc2 = Doc(id="doc2", url="https://a.ru/doc2", title="A", text="Факт 2026 года в Питере.", kind="web")

    res = evaluate.find_candidates("Факт 2026 года", [doc1, doc2])
    assert len(res) == 2
    # doc2 (a.ru) должен идти перед doc1 (z.ru) при равных баллах
    assert res[0]["doc"].url == "https://a.ru/doc2"
    assert res[1]["doc"].url == "https://z.ru/doc1"



def test_manifest_records_model_params_temperature_and_seed() -> None:
    """build_manifest сохраняет temperature и seed в model_params."""
    now = datetime.now(timezone.utc)
    manifest = eval_manifest.build_manifest(
        model="test-model",
        provider="test-provider",
        mode="test",
        reference_datetime="2026-09-26T12:00:00Z",
        eval_dir=Path("/tmp"),
        start_time=now,
        end_time=now,
    )
    assert "temperature" in manifest.model_params
    assert manifest.model_params["temperature"] == 0.0
    assert "seed" in manifest.model_params
    assert manifest.model_params["seed"] == 42


def test_limits_has_eval_temperature_and_seed() -> None:
    """Настройки содержат eval_temperature и eval_seed."""
    cfg = limits.Settings.from_env()
    assert hasattr(cfg, "eval_temperature")
    assert cfg.eval_temperature == 0.0
    assert hasattr(cfg, "eval_seed")
    assert cfg.eval_seed == 42
