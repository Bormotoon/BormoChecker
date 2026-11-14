from __future__ import annotations

from bormochecker.evaluate import EVALUATION_SCHEMA, MAX_CANDIDATES
from bormochecker.limits import Settings
import bormochecker.extract as extract


def test_eval_schema_matches_max_candidates():
    # Проверка (а): схема допускает столько кандидатов, сколько MAX_CANDIDATES
    assert MAX_CANDIDATES == 8

    n_schema = (
        EVALUATION_SCHEMA["json_schema"]["schema"]["properties"]["n"]
    )
    allowed_n = n_schema["enum"]
    assert len(allowed_n) == MAX_CANDIDATES + 1
    assert set(allowed_n) == set(range(MAX_CANDIDATES + 1))


def test_chunk_chars_from_settings_only():
    # Проверка (б): константа CHUNK_CHARS удалена из extract.py, единый источник — Settings
    assert not hasattr(extract, "CHUNK_CHARS")
    settings = Settings.from_env()
    assert settings.chunk_chars == 6000
