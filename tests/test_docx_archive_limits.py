import io
import zipfile
import pytest

from bormochecker.retrieval import (
    DecompressionBombError,
    _parse_docx_bytes,
)


def _make_valid_docx() -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as z:
        content = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Заголовок методических рекомендаций</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Текст первого абзаца для проверки.</w:t></w:r></w:p>"
            "<w:tbl>"
            "<w:tr><w:tc><w:p><w:r><w:t>Колонка 1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Колонка 2</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>Данные 1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Данные 2</w:t></w:r></w:p></w:tc></w:tr>"
            "</w:tbl>"
            "</w:body>"
            "</w:document>"
        ).encode("utf-8")
        z.writestr("word/document.xml", content)
    return bio.getvalue()


def test_valid_docx_parses_normally() -> None:
    """Валидный docx распаковывается штатно."""
    raw = _make_valid_docx()
    title, text = _parse_docx_bytes(raw)
    assert "Заголовок методических рекомендаций" in title
    assert "Текст первого абзаца для проверки." in text
    assert "[Таблица: Колонка 1: Данные 1 | Колонка 2: Данные 2]" in text


def test_zip_bomb_high_ratio_rejected() -> None:
    """Zip-бомба (высокий коэффициент сжатия > 100:1) отклоняется с понятной ошибкой."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # 600 КБ одинаковых байт сжимаются в ~700 байт (коэффициент > 800:1)
        z.writestr("word/document.xml", b"0" * 600_000)
    raw = bio.getvalue()

    with pytest.raises(DecompressionBombError, match="коэффициент сжатия"):
        _parse_docx_bytes(raw)


def test_archive_with_too_many_files_rejected() -> None:
    """Архив со слишком большим числом файлов (> 100) отклоняется с понятной ошибкой."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        for i in range(105):
            z.writestr(f"word/part_{i}.xml", b"<xml/>")
        z.writestr("word/document.xml", b"<w:document/>")
    raw = bio.getvalue()

    with pytest.raises(DecompressionBombError, match="слишком много файлов"):
        _parse_docx_bytes(raw)


def test_total_uncompressed_size_limit_rejected() -> None:
    """Архив, суммарный размер файлов которого превышает 10 МБ, отклоняется."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        # 11 файлов по 1 МБ случайных данных (коэффициент ~1:1, но суммарно 11 МБ > 10 МБ)
        import os
        chunk = os.urandom(1024 * 1024)
        for i in range(11):
            z.writestr(f"word/large_{i}.dat", chunk)
        z.writestr("word/document.xml", b"<w:document/>")
    raw = bio.getvalue()

    with pytest.raises(DecompressionBombError, match="превышает 10 МБ"):
        _parse_docx_bytes(raw)
