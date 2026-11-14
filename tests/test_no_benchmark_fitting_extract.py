from __future__ import annotations

import re

from bormochecker.paths import opinion_markers_path, repo_root


def test_no_benchmark_fitting_words_in_extract():
    extract_py = repo_root() / "bormochecker" / "extract.py"
    content = extract_py.read_text(encoding="utf-8")
    # Проверяем отсутствие подогнанных слов из кейсов
    for forbidden in ["драйв", "душевност", "хиты", "прекрасн", "лучш"]:
        assert not re.search(forbidden, content, re.IGNORECASE), f"Найдено слово {forbidden} в extract.py"


def test_opinion_markers_file_exists_and_general():
    path = opinion_markers_path()
    assert path.is_file()
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert len(lines) >= 10
    # Общие оценочные слова из словаря
    assert "великолепный" in lines
    assert "потрясающий" in lines
    # Нет слов из эталонных кейсов
    assert "драйв" not in lines
    assert "хиты" not in lines
