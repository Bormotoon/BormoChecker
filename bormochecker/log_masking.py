"""Модуль маскирования конфиденциальных данных и секретов в журналах (T309)."""

from __future__ import annotations

import logging
import os
import re

# Паттерны для поиска и маскирования конфиденциальных данных в строках
_SECRET_PATTERNS = [
    # Authorization: Bearer / Api-Key
    (re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.+=~]+"), r"\1***"),
    (re.compile(r"(?i)(api-key\s+)[a-zA-Z0-9_\-\.+=~]+"), r"\1***"),
    # URL basic auth: https://user:pass@host
    (re.compile(r"(https?://[^:/@\s]+:)[^@\s/]+(@)"), r"\1***\2"),
    # JSON / Key-Value: "api_key": "...", token=..., password: ...
    (
        re.compile(
            r'(?i)(["\']?(?:api[_-]?key|token|access[_-]?token|refresh[_-]?token|password|secret|app[_-]?password)["\']?\s*[:=]\s*["\']?)([^"\'\s,;&]+)(["\']?)'
        ),
        r"\1***\3",
    ),
    # Query params: ?api_key=... or &secret=...
    (
        re.compile(
            r'(?i)([?&](?:api[_-]?key|token|access[_-]?token|password|secret)=)([^"\'\s&]+)'
        ),
        r"\1***",
    ),
]

_ENV_SECRET_NAMES = (
    "FACTCHECK_INTERNAL_TOKEN",
    "PASSPORT_HMAC_SECRET",
    "YANDEX_API_KEY",
    "YANDEX_CLOUD_API_KEY",
    "TAVILY_API_KEY",
    "SERPAPI_API_KEY",
    "SERPER_API_KEY",
    "FIRECRAWL_API_KEY",
    "OPENAI_API_KEY",
)


def mask_sensitive_data(text: str) -> str:
    """Маскирует секреты, пароли, токены и API-ключи в тексте."""
    if not isinstance(text, str) or not text:
        return text

    masked = text
    for pattern, repl in _SECRET_PATTERNS:
        masked = pattern.sub(repl, masked)

    # Дополнительно маскируем точные значения из переменных окружения, если они длиннее 5 символов
    for env_name in _ENV_SECRET_NAMES:
        val = os.environ.get(env_name, "").strip()
        if len(val) >= 6 and val in masked:
            masked = masked.replace(val, "***")

    return masked


class SecretMaskingFilter(logging.Filter):
    """Фильтр для logging.Handler / Logger, маскирующий чувствительные данные в записях журнала."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = record.getMessage()
                record.args = None
            if isinstance(record.msg, str):
                record.msg = mask_sensitive_data(record.msg)
        except Exception:
            pass
        return True


def install_secret_masker(target_logger: logging.Logger | None = None) -> None:
    """Устанавливает SecretMaskingFilter на указанный логгер или на корневой логгер."""
    log = target_logger or logging.getLogger()
    # Проверяем, не установлен ли уже фильтр
    for f in log.filters:
        if isinstance(f, SecretMaskingFilter):
            return
    log.addFilter(SecretMaskingFilter())
    for handler in log.handlers:
        for f in handler.filters:
            if isinstance(f, SecretMaskingFilter):
                break
        else:
            handler.addFilter(SecretMaskingFilter())
