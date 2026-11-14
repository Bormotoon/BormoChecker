from __future__ import annotations

import re

from bormochecker.evaluate import CONFIRMATION_CHECK_PROMPT, PROMPT as EVAL_PROMPT
from bormochecker.extract import PROMPT as EXTRACT_PROMPT
from bormochecker.paths import repo_root


def test_polysemy_rule_in_prompts():
    # Правило обработки многозначных слов присутствует в промптах
    assert "многозначн" in EXTRACT_PROMPT.lower()
    assert "многозначн" in EVAL_PROMPT.lower()
    assert "многозначн" in CONFIRMATION_CHECK_PROMPT.lower()


def test_no_hardcoded_snyali_condition_in_code():
    # Проверяем, что в рабочем коде factcheck/extract.py, evaluate.py, derived.py, origin.py
    # нет условий/веток на конкретное слово "сняли"
    root = repo_root()
    for py_file in [
        root / "bormochecker" / "extract.py",
        root / "bormochecker" / "evaluate.py",
        root / "bormochecker" / "derived.py",
        root / "bormochecker" / "origin.py",
    ]:
        content = py_file.read_text(encoding="utf-8")
        # Не должно быть условий или списков с проверкой "сняли"/"снял"
        assert not re.search(r"[\"']сняли?[\"']", content)
