# Прямые внешние API фактчекинга (Direct APIs)

Реестр прямых API источников для Ступени 1 фактчекинга (`bormochecker/direct_apis.py`).

> **Важно:** Живая проверка внешних адресов и эндпоинтов требует разрешения владельца (**[НУЖНО РАЗРЕШЕНИЕ]**), чтобы исключить несанкционированные сетевые запросы и возможные блокировки. До подтверждения все непроверенные API отключены флагами окружения по умолчанию.

---

## Реестр эндпоинтов

| API / Класс | Адрес (URL) | Источник документации | Дата проверки | Статус | Переменная окружения (по умолчанию) |
|---|---|---|---|---|---|
| **RuWikipediaAPI** | `https://ru.wikipedia.org/w/api.php` | [MediaWiki Action API](https://www.mediawiki.org/wiki/API:Main_page/ru) | 2026-11-11 | Проверен | `FACTCHECK_ENABLE_WIKIPEDIA=1` (включен) |
| **PravoGovAPI** | `https://publication.pravo.gov.ru/api/Documents` | Официальный интернет-портал правовой информации ([publication.pravo.gov.ru](https://publication.pravo.gov.ru/)) | 2026-11-11 | Не проверен | `FACTCHECK_ENABLE_PRAVOGOV=0` (отключен) |
| **FgosReestrAPI** | `https://fgosreestr.edsoo.ru/api/search` | Реестр примерных основных общеобразовательных программ ([fgosreestr.edsoo.ru](https://fgosreestr.edsoo.ru/)) | 2026-11-11 | Не проверен | `FACTCHECK_ENABLE_FGOS=0` (отключен) |
| **CyberLeninkaAPI** | `https://cyberleninka.ru/api/search` | Научная электронная библиотека «КиберЛенинка» ([cyberleninka.ru](https://cyberleninka.ru/)) | 2026-11-11 | Не проверен | `FACTCHECK_ENABLE_CYBERLENINKA=0` (отключен) |
| **MosRuSuggestAPI** | `https://www.mos.ru/api/newsfeed/v4/frontend/json/ru/articles` | Официальный портал Мэра и Правительства Москвы ([mos.ru](https://www.mos.ru/)) | 2026-11-11 | Не проверен | `FACTCHECK_ENABLE_MOSRU=0` (отключен) |

---

## Правила работы и флаги

1. Все эндпоинты строго используют протокол **HTTPS**. Использование незашифрованного `http://` запрещено.
2. Непроверенные API отключены по умолчанию (`0`). Чтобы включить API для тестирования или в рабочей среде, необходимо явно задать соответствующую переменную:
   ```bash
   FACTCHECK_ENABLE_PRAVOGOV=1
   FACTCHECK_ENABLE_FGOS=1
   FACTCHECK_ENABLE_CYBERLENINKA=1
   FACTCHECK_ENABLE_MOSRU=1
   ```
3. Живая проверка доступности эндпоинтов и формата ответов:
   - **[НУЖНО РАЗРЕШЕНИЕ]** владельца репозитория / администратора сети перед проведением сетевых тестов.
   - Запрещено выполнять запросы к доменам из стоп-листа (`BLOCKED_DOMAINS`, `FACTCHECK_BLOCKED_DOMAINS`) и внутренним адресам (`FACTCHECK_BLOCKED_CIDRS`, RFC 1918, loopback).
