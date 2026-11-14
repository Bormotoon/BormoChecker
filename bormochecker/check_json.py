"""Run a JSON input file through the same pipeline as the web form.

uv run --env-file .env python -m bormochecker.check_json input.json --output runs/report.json
Input: {"text":"original news", "note":"editorial context"}.
"""

import argparse
from pathlib import Path

from bormochecker.contract import CheckMode, CheckRequest
from bormochecker.pipeline import check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument(
        '--mode',
        choices=[m.value for m in CheckMode],
        default=None,
        help="Режим проверки (по умолчанию из input JSON или education_searxng)",
    )
    parser.add_argument('--strict', action='store_true', help='return 1 for partial reports too')
    args = parser.parse_args()
    request = CheckRequest.model_validate_json(args.input.read_text(encoding='utf-8'))
    mode = CheckMode(args.mode) if args.mode else request.mode
    report = check(
        request.text,
        request.note,
        mode=mode,
        reference_datetime=request.reference_datetime,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(), encoding='utf-8')
    return 1 if report.status == 'failed' or (args.strict and report.status == 'partial') else 0


if __name__ == '__main__':
    raise SystemExit(main())
