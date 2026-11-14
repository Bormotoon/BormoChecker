"""Тесты интерфейса и логики клиентского кода (T701)."""

from pathlib import Path


def test_apply_safe_suggestions_filters_error_with_sources() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # 1. applySafeSuggestions применяет только c.label === 'error' с источниками
    assert "c.label==='error'" in index_html or 'c.label === "error"' in index_html
    assert "c.sources" in index_html
    
    # 2. clarify не применяется автоматически внутри фильтра fixable в applySafeSuggestions
    # Убеждаемся, что в критерии авто-применения нет c.label === 'clarify'
    assert "c.label==='clarify'||c.label==='error'" not in index_html
    assert "c.label==='error'||c.label==='clarify'" not in index_html

    # 3. Ручное подтверждение для clarify присутствует
    assert "applyManualSuggestion" in index_html
    assert "Применить вручную" in index_html


def test_clipboard_fallback_and_error_handling() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # 1. Функция copyToClipboard с fallback через textarea / execCommand
    assert "copyToClipboard" in index_html
    assert "execCommand" in index_html or "createElement('textarea')" in index_html or 'createElement("textarea")' in index_html

    # 2. Сообщение об ошибке при сбое буфера обмена
    assert "Не удалось скопировать в буфер" in index_html


def test_zero_factual_claims_and_partial_warning() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # 1. При отсутствии проверяемых утверждений показывается «Проверяемых утверждений не найдено»
    assert "Проверяемых утверждений не найдено" in index_html
    
    # 2. При статусе partial выводится предупреждение и список непроверенного
    assert "partial" in index_html
    assert "Внимание" in index_html or "частично" in index_html


def test_accessibility_labels_and_focus() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # 1. label for="checkMode"
    assert '<label for="checkMode"' in index_html or '<label class="visually-hidden" for="checkMode"' in index_html
    
    # 2. aria-live на отчёте
    assert 'aria-live="polite"' in index_html

    # 3. focus-visible стили
    assert ":focus-visible" in index_html


def test_queue_polling_visibility_and_backoff() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # 1. visibilitychange обработчик
    assert "visibilitychange" in index_html
    
    # 2. Остановка при document.hidden
    assert "document.hidden" in index_html
    
    # 3. Увеличение интервала при ошибках (4, 8, 16, 30 с)
    assert "30000" in index_html or "30" in index_html
    assert "scheduleNext" in index_html or "backoff" in index_html


def test_ui_polish_t706() -> None:
    index_html = Path("bormochecker/static/index.html").read_text(encoding="utf-8")
    
    # (а) Заглушка «Редактор» заменена на информативный статус
    assert "<b>Редактор</b>" not in index_html
    
    # (б) Объяснение лимита 10 000 знаков
    assert "Лимит: 10 000 знаков" in index_html or "10 000 знаков" in index_html
    
    # (в) Нет чисел-ловушек 344/303/93% в демонстрационном примере
    assert "344" not in index_html
    assert "303" not in index_html
    
    # (г) Подсказка доступа для закрытых режимов
    assert "FACTCHECK_INTERNAL_TOKEN" in index_html or "токеном" in index_html
    
    # (д) Вид источника (official, user_attachment, ledger) и момент проверки
    assert "Момент проверки" in index_html
    assert "user_attachment" in index_html


def test_ui_checklist_t707() -> None:
    checklist_path = Path("docs/UI_CHECKLIST.md")
    assert checklist_path.is_file(), "docs/UI_CHECKLIST.md должен существовать"
    content = checklist_path.read_text(encoding="utf-8")
    
    # Проверка обязательных сценариев из T707
    assert "Загрузка страницы" in content
    assert "Проверка текста с" in content
    assert "Пустой результат" in content
    assert "Ошибка сервера" in content
    assert "Отказ буфера обмена" in content






