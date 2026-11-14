# Руководство по эксплуатации: BormoChecker (RUNBOOK)

Сервис автономной верификации фактов, чисел, дат и нормативных документов.

---

## 1. Запуск, остановка и управление сервисом

### 1.1. Локальный запуск (разработка и отладка)

Из корня репозитория с виртуальным окружением `.venv`:

```bash
# Запуск FastAPI приложения через uvicorn
.venv/bin/python -m uvicorn bormochecker.web:app --host 127.0.0.1 --port 8612 --reload
```

- Swagger UI: `http://127.0.0.1:8612/docs`
- Схема OpenAPI: `http://127.0.0.1:8612/openapi.json`
- Healthcheck: `http://127.0.0.1:8612/healthz`
- Readiness: `http://127.0.0.1:8612/readyz`

### 1.2. Запуск под systemd

На сервере сервис может управляться через systemd unit `bormochecker.service`:

```bash
sudo systemctl status bormochecker.service
sudo systemctl start bormochecker.service
sudo systemctl stop bormochecker.service
sudo systemctl restart bormochecker.service
journalctl -u bormochecker.service -f
```

---

## 2. Переменные окружения (Reference Table)

| Переменная | Назначение | Значение по умолчанию | Обязательность |
|---|---|---|---|
| `FACTCHECK_REQUIRE_AUTH` | Требовать авторизацию (`bc_session` или токен) | `"1"` (`"1"` — требовать, `"0"` — открытый доступ) | Опционально |
| `FACTCHECK_GUEST_MODES` | Режимы проверки, разрешённые гостям | `"local_frp,education"` | Опционально |
| `FACTCHECK_INTERNAL_TOKEN` | Сервисный токен доступа (заголовок `X-Factcheck-Token`) | `""` | Рекомендуется |
| `PASSPORT_HMAC_SECRET` | HMAC-секрет проверки cookie `bc_session` | `""` | Для подписанных cookie |
| `FACTCHECK_CORS_ORIGINS` | Дополнительные разрешённые CORS origins (через запятую) | `""` (`localhost` включён) | Опционально |
| `FACTCHECK_BLOCKED_DOMAINS` | Дополнительные домены в SSRF / self-citation стоп-лист | `""` | Опционально |
| `FACTCHECK_BLOCKED_CIDRS` | Дополнительные заблокированные CIDR-подсети (через запятую) | `""` | Опционально |
| `FACTCHECK_BLOCKED_IPS` | Дополнительные заблокированные IP-адреса (через запятую) | `""` | Опционально |
| `FACTCHECK_MAX_QUEUE` | Максимальный размер очереди ожидания | `"5"` | Опционально |
| `FACTCHECK_MAX_BODY_BYTES` | Лимит размера тела запроса (в байтах) | `"1048576"` (1 МБ) | Опционально |
| `FACTCHECK_RATE_LIMIT_RPM` | Лимит запросов в минуту на IP | `"10"` | Опционально |
| `FACTCHECK_MAX_CLAIMS` | Лимит утверждений из одного текста | `"60"` | Опционально |
| `FACTCHECK_TIME_BUDGET_SECONDS` | Общий лимит времени на проверку текста (сек) | `"300"` (5 минут) | Опционально |
| `FACTCHECK_CLAIM_TIME_BUDGET_SECONDS` | Лимит времени на проверку одного утверждения | `"60"` (1 минута) | Опционально |
| `FACTCHECK_LLM_BASE_URL` / `FACTCHECK_LLM_URL` | URL основной (heavy) OpenAI-совместимой LLM | `http://127.0.0.1:11440/v1` | Обязательно |
| `FACTCHECK_LLM_MODEL` | Имя основной модели в llama.cpp / сервере | `gemma4-26b-mtp` | Обязательно |
| `FACTCHECK_LLM_TIMEOUT` | Таймаут вызова модели (сек) | `"90"` | Опционально |
| `FACTCHECK_LIGHT_LLM_URL` | URL лёгкой LLM (`gaps(light=True)`, OCR) | `http://127.0.0.1:11441/v1` | Опционально (fallback на основную) |
| `FACTCHECK_LIGHT_LLM_MODEL` | Имя лёгкой модели | `gemma4-e4b-mtp` | Опционально |
| `SEARXNG_BASE_URL` | URL локального метапоисковика SearXNG | `http://127.0.0.1:8088` | Для режима `education` |
| `YANDEX_CLOUD_API_KEY` | API-ключ Yandex Search API | `""` | Для платного поиска |
| `FACTCHECK_ALLOW_PAID_SEARCH` | Разрешить платный поиск при промахе бесплатного каскада | `"0"` | Опционально |
| `FACTCHECK_OFFLINE` | Полное отключение исходящих HTTP-запросов поиска | `"0"` (`"1"` — только локальные индексы) | Для тестов и ЧС |
| `FACTCHECK_LOCAL_FTS_DB` | Путь к базе SQLite FTS5 нормативных документов | `data/knowledge_fts.sqlite` | Опционально |
| `FACTCHECK_MIRRORS_FTS_DB` | Путь к базе SQLite FTS5 зеркал сайтов | `data/mirrors_fts.sqlite` | Опционально |
| `FACTCHECK_MIRRORS_DIR` | Каталог офлайн-зеркал официальных сайтов | `data/site_mirrors` | Опционально |
| `FACTCHECK_FACT_LEDGER_PATH` | Путь к постоянному реестру фактов | `data/fact_ledger.jsonl` | Опционально |
| `FACTCHECK_CACHE_DB` | Путь к SQLite-кэшу поисковых ответов | `data/factcheck_search_cache.sqlite` | Опционально |
| `FACTCHECK_CACHE_SEARCH_TTL_SECONDS` | Время жизни поисковой выдачи в кэше | `"259200"` (72 часа) | Опционально |
| `FACTCHECK_DIRECT_APIS_ENABLED` | Разрешить прямые обращения к API ведомств | `"1"` | Опционально |

---

## 3. Архитектура и зависимости

1. **LLM Runtime:** OpenAI-совместимые эндпоинты (по умолчанию llama.cpp на `:11440` для тяжёлой модели и `:11441` для лёгкой). Запросы идут на `/v1/chat/completions`.
2. **Локальный поиск SearXNG:** Локальный инстанс на `127.0.0.1:8088` без доступа извне.
3. **Локальные индексы SQLite FTS5:**
   - `knowledge_fts.sqlite` (нормативные акты, образовательные стандарты ФГОС, приказы).
   - `mirrors_fts.sqlite` (зеркала официальных сайтов).
4. **Сетевой периметр:** Защита от SSRF и DNS-rebinding (`SafePinnedTransport` / `assert_safe_http_url` запрещают запросы к loopback, RFC 1918, link-local, `BLOCKED_DOMAINS`, `FACTCHECK_BLOCKED_DOMAINS` и `FACTCHECK_BLOCKED_CIDRS`, кроме явно разрешённых `FACTCHECK_ALLOWED_CIDRS`).

---

## 4. Регламент действий при типовых инцидентах

### 4.1. Сбой: LLM-модель недоступна (503 на `/readyz` или таймауты)

**Симптомы:**
- `GET /readyz` возвращает `503 Service Unavailable`.
- Проверки завершаются ошибкой или зависают по таймауту.

**Диагностика:**
```bash
curl -s http://127.0.0.1:11440/health
nvidia-smi
```

**Действия по устранению:**
1. Перезапустить бэкенд моделей `llama.cpp` / OpenAI-совместимый сервер.
2. Проверить занятость GPU процессами (`fuser -v /dev/nvidia*`).
3. Проверить `/readyz` повторно (`curl -s http://127.0.0.1:8612/readyz`).

---

### 4.2. Сбой: Индекс FTS устарел или повреждён

**Диагностика:**
```bash
sqlite3 data/mirrors_fts.sqlite "PRAGMA integrity_check;"
sqlite3 data/knowledge_fts.sqlite "PRAGMA integrity_check;"
```

**Действия по устранению:**
1. При повреждении базы выполнить откат на резервную копию `mirrors_fts.sqlite.prev.sqlite`.
2. Либо запустить атомарную пересборку индекса зеркал:
   ```bash
   .venv/bin/python -m bormochecker.scripts.update_mirrors_index
   ```
3. Проверить готовность сервиса через `GET /readyz`.

---

### 4.3. Сбой: Очередь переполнена (503)

**Диагностика:**
```bash
curl -s http://127.0.0.1:8612/api/queue
```

**Действия по устранению:**
1. Проверить, не установлен ли флаг `BORMOCHECKER_GPU_LOCKED=1` или lock-файл GPU (`FACTCHECK_GPU_LOCK_PATH`).
2. При зависании воркера перезапустить сервис (`sudo systemctl restart bormochecker.service`).
3. При необходимости временно увеличить очередь через `FACTCHECK_MAX_QUEUE=10`.

---

### 4.4. Сбой: Провайдер веб-поиска недоступен

**Действия по устранению:**
1. Проверить локальный контейнер SearXNG (`curl -s "http://127.0.0.1:8088/search?q=test&format=json" | head -c 100`).
2. Для автономной работы без сети перевести сервис в офлайн-режим:
   ```bash
   export FACTCHECK_OFFLINE=1
   ```

---

### 4.5. Сбой: 429 Too Many Requests или 413 Payload Too Large

1. Ошибка 413: напомнить пользователю об ограничении в 10 000 символов текста или при необходимости скорректировать `FACTCHECK_MAX_BODY_BYTES`.
2. Ошибка 429: по умолчанию действует лимит 10 проверок в минуту на IP/токен (`FACTCHECK_RATE_LIMIT_RPM`).

---

## 5. Процедура отката и верификации (Rollback)

```bash
git log -n 5 --oneline
git checkout <СТАБИЛЬНЫЙ_КОММИТ>
sudo systemctl restart bormochecker.service
.venv/bin/python -m pytest tests/test_openapi_schema.py tests/test_smoke_app.py -v
curl -s http://127.0.0.1:8612/healthz
curl -s http://127.0.0.1:8612/readyz
```
