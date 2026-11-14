"""Клиент модели: OpenAI-совместимый /v1/chat/completions, каждый вызов учитывается в Meter.

Локальный стек Gemma 4 MTP на эндпоинтах llama.cpp:
  тяжёлая  :11440  gemma4-26b-mtp — извлечение утверждений и вынесение вердиктов
  лёгкая   :11441  gemma4-e4b-mtp — второй проход gaps(light=True), NLI-аудит и OCR
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

from bormochecker.costs import Meter
from bormochecker.log_masking import install_secret_masker

logger = logging.getLogger(__name__)
install_secret_masker(logger)


class LLMError(RuntimeError):
    pass


def parse_llm_json(raw: str) -> Any:
    """Защитный парсер JSON для Gemma 4 MTP с очисткой управляющих символов и обёрток."""
    if not raw:
        raise ValueError("Empty LLM response")
    cleaned = raw.strip()
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 1. Поиск блока кода ```json ... ```
    code_blocks = re.findall(r"```(?:json)?\s*([\{\[][\s\S]*?[\}\]])\s*```", cleaned, re.IGNORECASE)
    for cb in reversed(code_blocks):
        try:
            return json.loads(cb.strip())
        except json.JSONDecodeError:
            continue

    # 2. Поиск JSON после <thought> или "Thinking Process:"
    thought_parts = re.split(r"(?:</thought>|\n\n(?=[{\[]))", cleaned)
    for part in reversed(thought_parts):
        p_clean = part.strip()
        if p_clean.startswith("```"):
            p_clean = re.sub(r"^```(?:json)?\s*", "", p_clean, flags=re.IGNORECASE)
            p_clean = re.sub(r"\s*```$", "", p_clean)
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            s = p_clean.find(open_ch)
            e = p_clean.rfind(close_ch)
            if s != -1 and e > s:
                try:
                    return json.loads(p_clean[s : e + 1])
                except json.JSONDecodeError:
                    continue

    # 3. Общий fallback по первой/последней скобке
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Unparseable JSON from LLM: {raw[:240]}")


parse_json = parse_llm_json


def _resolve_endpoint(light: bool = False) -> tuple[str, str, str]:
    if light:
        base_url = (
            os.environ.get("FACTCHECK_LIGHT_LLM_URL")
            or os.environ.get("LLM_LIGHT_BASE_URL")
            or "http://127.0.0.1:11441/v1"
        ).rstrip("/")
        model = (
            os.environ.get("FACTCHECK_LIGHT_LLM_MODEL")
            or os.environ.get("LLM_LIGHT_MODEL")
            or "gemma4-e4b-mtp"
        )
        api_key = (
            os.environ.get("FACTCHECK_LIGHT_LLM_API_KEY")
            or os.environ.get("LLM_LIGHT_API_KEY")
            or "sk-local"
        )
    else:
        base_url = (
            os.environ.get("FACTCHECK_LLM_URL")
            or os.environ.get("LLM_BASE_URL")
            or "http://127.0.0.1:11440/v1"
        ).rstrip("/")
        model = (
            os.environ.get("FACTCHECK_LLM_MODEL")
            or os.environ.get("LLM_MODEL")
            or "gemma4-26b-mtp"
        )
        api_key = (
            os.environ.get("FACTCHECK_LLM_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or "sk-local"
        )
    return base_url, model, api_key


class LLM:
    """Единый клиент LLM для Gemma 4 MTP со строгой обработкой finish_reason и json_schema."""

    def __init__(
        self,
        *,
        url: str | None = None,
        model: str | None = None,
        light_url: str | None = None,
        light_model: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        def_base, def_model, def_key = _resolve_endpoint(light=False)
        def_l_base, def_l_model, def_l_key = _resolve_endpoint(light=True)
        self.base_url = (url or def_base).rstrip("/")
        self.model = model or def_model
        self.api_key = def_key
        self.light_url = (light_url or def_l_base).rstrip("/")
        self.light_model = light_model or def_l_model
        self.light_api_key = def_l_key
        self.timeout = timeout or float(os.environ.get("FACTCHECK_LLM_TIMEOUT") or 90)
        self._client = client or httpx.Client(timeout=self.timeout)

    def chat(
        self,
        meter: Meter,
        messages: list[dict],
        *,
        light: bool = False,
        temperature: float = 0.0,
        top_p: float = 0.9,
        max_tokens: int = 4096,
        timeout: float | None = None,
        retries: int = 1,
        response_format: dict | None = None,
        deadline: Any | None = None,
        seed: int | None = None,
    ) -> str:
        req_timeout = timeout if timeout is not None else self.timeout
        base_url = self.light_url if light else self.base_url
        model_name = self.light_model if light else self.model
        key = self.light_api_key if light else self.api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}

        current_max_tokens = max_tokens
        length_retried = False
        last_error = "нет попыток"

        if deadline is not None and deadline.expired():
            raise LLMError("Бюджет времени исчерпан (Deadline expired)")

        attempt = 0
        max_attempts = retries + 1
        while attempt < max_attempts:
            if deadline is not None:
                rem = deadline.remaining()
                if attempt > 0 and rem < 0.5:
                    break
                attempt_timeout = min(req_timeout, rem)
            else:
                attempt_timeout = req_timeout

            body: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": current_max_tokens,
                "cache_prompt": True,
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
                "thinking_budget_tokens": 0,
            }
            if seed is not None:
                body["seed"] = seed

            if response_format is not None:
                body["response_format"] = response_format

            extra_str = os.environ.get("LLM_LIGHT_EXTRA_BODY" if light else "LLM_EXTRA_BODY")
            if extra_str:
                try:
                    extra_body = json.loads(extra_str)
                    if isinstance(extra_body, dict):
                        body |= extra_body
                except json.JSONDecodeError:
                    pass

            t0 = time.monotonic()
            try:
                r = self._client.post(
                    f"{base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=attempt_timeout,
                )
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]
                finish_reason = choice.get("finish_reason")
                msg = choice["message"]
                content = (msg.get("content") or "").strip()
                reasoning = (msg.get("reasoning_content") or "").strip()

                if finish_reason == "length":
                    if not length_retried:
                        length_retried = True
                        max_attempts += 1
                        current_max_tokens = min(current_max_tokens * 2, 8192)
                        logger.warning(
                            "Ответ LLM обрезан по длине (finish_reason=length), повтор с max_tokens=%d",
                            current_max_tokens,
                        )
                        meter.add_llm(data.get("usage"), retry=True, seconds=time.monotonic() - t0)
                        attempt += 1
                        continue
                    raise LLMError(f"Ответ модели обрезан по длине (finish_reason=length, лимит {current_max_tokens})")

                if not content:
                    if reasoning:
                        logger.warning("LLM вернула reasoning_content вместо content: %s", reasoning[:200])
                        raise LLMError("Основное поле content пусто: модель вернула reasoning_content вместо ответа")
                    raise LLMError(f"Пустой ответ модели, finish_reason={finish_reason}")

                meter.add_llm(data.get("usage"), retry=attempt > 0, seconds=time.monotonic() - t0)
                return content
            except (httpx.HTTPError, ValueError, KeyError, IndexError, LLMError) as e:
                meter.add_llm({"prompt_tokens": 0, "completion_tokens": 0}, retry=attempt > 0, seconds=time.monotonic() - t0)
                last_error = f"{type(e).__name__}: {e}"
                if isinstance(e, LLMError) and ("reasoning_content" in str(e) or "finish_reason=length" in str(e)):
                    raise
                attempt += 1
                continue

        raise LLMError(f"модель не ответила после попыток: {last_error}")

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        max_tokens: int = 4096,
        light: bool = False,
        meter: Meter | None = None,
        deadline: Any | None = None,
    ) -> Any:
        m = meter or Meter()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            },
        }
        raw_text = self.chat(
            m,
            messages,
            light=light,
            max_tokens=max_tokens,
            response_format=response_format,
            deadline=deadline,
        )
        return parse_llm_json(raw_text)

    def check_health(self, timeout: float = 2.0) -> bool:
        """Проверка доступности модели (GET /health или GET /v1/models)."""
        base = self.base_url
        root_url = base[:-3] if base.endswith("/v1") else base
        for url in (f"{root_url}/health", f"{base}/models"):
            try:
                r = self._client.get(url, timeout=timeout)
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        return False



_default_client = LLM()


def chat(
    meter: Meter,
    messages: list[dict],
    *,
    light: bool = False,
    temperature: float = 0.0,
    top_p: float = 0.9,
    max_tokens: int = 4096,
    timeout: float = 240,
    retries: int = 1,
    response_format: dict | None = None,
    deadline: Any | None = None,
    seed: int | None = None,
) -> str:
    """Текст ответа модели через единый клиент LLM."""
    return _default_client.chat(
        meter,
        messages,
        light=light,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        timeout=timeout,
        retries=retries,
        response_format=response_format,
        deadline=deadline,
        seed=seed,
    )


def check_health(timeout: float = 2.0) -> bool:
    """Проверка доступности модели по умолчанию."""
    return _default_client.check_health(timeout=timeout)

