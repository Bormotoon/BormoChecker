"""Контроль качества на открытых примерах по разбору заказчика.

    uv run --env-file .env python -m bormochecker.bench [dabro|moose]

Эталон — task/examples/04_Разбор_учебных_примеров_для_контроля_качества.md. Каждая проверка —
фрагмент исходника и допустимые для него метки. «must» — то, что заказчик назвал обязательным
(провал = код выхода 1), «should» — «желательно заметить» и «не создавать лишних сомнений».
Фрагмент без единой метки — провал: по ТЗ размечаются все фактические утверждения.
"""

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bormochecker.paths import eval_dir, repo_root
from bormochecker.contract import CheckMode, Label, Report
from bormochecker.pipeline import check

REPO_ROOT = repo_root()

BENCHMARK_DATETIME = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
NOW = f"Момент проверки: {BENCHMARK_DATETIME:%d.%m.%Y}."
EXAMPLES = eval_dir() / "reference_cases"
C, Q, E, O, P = Label.confirmed, Label.clarify, Label.error, Label.opinion, Label.editorial  # noqa: E741 -- однобуквенные псевдонимы меток для таблицы фикстур
NOT_ERROR = {C, Q, O, P}

# (фрагмент, уровень, допустимые метки, за что)
CASES = {
    "dabro": (
        "02_Открытый_пример_Dabro.txt",
        NOW,
        [
            ("1,5 часа без антракта", "must", {E}, "на странице КЦ — 1 ч 40 мин без антракта"),
            ("выпустил новый альбом", "should", {Q, E}, "«Дальше — больше» вышел в 2025, «новый» устарел"),
            ("продолжает записывать песни", "should", {Q}, "синглы 2026 подтверждают релизы, не запись"),
            ("сочетает драйв и душевность", "must", {O, C}, "реклама — оценка, не спор о словах"),
            ("1 октября (это четверг) в 19:00", "must", NOT_ERROR, "дата и время верны"),
            ("1 октября (это четверг) в 19:00", "should", {C}, "подтверждается афишей"),
            ("Билеты от 2500 до 7000 рублей", "must", NOT_ERROR, "цена по афише"),
            ("На концерте прозвучат хиты", "must", NOT_ERROR, "заявленная программа, не сет-лист"),
        ],
    ),
    "moose": (
        "03_Открытый_пример_Лось_и_заяц.txt",
        f"{NOW} Детали эпизода — из видео, присланных редакции; сами видео не передаются.",
        [
            ("сняли ночью возле жилых домов", "must", {P}, "из видео редакции, видео не передано"),
            ("лося — в 11-м микрорайоне", "must", {P}, "из видео редакции"),
            ("зайца — в 20-м", "must", {P}, "из видео редакции"),
            ("Лось пересёк проезжую часть и ушёл в траву", "must", {P}, "из видео редакции"),
            ("заяц сидел возле освещённой дорожки", "must", {P}, "из видео редакции"),
            ("это именно заяц, а не сбежавший домашний кролик", "must", {P, Q}, "без видео не подтвердить"),
            ("В конце августа у этих животных начинается гон", "must", NOT_ERROR, "справка, проверяется поиском"),
            ("В конце августа у этих животных начинается гон", "should", {C}, "подтверждается профильными"),
            ("самцы становятся менее осторожными и могут проявлять агрессию", "should", {C}, "«могут», не «все»"),
            ("здоровый зверь для человека опасности практически не представляет", "must", {C, O}, "«здоровый» ограничивает область"),
            ("не нужно подходить ближе ради фотографии", "should", {C}, "рекомендация подтверждается"),
        ],
    ),
}


def score(report: Report, cases: list) -> list[tuple]:
    """[(ok, уровень, фрагмент, найденные метки, за что)] для каждой проверки."""
    rows = []
    for fragment, level, allowed, why in cases:
        start = report.text.find(fragment)
        assert start >= 0, f"фрагмента нет в тексте примера: {fragment!r}"
        end = start + len(fragment)
        labels = {
            c.label
            for c in report.claims
            if c.start is not None and c.end is not None and c.start < end and c.end > start
        }
        rows.append((bool(labels) and labels <= allowed, level, fragment, labels, why))
    return rows


def _parse_ref_dt(val: datetime | str | None) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str) and val.strip():
        dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return BENCHMARK_DATETIME


def main(
    names: list[str],
    *,
    runs: int = 1,
    output: Path | None = None,
    mode: CheckMode | str = CheckMode.education_searxng,
    reference_datetime: datetime | str | None = None,
) -> int:
    if runs < 1:
        raise ValueError('runs must be positive')
    check_mode = mode if isinstance(mode, CheckMode) else CheckMode(mode)
    ref_dt = _parse_ref_dt(reference_datetime)
    output = output or Path('runs') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=True)
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False).stdout.strip()
    results = []
    failed_must = 0
    total_cost = 0.0
    for iteration, name in ((i, name) for i in range(1, runs + 1) for name in (names or CASES)):
        file, note, cases = CASES[name]
        report = check(
            (EXAMPLES / file).read_text(encoding="utf-8").strip(),
            note,
            reference_datetime=ref_dt,
            mode=check_mode,
        )
        rows = score(report, cases)
        (output / f'{iteration}-{name}.json').write_text(report.model_dump_json(), encoding='utf-8')
        results.append({'run': iteration, 'case': name, 'valid': report.status == 'ok',
                        'must_passed': sum(r[0] for r in rows if r[1] == 'must'),
                        'must_total': sum(r[1] == 'must' for r in rows),
                        'should_passed': sum(r[0] for r in rows if r[1] == 'should'),
                        'should_total': sum(r[1] == 'should' for r in rows),
                        'costs': report.costs.model_dump(mode='json')})
        cost = report.costs.total_cost_usd
        print(f"\n== {name}: status={report.status}, "
              f"цена={'неизвестно' if cost is None else f'{cost * 100:.2f} ¢'}, {report.costs.duration_s} с")
        for ok, level, fragment, labels, why in rows:
            got = ", ".join(sorted(label.value for label in labels)) or "не размечено"
            print(f"  {'✓' if ok else '✗'} {level:6} «{fragment}» → {got}   ({why})")
        for level in ("must", "should"):
            part = [r for r in rows if r[1] == level]
            print(f"  {level}: {sum(r[0] for r in part)}/{len(part)}")
        failed_must += sum(not r[0] for r in rows if r[1] == "must")
        if report.status != "ok":
            print(f"  ⚠ status={report.status} — прогон недействителен: {'; '.join(report.limitations)}")
            failed_must += 1
        total_cost = None if cost is None or total_cost is None else total_cost + cost
    print(f"\nИТОГ: провалено must — {failed_must}; цена всех прогонов — "
          f"{'неизвестно' if total_cost is None else f'{total_cost * 100:.2f} ¢'} (цель ≤ 10 ¢ за проверку)")
    valid = [r['must_passed'] / r['must_total'] for r in results if r['valid'] and r['must_total']]
    summary = {'commit': revision, 'results': results, 'failed_must': failed_must,
               'valid_results': len(valid), 'must_pass_rate':
               {'min': min(valid), 'median': statistics.median(valid), 'max': max(valid)} if valid else None}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'JSON-отчёты: {output}')
    return 1 if failed_must else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('names', nargs='*', choices=tuple(CASES))
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--output', type=Path)
    parser.add_argument(
        '--mode',
        choices=[m.value for m in CheckMode],
        default=CheckMode.education_searxng.value,
        help="Режим проверки (по умолчанию education_searxng)",
    )
    parser.add_argument(
        '--reference-datetime',
        type=str,
        default=None,
        help="Эталонный момент проверки в формате ISO-8601 (по умолчанию 2026-09-26T12:00:00Z)",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    sys.exit(
        main(
            args.names,
            runs=args.runs,
            output=args.output,
            mode=CheckMode(args.mode),
            reference_datetime=args.reference_datetime,
        )
    )
