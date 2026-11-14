"""Тесты шумовых токенов и таблиц (T209 / [Q08])."""
import io
import zipfile
import pytest

from bormochecker.retrieval import (
    UnsafeUrlError,
    _parse_docx_bytes,
    assert_safe_http_url,
    extract_title_and_text,
    is_noise_url,
)


def test_url_noise_tokens():
    # Адрес /authoritative-report не должен фильтроваться подстрокой "auth"
    safe_url = "https://obrnadzor.gov.ru/authoritative-report"
    assert not is_noise_url(safe_url)
    assert assert_safe_http_url(safe_url) == safe_url

    # Адрес /auth/login должен фильтроваться по границам сегмента пути
    noise_url = "https://obrnadzor.gov.ru/auth/login"
    assert is_noise_url(noise_url)
    with pytest.raises(UnsafeUrlError, match="шумовой"):
        assert_safe_http_url(noise_url)


def test_html_authoritative_class_not_filtered():
    html = """
    <html>
      <body>
        <div class="authoritative">Достоверный образовательный факт.</div>
        <div class="auth login">Вход в личный кабинет</div>
      </body>
    </html>
    """
    _, text = extract_title_and_text(html)
    assert "Достоверный образовательный факт." in text
    assert "Вход в личный кабинет" not in text


def test_html_table_with_merged_cell_and_empty_header():
    # 1. Таблица 3x3 с пустой первой ячейкой в шапке (сдвиг колонок предотвращен)
    html_empty_header = """
    <table>
      <tr><th></th><th>Категория</th><th>Цена</th></tr>
      <tr><td>Учебник</td><td>Математика</td><td>100</td></tr>
      <tr><td>Справочник</td><td>Физика</td><td>250</td></tr>
    </table>
    """
    _, text = extract_title_and_text(html_empty_header)
    assert "Цена: 100" in text
    assert "Категория: Математика" in text
    assert "Цена: 250" in text

    # 2. Таблица 3x3 с объединенной ячейкой colspan="2"
    html_colspan = """
    <table>
      <tr><th colspan="2">Описание</th><th>Сумма</th></tr>
      <tr><td>Курс</td><td>Онлайн</td><td>5000</td></tr>
      <tr><td>Семинар</td><td>Очно</td><td>3000</td></tr>
    </table>
    """
    _, text_cs = extract_title_and_text(html_colspan)
    assert "Сумма: 5000" in text_cs
    assert "Сумма: 3000" in text_cs


def test_docx_table_with_merged_cell():
    # Создаем минимальный DOCX в памяти с таблицей 3x3 и w:gridSpan
    xml_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Заголовок документа</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc>
              <w:tcPr><w:gridSpan w:val="2"/></w:tcPr>
              <w:p><w:r><w:t>Наименование и тип</w:t></w:r></w:p>
            </w:tc>
            <w:tc><w:p><w:r><w:t>Количество</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Школа</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Средняя</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Гимназия</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Профильная</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>15</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """.encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml_content)
    raw_docx = buf.getvalue()

    title, text = _parse_docx_bytes(raw_docx)
    assert "Заголовок документа" in title or "Заголовок документа" in text
    assert "Количество: 42" in text
    assert "Количество: 15" in text
