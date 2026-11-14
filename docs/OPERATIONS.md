# Эксплуатация BormoChecker: Обновление локального поискового индекса

## 1. Назначение

Локальный поисковый индекс `bormochecker/data/mirrors_fts.sqlite` строится по зеркалам официальных сайтов и базе ФРП (путь задаётся `FACTCHECK_MIRRORS_DIR`). Индекс использует единый нормализатор `bormochecker.textnorm.normalize_text` (`normalizer_version = 1.0`, зафиксированный в таблице `index_metadata`) и обеспечивает быстрый (0.01–0.03 с) поиск без сети и без внешних затрат (Ступень 0).

По мере обновления нормативных документов на источниках индекс требует периодической переиндексации.

---

## 2. Алгоритм безопасного обновления (`bormochecker/scripts/update_mirrors_index.py`)

Чтобы предотвратить повреждение рабочей базы или выпадение документов из-за сбоев при чтении:
1. **Сборка во временный файл:** Новая база формируется в `mirrors_fts.sqlite.tmp.sqlite`.
2. **Проверка объёма документов:** Новое количество проиндексированных документов сравнивается с текущей рабочей базой. Если документов стало меньше 90% (`min_ratio = 0.90`), обновление аварийно прерывается, а временный файл удаляется.
3. **Пробный поисковый запрос и сверка метаданных:** Проверяется `PRAGMA integrity_check`, наличие `normalizer_version` в `index_meta` и выполняется тестовый полнотекстовый FTS-запрос (`SELECT count(*) FROM docs_fts WHERE docs_fts MATCH ...`). При ошибках синтаксиса или повреждении FTS5 обновление блокируется.
4. **Атомарная ротация:**
   - Текущая база копируется в `mirrors_fts.sqlite.prev.sqlite` (хранение предыдущей версии для быстрого отката).
   - Временный файл перемещается через `os.replace` на место `mirrors_fts.sqlite`.

---

## 3. Ручной запуск обновления и проверки

Для ручного запуска пересборки:
```bash
cd /opt/bormochecker
.venv/bin/python -m bormochecker.scripts.update_mirrors_index
```

Для проверки целостности существующего индекса без пересборки:
```bash
.venv/bin/python -m bormochecker.scripts.update_mirrors_index --verify-only
```

Опции:
- `--mirror-dir`: путь к каталогу зеркал (по умолчанию `FACTCHECK_MIRRORS_DIR` или `data/site_mirrors`).
- `--db-path`: целевой файл базы SQLite (по умолчанию `bormochecker/data/mirrors_fts.sqlite`).
- `--min-ratio 0.90`: порог допустимого изменения объёма (по умолчанию 90%).
- `--verify-only`: проверить существующий индекс (`integrity_check`, `normalizer_version`, пробный FTS-запрос) и завершиться.
- `--force`: принудительное обновление, игнорируя падение объёма документов.

---

## 4. Автоматизация через systemd timer

В каталоге `deploy/systemd/` подготовлены юнит-файлы:
- `factcheck-update-index.service` — однократный запуск скрипта обновления.
- `factcheck-update-index.timer` — еженедельный запуск по воскресеньям в 03:00.

> ⚠️ **ВНИМАНИЕ:** Включение и запуск сервисов и таймеров на рабочем сервере выполняется после проверки конфигурации администратором.

Порядок активации:
```bash
sudo cp deploy/systemd/factcheck-update-index.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now factcheck-update-index.timer
```

Проверка статуса таймера:
```bash
systemctl list-timers 'factcheck-update-index*'
```
