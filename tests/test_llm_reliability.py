from __future__ import annotations

import json
import httpx
import pytest

from bormochecker.costs import Meter
from bormochecker.llm import LLM, LLMError


def test_llm_retries_on_length_finish_reason():
    calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        req_body = json.loads(request.content)
        calls.append(req_body)
        if len(calls) == 1:
            # Первый вызов — обрезано по длине
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"items": ['},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                },
            )
        # Второй вызов (повтор) — полный успешный ответ
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"items": ["done"]}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    llm = LLM(client=mock_client)
    meter = Meter()

    result = llm.chat(meter, [{"role": "user", "content": "test"}], max_tokens=100)
    assert result == '{"items": ["done"]}'
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 100
    assert calls[1]["max_tokens"] == 200  # max_tokens удвоен на повторе
    assert calls[0]["enable_thinking"] is False


def test_llm_raises_on_persistent_length_truncation():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"partial": true'},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    llm = LLM(client=mock_client)
    meter = Meter()

    with pytest.raises(LLMError, match="finish_reason=length"):
        llm.chat(meter, [{"role": "user", "content": "test"}], max_tokens=50)


def test_llm_raises_on_reasoning_content_instead_of_content():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "",
                            "reasoning_content": "I am analyzing this question step by step...",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 10},
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    llm = LLM(client=mock_client)
    meter = Meter()

    with pytest.raises(LLMError, match="reasoning_content"):
        llm.chat(meter, [{"role": "user", "content": "test"}])
