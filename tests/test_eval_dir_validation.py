import pytest
from bormochecker.scripts.eval_factcheck_benchmark import (
    resolve_eval_dir,
    validate_eval_dir,
)


def test_resolve_eval_dir(tmp_path, monkeypatch):
    # 1. From argument
    custom_dir = tmp_path / "custom_eval"
    assert resolve_eval_dir(custom_dir) == custom_dir.resolve()

    # 2. From environment variable
    monkeypatch.setenv("FACTCHECK_EVAL_DIR", str(custom_dir))
    assert resolve_eval_dir(None) == custom_dir.resolve()

    # 3. Default fallback
    monkeypatch.delenv("FACTCHECK_EVAL_DIR", raising=False)
    default_dir = resolve_eval_dir(None)
    assert default_dir.name == "eval"


def test_validate_eval_dir_missing_items_raises_exit_2(tmp_path):
    empty_dir = tmp_path / "empty_eval"
    empty_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        validate_eval_dir(empty_dir)
    assert exc_info.value.code == 2


def test_validate_eval_dir_empty_items_raises_exit_2(tmp_path):
    eval_dir = tmp_path / "eval_with_empty_items"
    items_dir = eval_dir / "items"
    items_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc_info:
        validate_eval_dir(eval_dir)
    assert exc_info.value.code == 2


def test_validate_eval_dir_with_valid_case_passes(tmp_path):
    eval_dir = tmp_path / "valid_eval"
    case_dir = eval_dir / "items" / "case_01"
    case_dir.mkdir(parents=True)
    (case_dir / "text.txt").write_text("Test case text", encoding="utf-8")

    result = validate_eval_dir(eval_dir)
    assert result == eval_dir
