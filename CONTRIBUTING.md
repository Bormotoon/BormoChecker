# Contributing to BormoChecker

Thank you for your interest in contributing to **BormoChecker**!

## Development Setup

Requires **Python 3.11+**:

```bash
git clone https://github.com/Bormotoon/BormoChecker.git
cd BormoChecker
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Verification Before Opening a Pull Request

Every pull request must pass the same checks enforced by [CI](.github/workflows/ci.yml):

```bash
python -m compileall -q bormochecker tests
ruff check bormochecker tests
pytest tests -q -m "not network and not gpu and not live"
```

## Guidelines

- Keep deterministic CPU checks (`derived.py`, `numerals.py`, `evaluate.py` guards) independent from LLM calls.
- Never allow an `error` verdict without a verified contradiction or deterministic CPU arithmetic/calendar failure.
- Preserve strict `Costs` accounting (`None` for unknown tariffs rather than `0.0`).
- Include unit tests in `tests/` for bug fixes and new retrieval/verification rules.
