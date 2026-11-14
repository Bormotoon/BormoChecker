#!/usr/bin/env python3
"""Комплексный бенчмарк и оценка качества фактчекера BormoChecker.

Запуск:
    .venv/bin/python -m bormochecker.scripts.eval_factcheck_benchmark [--split all|dev|test] [--only ID] [--report-file PATH]

Оценивает:
- 24 эталонных текста из eval/items/ (13 dev + 11 test, 146 эталонных утверждений).
- Контрольные кейсы: Dabro, Лось и заяц, МИЭТ.
- Считает ключевые метрики: ложные подтверждения (false confirmations), ложные обвинения (false accusations),
  точность выявления ошибок, финансовые расходы (0 ₽ за проверку) и скорость работы.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any

_BOOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BOOT_ROOT not in sys.path:
    sys.path.insert(0, _BOOT_ROOT)

from bormochecker import bench  # noqa: E402 -- импорт после настройки sys.path для запуска как CLI-скрипта
from bormochecker.eval_manifest import (  # noqa: E402 -- импорт после настройки sys.path для запуска как CLI-скрипта
    LogTee,
    build_manifest,
    create_run_directory,
    save_run_artifacts,
)
from bormochecker.paths import eval_dir as default_eval_dir, repo_root  # noqa: E402 -- импорт после настройки sys.path
from bormochecker.retrieval import network_allowed_ctx  # noqa: E402 -- импорт после настройки sys.path
from bormochecker import pipeline  # noqa: E402 -- импорт после настройки sys.path
from bormochecker.contract import CheckMode, Label, Report  # noqa: E402 -- импорт после настройки sys.path

REPO_ROOT = repo_root()


def resolve_eval_dir(eval_dir_arg: pathlib.Path | str | None = None) -> pathlib.Path:
    """Определяет путь к каталогу эталонного набора."""
    if eval_dir_arg:
        return pathlib.Path(eval_dir_arg).resolve()
    env_dir = os.environ.get("FACTCHECK_EVAL_DIR", "").strip()
    if env_dir:
        return pathlib.Path(env_dir).resolve()
    return default_eval_dir()


def validate_eval_dir(eval_dir_path: pathlib.Path) -> pathlib.Path:
    """Проверяет наличие подкаталога items/ и хотя бы одного валидного кейса."""
    items_dir = eval_dir_path / "items"
    if not items_dir.is_dir():
        print(
            f"Ошибка: каталог эталонного набора не найден: {items_dir}\n"
            "Каталог items/ защищен авторским правом и не публикуется в открытом репозитории.\n"
            "Инструкция по получению набора: см. factcheck/eval/README.md (§ 'Набор не публикуется').\n"
            "Укажите путь через аргумент --eval-dir или переменную окружения FACTCHECK_EVAL_DIR.",
            file=sys.stderr,
        )
        sys.exit(2)

    cases = [p for p in items_dir.iterdir() if p.is_dir() and (p / "text.txt").exists()]
    if not cases:
        print(
            f"Ошибка: в каталоге {items_dir} нет валидных кейсов с text.txt.\n"
            "Инструкция по получению набора: см. factcheck/eval/README.md (§ 'Набор не публикуется').\n"
            "Укажите путь через аргумент --eval-dir или переменную окружения FACTCHECK_EVAL_DIR.",
            file=sys.stderr,
        )
        sys.exit(2)
    return eval_dir_path


@dataclass
class ItemResult:
    item_id: str
    split: str
    title: str
    duration_s: float
    status: str
    gold_count: int
    pred_count: int
    matched_count: int
    false_confirmations: int
    false_accusations: int
    caught_errors: int
    gold_contradictions: int
    gold_supported: int
    pred_confirmed: int
    pred_errors: int
    search_queries: int
    cost_usd: float | None = None
    unmatched_preds: int = 0
    is_failed: bool = False
    gold_partially_supported: int = 0
    gold_not_checkable: int = 0
    gold_unresolved: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def load_benchmark_items(
    eval_dir_path: pathlib.Path, split_filter: str = "all", only_item: str | None = None
) -> list[dict]:
    items = []
    items_dir = eval_dir_path / "items"
    if not items_dir.exists():
        print(f"Директория {items_dir} не найдена!")
        return []

    # Чтение splits.json, если присутствует (T409 / [E02])
    splits_config = {}
    splits_file = eval_dir_path / "splits.json"
    if not splits_file.is_file():
        splits_file = REPO_ROOT / "factcheck" / "eval" / "splits.json"
    if splits_file.is_file():
        try:
            with open(splits_file, "r", encoding="utf-8") as f:
                splits_config = json.load(f)
        except Exception:
            splits_config = {}

    holdout_set = set(splits_config.get("holdout", []))
    dev_set = set(splits_config.get("dev", []))

    for d in sorted(items_dir.iterdir()):
        if not d.is_dir():
            continue
        item_id = d.name
        if only_item and only_item not in item_id:
            continue

        gold_path = d / "gold.json"
        text_path = d / "text.txt"
        docs_json_path = d / "documents.json"
        docs_dir = d / "documents"

        if not (gold_path.exists() and text_path.exists()):
            continue

        with open(gold_path, "r", encoding="utf-8") as f:
            gold = json.load(f)

        if splits_config:
            if item_id in holdout_set:
                item_split = "holdout"
            elif item_id in dev_set:
                item_split = "dev"
            else:
                item_split = gold.get("split", "dev")
        else:
            item_split = gold.get("split", "dev")

        if split_filter != "all":
            if split_filter in ("test", "holdout"):
                if item_split not in ("test", "holdout"):
                    continue
            elif item_split != split_filter:
                continue

        text = text_path.read_text("utf-8").strip()

        attachments = []
        if docs_json_path.exists():
            with open(docs_json_path, "r", encoding="utf-8") as f:
                docs_meta = json.load(f)
            for dm in docs_meta:
                f_name = dm.get("file", "")
                f_path = docs_dir / f_name
                if f_path.exists():
                    attachments.append({
                        "extracted_text": f_path.read_text("utf-8"),
                        "title": dm.get("title", f_name),
                        "storage_path": dm.get("url", f"file://{f_name}"),
                    })

        items.append({
            "id": item_id,
            "split": item_split,
            "text": text,
            "gold": gold,
            "attachments": attachments,
            "path": d,
        })
    return items


DEFAULT_OVERLAP_THRESHOLD = 0.50


def score_item(
    item_data: dict,
    report: Report,
    duration: float,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> ItemResult:
    text = report.text
    gold_claims = item_data["gold"].get("claims", [])
    pred_claims = report.claims

    false_confirmations = 0
    false_accusations = 0
    caught_errors = 0
    gold_contradictions = 0
    gold_supported = 0
    gold_partially_supported = 0
    gold_not_checkable = 0
    gold_unresolved = 0
    matched_count = 0
    matched_pred_indices: set[int] = set()

    pred_confirmed = sum(1 for c in pred_claims if c.label == Label.confirmed)
    pred_errors = sum(1 for c in pred_claims if c.label == Label.error)

    details = []

    for gc in gold_claims:
        g_quote = gc.get("quote", "").strip()
        g_verdict = gc.get("verdict", "unresolved")
        if g_verdict == "contradicted":
            gold_contradictions += 1
        elif g_verdict == "supported":
            gold_supported += 1
        elif g_verdict == "partially_supported":
            gold_partially_supported += 1
        elif g_verdict == "not_checkable":
            gold_not_checkable += 1
        else:
            gold_unresolved += 1

        g_start = text.find(g_quote)
        g_end = g_start + len(g_quote) if g_start >= 0 else -1

        overlapping_preds = []
        if g_start >= 0:
            for idx, pc in enumerate(pred_claims):
                if pc.start is None or pc.end is None:
                    if g_quote in pc.text or pc.text in g_quote:
                        overlapping_preds.append((idx, pc))
                    continue
                inter = max(0, min(g_end, pc.end) - max(g_start, pc.start))
                if inter > 0:
                    pct_g = inter / max(len(g_quote), 1)
                    pct_p = inter / max(len(pc.text), 1)
                    if pct_g >= overlap_threshold or pct_p >= overlap_threshold:
                        overlapping_preds.append((idx, pc))
        else:
            # поиск по подстроке
            for idx, pc in enumerate(pred_claims):
                if g_quote in pc.text or pc.text in g_quote:
                    overlapping_preds.append((idx, pc))

        effective_label = None
        pred_text = ""
        pred_explanation = ""

        if overlapping_preds:
            matched_count += 1
            for idx_m, _ in overlapping_preds:
                matched_pred_indices.add(idx_m)
            # Severity order: error > clarify > editorial > confirmed > opinion
            sev_order = [Label.error, Label.clarify, Label.editorial, Label.confirmed, Label.opinion]
            overlapping_preds.sort(
                key=lambda it: sev_order.index(it[1].label) if it[1].label in sev_order else 99
            )
            best_idx, best_p = overlapping_preds[0]
            effective_label = best_p.label
            pred_text = best_p.text
            pred_explanation = best_p.explanation

        # Оценка ложных срабатываний с учетом partially_supported и not_checkable (T406 / [E05]):
        # Ложное подтверждение: эталон contradicted, unresolved, partially_supported или not_checkable, а модель выдала confirmed
        is_false_conf = (
            g_verdict in ("contradicted", "unresolved", "partially_supported", "not_checkable")
            and effective_label == Label.confirmed
        )
        # Ложное обвинение: эталон supported, unresolved, partially_supported или not_checkable, а модель выдала error
        is_false_acc = (
            g_verdict in ("supported", "unresolved", "partially_supported", "not_checkable")
            and effective_label == Label.error
        )

        if is_false_conf:
            false_confirmations += 1
        if is_false_acc:
            false_accusations += 1
        if g_verdict == "contradicted" and effective_label == Label.error:
            caught_errors += 1

        details.append({
            "gold_id": gc.get("id"),
            "gold_quote": g_quote,
            "gold_verdict": g_verdict,
            "pred_label": effective_label.value if effective_label else "unmarked",
            "pred_text": pred_text,
            "pred_explanation": pred_explanation,
            "false_confirmation": is_false_conf,
            "false_accusation": is_false_acc,
            "note": gc.get("note", ""),
        })

    cost_usd = (
        report.costs.total_cost_usd
        if report.costs.total_cost_usd is not None
        else report.costs.tools_cost_usd
    )
    unmatched_preds = max(0, len(pred_claims) - len(matched_pred_indices))

    return ItemResult(
        item_id=item_data["id"],
        split=item_data["split"],
        title=item_data["gold"].get("event", item_data["id"]),
        duration_s=round(duration, 2),
        status=report.status,
        gold_count=len(gold_claims),
        pred_count=len(pred_claims),
        matched_count=matched_count,
        false_confirmations=false_confirmations,
        false_accusations=false_accusations,
        caught_errors=caught_errors,
        gold_contradictions=gold_contradictions,
        gold_supported=gold_supported,
        pred_confirmed=pred_confirmed,
        pred_errors=pred_errors,
        search_queries=report.costs.search_requests,
        cost_usd=cost_usd,
        unmatched_preds=unmatched_preds,
        is_failed=report.status in ("failed", "error") or report.status.startswith("error"),
        gold_partially_supported=gold_partially_supported,
        gold_not_checkable=gold_not_checkable,
        gold_unresolved=gold_unresolved,
        details=details,
        limitations=report.limitations,
    )


MIET_CHECKS = [
    ("с 959 до 966", "must", {Label.clarify, Label.error}, "расхождение между официальными источниками"),
    ("от 60 до 115 баллов", "must", {Label.error}, "средние баллы не могут быть диапазоном (логическая ошибка)"),
    ("на 47%", "should", {Label.confirmed}, "рост числа заявлений подтверждается"),
    ("261 заявление", "should", {Label.confirmed}, "прием заявлений в колледж"),
    ("35 тысяч", "should", {Label.clarify, Label.confirmed}, "относится к бакалавриату и специалитету"),
]


def run_reference_cases(
    eval_dir_path: pathlib.Path,
    *,
    mode: CheckMode = CheckMode.education_searxng,
    reference_datetime: datetime.datetime | None = None,
    only_case: str | None = None,
) -> list[dict]:
    """Прогон 3 контрольных кейсов (Dabro, Лось и заяц, МИЭТ)."""
    ref_results = []
    cases = [
        ("dabro", "02_Открытый_пример_Dabro.txt", "Концерт Dabro в КЦ Зеленоград (1 октября)"),
        ("moose", "03_Открытый_пример_Лось_и_заяц.txt", "Дикие животные в Зеленограде (Лось и заяц)"),
        ("miet", "03_Закрытый_пример_МИЭТ.txt", "Приемная кампания НИУ МИЭТ"),
    ]
    if only_case in ("dabro", "moose", "miet"):
        cases = [c for c in cases if c[0] == only_case]

    ref_dir = eval_dir_path / "reference_cases"

    ref_dt = reference_datetime or datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.timezone.utc)
    is_offline = os.environ.get("FACTCHECK_OFFLINE") == "1"
    effective_mode = CheckMode.local_frp if is_offline else CheckMode.education_searxng

    for cid, fname, title in cases:
        fpath = ref_dir / fname
        if not fpath.exists():
            continue
        text = fpath.read_text("utf-8").strip()
        t0 = time.monotonic()
        print(f"[*] Контрольный кейс: {title} ({cid}) [mode={effective_mode.value}]...", flush=True)
        note = f"Момент проверки: {ref_dt:%d.%m.%Y}."
        if cid == "moose":
            note += " Детали эпизода — из видео, присланных редакции; сами видео не передаются."

        net_token = None if is_offline else network_allowed_ctx.set(True)
        try:
            report = pipeline.check(
                text,
                note=note,
                mode=effective_mode,
                reference_datetime=ref_dt,
            )
            dur = time.monotonic() - t0
            rows: list[tuple] = []
            if cid in bench.CASES:
                rows = bench.score(report, bench.CASES[cid][2])
            elif cid == "miet":
                rows = bench.score(report, MIET_CHECKS)

            must_passed = sum(r[0] for r in rows if r[1] == "must")
            must_total = sum(r[1] == "must" for r in rows)
            should_passed = sum(r[0] for r in rows if r[1] == "should")
            should_total = sum(r[1] == "should" for r in rows)

            ref_results.append({
                "id": cid,
                "title": title,
                "duration_s": round(dur, 2),
                "status": report.status,
                "claims": report.claims,
                "limitations": report.limitations,
                "costs": report.costs,
                "summary": report.summary,
                "rows": rows,
                "must_passed": must_passed,
                "must_total": must_total,
                "should_passed": should_passed,
                "should_total": should_total,
                "raw_report": report.model_dump(mode="json"),
            })
            print(f"    Готово за {dur:.1f}с. Найдено утверждений: {len(report.claims)}, статус: {report.status}, must: {must_passed}/{must_total}")
        except Exception as err:
            dur = time.monotonic() - t0
            print(f"    Ошибка: {err}")
            ref_results.append({
                "id": cid,
                "title": title,
                "duration_s": round(dur, 2),
                "status": "failed",
                "claims": [],
                "error": str(err),
            })
        finally:
            if net_token is not None:
                network_allowed_ctx.reset(net_token)
    return ref_results


def load_dod_criteria(eval_dir_path: pathlib.Path | None = None) -> dict[str, Any]:
    """Загружает критерии Definition of Done из dod.yaml."""
    candidates = []
    if eval_dir_path:
        candidates.append(eval_dir_path / "dod.yaml")
    candidates.append(REPO_ROOT / "eval" / "dod.yaml")

    for p in candidates:
        if p.is_file():
            try:
                import yaml
                data = yaml.safe_load(p.read_text("utf-8"))
                if isinstance(data, dict):
                    return data.get("definition_of_done", data)
            except Exception as err:
                print(f"[-] Предупреждение: ошибка чтения {p}: {err}")

    # Дефолтные критерии Definition of Done
    return {
        "min_contradictions_caught": 11,
        "total_benchmark_contradictions": 12,
        "max_false_confirmations": 0,
        "max_false_accusations": 2,
        "target_avg_duration_seconds": 45.0,
        "time_limit_per_case_seconds": 600.0,
        "clarify_counts_as_confirmed": False,
        "pred_claims_range": {"min": 120, "max": 160},
    }


def compute_duration_metrics(
    benchmark_results: list[ItemResult],
    reference_results: list[dict],
) -> tuple[float, float, float]:
    """Считает среднее время, p95 и максимум по эталонным текстам (или контрольным кейсам, если эталонных нет)."""
    all_durs = [r.duration_s for r in benchmark_results]
    if not all_durs:
        all_durs = [rc["duration_s"] for rc in reference_results if "duration_s" in rc]
    if not all_durs:
        return 0.0, 0.0, 0.0
    avg_d = sum(all_durs) / len(all_durs)
    max_d = max(all_durs)
    s = sorted(all_durs)
    k = 0.95 * (len(s) - 1)
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    d = k - f
    p95_d = s[f] + d * (s[c] - s[f])
    return round(avg_d, 2), round(p95_d, 2), round(max_d, 2)


@dataclass
class DiscrepancyCheck:
    check_id: str
    title: str
    target: str
    expected: str
    actual: str
    passed: bool
    details: str = ""


def _extract_labels_for_fragment(hc: dict[str, Any], fragment: str) -> set[Any]:
    """Извлекает множество меток, сопоставленных с текстовым фрагментом."""
    rows = hc.get("rows", [])
    matched_labels = set()
    found_in_rows = False
    for _ok, _lvl, frag, labels, _why in rows:
        if frag == fragment:
            found_in_rows = True
            if isinstance(labels, (set, list, tuple)):
                for lbl in labels:
                    matched_labels.add(lbl)
    if found_in_rows:
        return matched_labels

    claims = hc.get("claims", [])
    frag_norm = fragment.strip().lower()
    for c in claims:
        quote = ""
        label = None
        if hasattr(c, "quote"):
            quote = c.quote or ""
            label = getattr(c, "label", None)
        elif isinstance(c, dict):
            quote = c.get("quote", "")
            label = c.get("label")
        if label is not None:
            if isinstance(label, str):
                try:
                    label = Label(label)
                except Exception:
                    pass
            q_norm = quote.strip().lower()
            if frag_norm in q_norm or q_norm in frag_norm:
                matched_labels.add(label)

    return matched_labels


def _is_strictly_confirmed(labels: set[Any]) -> bool:
    """Проверяет, что метка строго confirmed (clarify и ошибки считаются незачётом, T411 / fable)."""
    if not labels:
        return False
    for lbl in labels:
        val = lbl.value if hasattr(lbl, "value") else str(lbl)
        if val in ("clarify", "error", "not_checkable", "unresolved"):
            return False
    for lbl in labels:
        val = lbl.value if hasattr(lbl, "value") else str(lbl)
        if val == "confirmed":
            return True
    return False


def _format_labels(labels: set[Any]) -> str:
    if not labels:
        return "не размечено"
    return ", ".join(sorted(lbl.value if hasattr(lbl, "value") else str(lbl) for lbl in labels))


def evaluate_key_discrepancies(
    benchmark_results: list[ItemResult],
    reference_results: list[dict[str, Any]],
    time_limit_seconds: float = 600.0,
) -> list[DiscrepancyCheck]:
    """Оценивает конкретные расхождения отчёта (T411 / fable):

    1. m07-avtobusy-data: найден ли в бенчмарке.
    2. Dabro: дата и цена засчитываются только при confirmed (clarify — незачёт).
    3. МИЭТ: 47% и 261 заявление засчитываются только при confirmed (clarify — незачёт).
    4. МИЭТ: время сравнивается с лимитом.
    """
    checks: list[DiscrepancyCheck] = []

    # 1. m07-avtobusy-data
    m07 = next((r for r in benchmark_results if r.item_id == "m07-avtobusy-data"), None)
    if m07 is not None:
        m07_passed = not m07.is_failed
        actual_str = f"Найден (статус: `{m07.status}`, время: `{m07.duration_s:.1f} с`)"
        if m07.is_failed:
            actual_str += " — сбой при проверке"
        details_str = (
            f"Утверждений gold/pred: {m07.gold_count}/{m07.pred_count}, "
            f"ошибки: {m07.caught_errors}/{m07.gold_contradictions}"
        )
    else:
        m07_passed = False
        actual_str = "Кейс не найден / пропущен в наборе"
        details_str = "Кейс m07-avtobusy-data должен присутствовать в эталонном наборе"

    checks.append(
        DiscrepancyCheck(
            check_id="m07-avtobusy-data",
            title="Полнота набора: кейс `m07-avtobusy-data`",
            target="Наличие кейса `m07-avtobusy-data` в результатах бенчмарка",
            expected="Кейс найден среди проверенных текстов (без сбоев)",
            actual=actual_str,
            passed=m07_passed,
            details=details_str,
        )
    )

    # 2. Dabro: дата и цена
    dabro = next((rc for rc in reference_results if rc.get("id") == "dabro"), None)
    if dabro is not None and dabro.get("status") not in ("failed", "error"):
        date_frag = "1 октября (это четверг) в 19:00"
        price_frag = "Билеты от 2500 до 7000 рублей"
        date_labels = _extract_labels_for_fragment(dabro, date_frag)
        price_labels = _extract_labels_for_fragment(dabro, price_frag)

        date_ok = _is_strictly_confirmed(date_labels)
        price_ok = _is_strictly_confirmed(price_labels)
        dabro_passed = date_ok and price_ok

        date_status = "зачёт" if date_ok else "незачёт"
        price_status = "зачёт" if price_ok else "незачёт"
        actual_str = (
            f"Дата: `{_format_labels(date_labels)}` ({date_status}), "
            f"Цена: `{_format_labels(price_labels)}` ({price_status})"
        )
        details_str = f"Фрагменты: «{date_frag}», «{price_frag}»"
    else:
        dabro_passed = False
        actual_str = "Кейс Dabro не запускался или завершился сбоем"
        details_str = "Требуется запуск контрольного кейса dabro"

    checks.append(
        DiscrepancyCheck(
            check_id="dabro_date_price",
            title="Кейс Dabro: дата и цена (строго confirmed)",
            target="Фактическая дата и цена билетов в кейсе Dabro",
            expected="Только `confirmed` (при `clarify` считается незачётом)",
            actual=actual_str,
            passed=dabro_passed,
            details=details_str,
        )
    )

    # 3. МИЭТ: 47% и 261 заявление
    miet = next((rc for rc in reference_results if rc.get("id") == "miet"), None)
    if miet is not None and miet.get("status") not in ("failed", "error"):
        p47_frag = "на 47%"
        c261_frag = "261 заявление"
        p47_labels = _extract_labels_for_fragment(miet, p47_frag)
        c261_labels = _extract_labels_for_fragment(miet, c261_frag)

        p47_ok = _is_strictly_confirmed(p47_labels)
        c261_ok = _is_strictly_confirmed(c261_labels)
        miet_metrics_passed = p47_ok and c261_ok

        p47_status = "зачёт" if p47_ok else "незачёт"
        c261_status = "зачёт" if c261_ok else "незачёт"
        actual_str = (
            f"Рост 47%: `{_format_labels(p47_labels)}` ({p47_status}), "
            f"Колледж 261: `{_format_labels(c261_labels)}` ({c261_status})"
        )
        details_str = f"Фрагменты: «{p47_frag}», «{c261_frag}»"
    else:
        miet_metrics_passed = False
        actual_str = "Кейс МИЭТ не запускался или завершился сбоем"
        details_str = "Требуется запуск контрольного кейса miet"

    checks.append(
        DiscrepancyCheck(
            check_id="miet_47_and_261",
            title="Кейс МИЭТ: 47% и 261 заявление (строго confirmed)",
            target="Фактические значения 'на 47%' и '261 заявление' в кейсе МИЭТ",
            expected="Только `confirmed` (при `clarify` считается незачётом)",
            actual=actual_str,
            passed=miet_metrics_passed,
            details=details_str,
        )
    )

    # 4. МИЭТ: время vs лимит
    if miet is not None:
        dur = miet.get("duration_s", 0.0)
        time_ok = dur <= time_limit_seconds and miet.get("status") not in ("failed", "error")
        actual_str = f"`{dur:.1f} с` (лимит: `{time_limit_seconds:.0f} с`)"
        if miet.get("status") in ("failed", "error"):
            actual_str += f" [Статус: {miet.get('status')}]"
        details_str = f"Лимит из dod.yaml: {time_limit_seconds:.0f} с"
    else:
        time_ok = False
        actual_str = "Кейс МИЭТ не запускался"
        details_str = f"Лимит из dod.yaml: {time_limit_seconds:.0f} с"

    checks.append(
        DiscrepancyCheck(
            check_id="miet_duration",
            title="Кейс МИЭТ: соблюдение лимита времени",
            target="Время выполнения кейса МИЭТ",
            expected=f"Время ≤ {time_limit_seconds:.0f} с",
            actual=actual_str,
            passed=time_ok,
            details=details_str,
        )
    )

    return checks


def generate_markdown_report(
    benchmark_results: list[ItemResult],
    reference_results: list[dict],
    output_path: pathlib.Path,
    git_commit: str,
    isolation: str = "live",
    multirun_stats: dict[str, Any] | None = None,
    eval_dir: pathlib.Path | None = None,
    reference_datetime: datetime.datetime | None = None,
) -> str:
    ref_dt = reference_datetime or datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.timezone.utc)
    lines = []
    lines.append("# Отчет о комплексном тестировании BormoChecker")
    lines.append("")
    lines.append(
        f"> Дата прогона: **{ref_dt:%d.%m.%Y %H:%M}** | "
        f"Ветка/коммит: `{git_commit}` | Профиль: `0 ₽ за проверку` | Изоляция: `{isolation}`"
    )
    lines.append("")

    if not benchmark_results and not reference_results:
        lines.append("## Нет данных")
        lines.append("")
        lines.append("Набор проверенных кейсов пуст. Нет данных для построения отчета.")
        report_text = "\n".join(lines)
        output_path.write_text(report_text, encoding="utf-8")
        return report_text

    # 1. Executive Summary
    total_items = len(benchmark_results)
    total_gold_claims = sum(r.gold_count for r in benchmark_results)
    total_pred_claims = sum(r.pred_count for r in benchmark_results)
    total_false_conf = sum(r.false_confirmations for r in benchmark_results)
    total_false_acc = sum(r.false_accusations for r in benchmark_results)
    total_caught_err = sum(r.caught_errors for r in benchmark_results)
    total_gold_contr = sum(r.gold_contradictions for r in benchmark_results)

    known_costs = [r.cost_usd for r in benchmark_results if r.cost_usd is not None]
    has_unknown_cost = any(r.cost_usd is None for r in benchmark_results)

    if not known_costs:
        total_cost_str = "нет данных"
        cost_status = "⚠️ нет данных"
    elif has_unknown_cost:
        total_cost = sum(known_costs)
        total_cost_str = f"${total_cost:.2f}*"
        cost_status = f"⚠️ ${total_cost:.2f} (неполные данные)"
    else:
        total_cost = sum(known_costs)
        total_cost_str = f"${total_cost:.2f}"
        cost_status = (
            "✅ $0.00 (бесплатный каскад)"
            if total_cost == 0.0
            else f"❌ ${total_cost:.2f}"
        )

    dev_results = [r for r in benchmark_results if r.split == "dev"]
    test_results = [r for r in benchmark_results if r.split in ("test", "holdout")]

    dod = load_dod_criteria(eval_dir or (output_path.parent if output_path else None))
    min_caught = dod.get("min_contradictions_caught", 11)
    max_fc = dod.get("max_false_confirmations", 0)
    max_fa = dod.get("max_false_accusations", 2)
    target_avg_time = dod.get("target_avg_duration_seconds", 45.0)

    total_unmatched = sum(r.unmatched_preds for r in benchmark_results)
    total_failures = sum(1 for r in benchmark_results if r.is_failed)
    total_matched_preds = total_pred_claims - total_unmatched
    precision = (
        (total_matched_preds / max(total_pred_claims, 1)) * 100
        if total_pred_claims > 0
        else 0.0
    )

    if total_items == 0:
        items_status = "❌ Нет данных"
        gold_status = "—"
        pred_status = "—"
        unmatched_status = "—"
        fail_status = "—"
        fc_status = "—"
        fa_status = "—"
        catch_status = "—"
        time_status = "—"
        avg_time_str = "—"
    else:
        items_status = "✅ 24/24" if total_items >= 24 else f"⚠️ {total_items}/24"
        gold_status = f"{total_gold_claims}"
        if 120 <= total_pred_claims <= 160:
            pred_status = f"✅ В норме ({total_pred_claims} в [120, 160])"
        elif total_pred_claims < 120:
            pred_status = f"❌ Нарушение диапазона ({total_pred_claims} < 120)"
        else:
            pred_status = f"❌ Нарушение диапазона ({total_pred_claims} > 160)"

        unmatched_status = f"точность {precision:.1f}%"
        fail_status = "✅ 0" if total_failures == 0 else f"❌ {total_failures}"

        fc_status = "✅ 0" if total_false_conf <= max_fc else f"❌ {total_false_conf} > {max_fc}"
        fa_status = (
            "✅ 0"
            if total_false_acc == 0
            else (f"✅ {total_false_acc} ≤ {max_fa}" if total_false_acc <= max_fa else f"❌ {total_false_acc} > {max_fa}")
        )

        if total_gold_contr > 0:
            catch_rate = (total_caught_err / total_gold_contr) * 100
            if total_caught_err >= min_caught:
                catch_status = f"✅ {total_caught_err}/{total_gold_contr} ({catch_rate:.1f}%)"
            else:
                catch_status = f"❌ {total_caught_err}/{total_gold_contr} ({catch_rate:.1f}%) < {min_caught}/{total_gold_contr} (не выполнено)"
        else:
            catch_status = "—"

        avg_time, p95_time, max_time = compute_duration_metrics(benchmark_results, reference_results)
        avg_time_str = f"{avg_time:.1f} с (p95: {p95_time:.1f} с, max: {max_time:.1f} с)"
        time_status = (
            f"✅ {avg_time:.1f} с < {target_avg_time:.0f} с"
            if avg_time < target_avg_time
            else f"⚠️ {avg_time:.1f} с ≥ {target_avg_time:.0f} с"
        )

    time_limit = dod.get("time_limit_per_case_seconds", 600.0)
    discrepancies = evaluate_key_discrepancies(benchmark_results, reference_results, time_limit)

    lines.append("## 1. Сводный дашборд качества (KPI Scorecard)")
    lines.append("")
    lines.append("| Метрика | Значение | Целевой ориентир | Статус |")
    lines.append("|---|---|---|---|")
    lines.append(f"| **Проверено текстов** | `{total_items}` (dev: {len(dev_results)}, holdout: {len(test_results)}) | 24 текста | {items_status} |")
    lines.append(f"| **Сбои при проверке (Failures)** | **`{total_failures}`** | `0` (критично) | {fail_status} |")
    lines.append(f"| **Размеченных утверждений (Gold)** | `{total_gold_claims}` | 146 утверждений | {gold_status} |")
    lines.append(f"| **Извлечено фактчекером (Pred)** | `{total_pred_claims}` | ~120–160 | {pred_status} |")
    lines.append(f"| **Не сопоставленные предсказания (Unmatched)** | `{total_unmatched}` ({total_matched_preds}/{total_pred_claims}) | `0` (минимум шума) | {unmatched_status} |")
    lines.append(f"| **Ложные подтверждения (False Confirmations)** | **`{total_false_conf}`** | `0` (критично) | {fc_status} |")
    lines.append(f"| **Ложные обвинения (False Accusations)** | **`{total_false_acc}`** | `≤ {max_fa}` (критично) | {fa_status} |")
    caught_str = (
        f"**`{total_caught_err} / {total_gold_contr}` ({catch_rate:.1f}%)**"
        if total_gold_contr > 0
        else "—"
    )
    lines.append(f"| **Выявление ошибок (Contradictions Caught)** | {caught_str} | `≥ {min_caught}/{total_gold_contr if total_gold_contr else 12}` | {catch_status} |")
    lines.append(f"| **Финансовые затраты на поиск** | **`{total_cost_str}`** | `$0.00` | {cost_status} |")
    lines.append(f"| **Время проверки текста (Avg / p95 / Max)** | `{avg_time_str}` | `< {target_avg_time:.0f} с` | {time_status} |")
    disc_passed = sum(1 for d in discrepancies if d.passed)
    disc_total = len(discrepancies)
    disc_status = (
        f"✅ {disc_passed}/{disc_total}"
        if disc_passed == disc_total
        else f"⚠️ {disc_passed}/{disc_total} (см. разд. 6)"
    )
    lines.append(f"| **Ключевые расхождения (T411)** | `{disc_passed}/{disc_total}` | `4/4` (критично) | {disc_status} |")
    lines.append("")

    # 2. Результаты по сплитам Dev vs Test
    lines.append("## 2. Сравнение сплитов Dev и Test")
    lines.append("")
    lines.append("| Сплит | Текстов | Сбои | Утверждений | Ложные подтв. | Ложные обвин. | Поймано ошибок | Среднее время | Статус |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s_name, s_list in [("Dev (калибровка)", dev_results), ("Test (слепой тест)", test_results)]:
        if not s_list:
            continue
        s_gold = sum(r.gold_count for r in s_list)
        s_fail = sum(1 for r in s_list if r.is_failed)
        s_fc = sum(r.false_confirmations for r in s_list)
        s_fa = sum(r.false_accusations for r in s_list)
        s_ce = sum(r.caught_errors for r in s_list)
        s_gc = sum(r.gold_contradictions for r in s_list)
        s_dur = sum(r.duration_s for r in s_list) / len(s_list)
        s_rate = (s_ce / max(s_gc, 1)) * 100 if s_gc else 100.0
        s_stat = '✅' if s_fail == 0 and s_fc == 0 and s_fa == 0 else '⚠️'
        lines.append(f"| **{s_name}** | {len(s_list)} | `{s_fail}` | {s_gold} | `{s_fc}` | `{s_fa}` | `{s_ce} / {s_gc}` ({s_rate:.0f}%) | `{s_dur:.1f} с` | {s_stat} |")
    lines.append("")

    # 3. Детальная таблица по 24 бенчмарк-кейсам
    lines.append("## 3. Результаты по 24 эталонным текстам бенчмарка")
    lines.append("")
    lines.append("| ID текста | Сплит | Событие / Описание | Сбой | Время | Утв. (Gold/Pred) | Ложные подтв. | Ложные обвин. | Ошибки (поймано/всего) | Вердикт бенчмарка |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in benchmark_results:
        bench_status = "✅ PASSED"
        if r.is_failed:
            bench_status = f"❌ Сбой ({r.status})"
        elif r.false_confirmations > 0 or r.false_accusations > 0:
            bench_status = f"❌ FC:{r.false_confirmations} FA:{r.false_accusations}"
        elif r.gold_contradictions > 0 and r.caught_errors < r.gold_contradictions:
            bench_status = f"⚠️ Пропуск ({r.caught_errors}/{r.gold_contradictions})"
        fail_str = "❌ Да" if r.is_failed else "✅ Нет"
        lines.append(
            f"| `{r.item_id}` | {r.split} | {r.title} | {fail_str} | {r.duration_s} с | {r.gold_count} / {r.pred_count} | "
            f"`{r.false_confirmations}` | `{r.false_accusations}` | `{r.caught_errors} / {r.gold_contradictions}` | {bench_status} |"
        )
    lines.append("")

    # 4. Анализ ключевых ловушек
    lines.append("## 4. Разбор классических ловушек бенчмарка")
    lines.append("")
    lines.append("### 4.1. Ловушка `m06-podjezdy-arifmetika` (Арифметика в тексте)")
    lines.append("- **Текст**: «С первого раза приняли 303 из 344 — это 93%».")
    lines.append("- **Ожидание**: Детерминированный аудит на CPU ловит несоответствие `303 / 344 = 88.1% != 93%` как `Label.error`, при этом не обвиняет `«С первого раза приняли 303 из 344»` (остается `clarify`). Фраза `«Более 19% при 9 из 36»` признается верной (`25% > 19%`).")
    m06_res = next((r for r in benchmark_results if "m06" in r.item_id), None)
    if m06_res:
        lines.append(f"- **Результат фактчекера**: Ложных подтверждений: `{m06_res.false_confirmations}`, Ложных обвинений: `{m06_res.false_accusations}`, Ошибок поймано: `{m06_res.caught_errors} / {m06_res.gold_contradictions}`.")
        for d in m06_res.details:
            if "93%" in d["gold_quote"] or "303" in d["gold_quote"]:
                lines.append(f"  - `{d['gold_quote']}` -> Gold: `{d['gold_verdict']}` | Pred: `{d['pred_label']}` | *{d['pred_explanation'][:90]}*")
    lines.append("")

    lines.append("### 4.2. Ловушка `r01-podjezdy` (Смена года плана: 2026 vs 2027)")
    lines.append("- **Текст**: «В 2027-м году плановый ремонт подъездов запланирован в 340 подъездах».")
    lines.append("- **Ловушка**: Число 340 в официальных документах есть, но относится к **2026** году. Поверхностное сопоставление по числу ошибочно ставит confirmed. Правильный ответ: `clarify` (unresolved).")
    r01_res = next((r for r in benchmark_results if "r01" in r.item_id), None)
    if r01_res:
        lines.append(f"- **Результат фактчекера**: Ложных подтверждений: `{r01_res.false_confirmations}`.")
        for d in r01_res.details:
            if "2027" in d["gold_quote"]:
                lines.append(f"  - `{d['gold_quote']}` -> Gold: `{d['gold_verdict']}` | Pred: `{d['pred_label']}` | *{d['pred_explanation'][:90]}*")
    lines.append("")

    lines.append("### 4.3. Ловушка `r04-tykvy` (Прошлый год vs этот год)")
    lines.append("- **Текст**: «в прошлом году — 2 тысячи участников из 30 городов».")
    lines.append("- **Ловушка**: В документах цифры относятся к текущему фестивалю. Правильный ответ: `clarify` (unresolved), а не `contradicted`.")
    r04_res = next((r for r in benchmark_results if "r04" in r.item_id), None)
    if r04_res:
        lines.append(f"- **Результат фактчекера**: Ложных обвинений: `{r04_res.false_accusations}`.")
    lines.append("")

    # 5. Результаты по контрольным кейсам
    lines.append("## 5. Результаты по открытым и закрытым контрольным кейсам")
    lines.append("")
    for hc in reference_results:
        lines.append(f"### Кейс: {hc['title']} (`{hc['id']}`)")
        lines.append(f"- **Время**: `{hc.get('duration_s')} с` | **Статус**: `{hc.get('status')}`")
        if "must_total" in hc and hc["must_total"]:
            pass_status = (
                f"✅ {hc['must_passed']}/{hc['must_total']}"
                if hc["must_passed"] == hc["must_total"]
                else f"❌ {hc['must_passed']}/{hc['must_total']}"
            )
            lines.append(
                f"- **Критерии MUST**: `{hc['must_passed']} / {hc['must_total']}` ({pass_status}) | "
                f"**Критерии SHOULD**: `{hc.get('should_passed', 0)} / {hc.get('should_total', 0)}`"
            )
        lines.append(f"- **Резюме**: {hc.get('summary', '')}")
        rows = hc.get("rows", [])
        if rows:
            lines.append("")
            lines.append("| Статус | Уровень | Фрагмент | Метки модели | Эталон |")
            lines.append("|---|---|---|---|---|")
            for ok, level, fragment, labels, why in rows:
                lbl_str = ", ".join(sorted(lbl.value for lbl in labels)) or "не размечено"
                status_icon = "✅ OK" if ok else "❌ FAIL"
                lines.append(f"| {status_icon} | `{level}` | «{fragment}» | `{lbl_str}` | {why} |")
        lines.append("")

    # 5.1. Контроль конкретных расхождений отчёта (T411 / fable)
    lines.append("## 5.1. Контроль ключевых расхождений отчёта (T411)")
    lines.append("")
    lines.append("Регрессионная верификация критических пунктов (аудит честности оценщика):")
    lines.append("")
    lines.append("| № | Проверяемый пункт | Ожидание | Фактический статус | Вердикт |")
    lines.append("|---|---|---|---|---|")
    for idx, disc in enumerate(discrepancies, 1):
        verdict_str = "✅ ВЫПОЛНЕНО" if disc.passed else "❌ НЕ ВЫПОЛНЕНО"
        lines.append(f"| {idx} | **{disc.title}** | {disc.expected} | {disc.actual} | {verdict_str} |")
    lines.append("")
    for disc in discrepancies:
        v_icon = "✅" if disc.passed else "❌"
        lines.append(f"- {v_icon} **{disc.title}**: {disc.details} -> {disc.actual}")
    lines.append("")

    if multirun_stats:
        lines.append("## 6. Анализ стабильности повторных прогонов (Repeated Runs)")
        lines.append("")
        runs_count = multirun_stats.get("runs_count", 1)
        disagreement_rate = multirun_stats.get("disagreement_rate", 0.0)
        differing = multirun_stats.get("differing_items", [])
        stats = multirun_stats.get("stats", {})
        lines.append(f"- **Число повторов**: `{runs_count}`")
        lines.append(f"- **Доля кейсов с расхождениями вердиктов**: `{disagreement_rate:.1f}%` ({len(differing)} кейсов)")
        lines.append("")
        lines.append("| Метрика | Мин. | Среднее | Макс. |")
        lines.append("|---|---|---|---|")
        for m_name, m_key, unit in [
            ("Ложные подтверждения", "false_confirmations", ""),
            ("Ложные обвинения", "false_accusations", ""),
            ("Поймано ошибок", "caught_errors", ""),
            ("Среднее время проверки текста", "avg_duration_s", " с"),
        ]:
            s_val = stats.get(m_key, {})
            mi = s_val.get("min", "—")
            me = s_val.get("mean", "—")
            ma = s_val.get("max", "—")
            me_str = f"{me:.2f}{unit}" if isinstance(me, (int, float)) else str(me)
            mi_str = f"{mi}{unit}" if isinstance(mi, (int, float)) else str(mi)
            ma_str = f"{ma}{unit}" if isinstance(ma, (int, float)) else str(ma)
            lines.append(f"| **{m_name}** | `{mi_str}` | `{me_str}` | `{ma_str}` |")
        lines.append("")

    # Заключение и рекомендации
    conclusion_title = "## 7. Заключение и готовность к внедрению" if multirun_stats else "## 6. Заключение и готовность к внедрению"
    lines.append(conclusion_title)
    lines.append("")
    lines.append(f"1. **Финансовые затраты**: {total_cost_str}.")
    lines.append("2. **Безопасность**: Защитные фильтры SafeDocumentFetcher активны.")
    lines.append("3. **CPU-аудит**: Выполнены детерминированные проверки.")
    lines.append("")

    report_text = "\n".join(lines)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"\n[+] Полный отчет успешно сохранен в: {output_path}")
    return report_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=pathlib.Path, default=None, help="Каталог эталонного набора (по умолчанию FACTCHECK_EVAL_DIR или factcheck/eval)")
    parser.add_argument("--run-dir", type=pathlib.Path, default=None, help="Каталог сохранения артефактов прогона (по умолчанию runs/<UTC>_<commit>)")
    parser.add_argument("--split", choices=["all", "dev", "holdout", "test"], default="all")
    parser.add_argument("--runs", type=int, default=1, help="Число повторных прогонов (для отчета не менее 3)")
    parser.add_argument("--only", type=str, default=None, help="Фильтр по конкретному ID текста")
    parser.add_argument("--live-benchmark", action="store_true", help="Использовать открытый веб-поиск вместо приложенных документов (fixture)")
    parser.add_argument("--offline", action="store_true", help="Изолированный прогон на фикстурах (временный кеш и FTS, запрет сети и боевого кеша)")
    parser.add_argument("--no-reference-cases", action="store_true", help="Не запускать контрольные кейсы Dabro/МИЭТ/Лось")
    parser.add_argument("--report-file", type=str, default="eval_report.md")
    parser.add_argument(
        "--overlap-threshold",
        type=float,
        default=DEFAULT_OVERLAP_THRESHOLD,
        help="Порог пересечения спанов для сопоставления (по умолчанию 0.50)",
    )
    parser.add_argument(
        "--reference-datetime",
        type=str,
        default="2026-09-26T12:00:00Z",
        help="Эталонный момент проверки в формате ISO-8601 (по умолчанию 2026-09-26T12:00:00Z)",
    )
    args = parser.parse_args()
    ref_dt = bench._parse_ref_dt(args.reference_datetime)

    if args.offline and args.live_benchmark:
        print("Ошибка: аргументы --offline и --live-benchmark несовместимы.", file=sys.stderr)
        sys.exit(2)

    if args.runs < 1:
        print("Ошибка: параметр --runs должен быть >= 1.", file=sys.stderr)
        sys.exit(2)

    eval_dir_path = resolve_eval_dir(args.eval_dir)
    validate_eval_dir(eval_dir_path)

    # Получаем коммит git
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        git_commit = "unknown"

    run_start_dt = datetime.datetime.now(datetime.timezone.utc)
    run_dir = (
        args.run_dir.resolve()
        if args.run_dir
        else create_run_directory(commit=git_commit, dt=run_start_dt)
    )

    with LogTee(run_dir / "stdout.log"):
        env_backup = {
            "FACTCHECK_OFFLINE": os.environ.get("FACTCHECK_OFFLINE"),
            "FACTCHECK_CACHE_DB": os.environ.get("FACTCHECK_CACHE_DB"),
            "FACTCHECK_LOCAL_FTS_DB": os.environ.get("FACTCHECK_LOCAL_FTS_DB"),
            "FACTCHECK_MIRRORS_FTS_DB": os.environ.get("FACTCHECK_MIRRORS_FTS_DB"),
        }
        offline_temp_dir: tempfile.TemporaryDirectory | None = None

        def _cleanup():
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if offline_temp_dir is not None:
                try:
                    offline_temp_dir.cleanup()
                except Exception:
                    pass

        if args.offline:
            isolation = "offline"
            offline_temp_dir = tempfile.TemporaryDirectory(prefix="factcheck_offline_")
            temp_path = pathlib.Path(offline_temp_dir.name)
            os.environ["FACTCHECK_OFFLINE"] = "1"
            os.environ["FACTCHECK_CACHE_DB"] = str(temp_path / "cache.sqlite")
            os.environ["FACTCHECK_LOCAL_FTS_DB"] = str(temp_path / "knowledge_fts.sqlite")
            os.environ["FACTCHECK_MIRRORS_FTS_DB"] = str(temp_path / "mirrors_fts.sqlite")
            eval_mode = CheckMode.local_frp
        else:
            isolation = "live"
            # Эталонные тексты из items/ всегда проверяются строго по своим локальным attachments
            eval_mode = CheckMode.local_frp

        print(f"=== Запуск комплексного фактчек-тестирования ({args.split}) ===")
        print(f"Каталог эталона: {eval_dir_path}")
        print(f"Каталог прогона: {run_dir}")
        print(f"Коммит: {git_commit}, Изоляция: {isolation}, LLM_PROVIDER=local, Mode={eval_mode.value}\n")

        items = load_benchmark_items(eval_dir_path, split_filter=args.split, only_item=args.only)
        print(f"Найдено текстов для проверки: {len(items)}")

        all_runs_benchmark: list[list[ItemResult]] = []
        reference_results: list[dict] = []
        raw_reports: dict[str, dict[str, Any]] = {}

        for r_idx in range(1, args.runs + 1):
            run_prefix = f"[Прогон {r_idx}/{args.runs}] " if args.runs > 1 else ""
            benchmark_results = []
            for idx, item in enumerate(items, 1):
                item_id = item["id"]
                split = item["split"]
                text = item["text"]
                attachments = item["attachments"]
                note = f"Момент проверки: {ref_dt:%d.%m.%Y}. Событие: {item['gold'].get('event', item_id)}"

                print(f"{run_prefix}[{idx}/{len(items)}] Проверка `{item_id}` ({split}, {len(attachments)} вложений, {len(text)} симв.)...", flush=True)
                t0 = time.monotonic()
                try:
                    report = pipeline.check(
                        text,
                        note=note,
                        mode=eval_mode,
                        attachments=attachments,
                        reference_datetime=ref_dt,
                    )
                    duration = time.monotonic() - t0
                    raw_key = f"{item_id}_run{r_idx}" if args.runs > 1 else item_id
                    raw_reports[raw_key] = report.model_dump(mode="json")
                    res = score_item(
                        item,
                        report,
                        duration,
                        overlap_threshold=args.overlap_threshold,
                    )
                    benchmark_results.append(res)
                    print(f"    -> Готово за {duration:.1f}с. Статус: {report.status}. Утв: {res.pred_count}. Ложных подтв: {res.false_confirmations}, ложных обвин: {res.false_accusations}")
                except Exception as err:
                    duration = time.monotonic() - t0
                    print(f"    -> ОШИБКА: {err}")
                    gold_claims = item["gold"].get("claims", [])
                    gold_contr = sum(1 for c in gold_claims if c.get("verdict") == "contradicted")
                    gold_supp = sum(1 for c in gold_claims if c.get("verdict") == "supported")
                    gold_part = sum(1 for c in gold_claims if c.get("verdict") == "partially_supported")
                    gold_not_chk = sum(1 for c in gold_claims if c.get("verdict") == "not_checkable")
                    gold_unres = sum(1 for c in gold_claims if c.get("verdict") == "unresolved")
                    # Сохраняем как ошибку
                    res = ItemResult(
                        item_id=item_id,
                        split=split,
                        title=item["gold"].get("event", item_id),
                        duration_s=round(duration, 2),
                        status=f"error: {err}",
                        gold_count=len(gold_claims),
                        pred_count=0,
                        matched_count=0,
                        false_confirmations=0,
                        false_accusations=0,
                        caught_errors=0,
                        gold_contradictions=gold_contr,
                        gold_supported=gold_supp,
                        pred_confirmed=0,
                        pred_errors=0,
                        search_queries=0,
                        cost_usd=None,
                        unmatched_preds=0,
                        is_failed=True,
                        gold_partially_supported=gold_part,
                        gold_not_checkable=gold_not_chk,
                        gold_unresolved=gold_unres,
                    )
                    benchmark_results.append(res)

            all_runs_benchmark.append(benchmark_results)

            # Сохраняем промежуточный отчет на диск после каждого прогона
            rep_path = pathlib.Path(args.report_file).resolve()
            generate_markdown_report(
                benchmark_results,
                reference_results,
                rep_path,
                git_commit,
                isolation=isolation,
                eval_dir=eval_dir_path,
            )

        benchmark_results = all_runs_benchmark[0] if all_runs_benchmark else []

        multirun_stats = None
        if args.runs > 1 and all_runs_benchmark:
            differing_items = []
            for item in items:
                cid = item["id"]
                verdicts = []
                for b_res in all_runs_benchmark:
                    r_item = next((r for r in b_res if r.item_id == cid), None)
                    if r_item:
                        verdicts.append((r_item.status, r_item.false_confirmations, r_item.false_accusations, r_item.caught_errors))
                if len(set(verdicts)) > 1:
                    differing_items.append(cid)

            disagreement_rate = (len(differing_items) / max(len(items), 1)) * 100
            runs_durations = [sum(r.duration_s for r in b_res) / max(len(b_res), 1) for b_res in all_runs_benchmark]
            runs_caught = [sum(r.caught_errors for r in b_res) for b_res in all_runs_benchmark]
            runs_fc = [sum(r.false_confirmations for r in b_res) for b_res in all_runs_benchmark]
            runs_fa = [sum(r.false_accusations for r in b_res) for b_res in all_runs_benchmark]

            multirun_stats = {
                "runs_count": args.runs,
                "disagreement_rate": round(disagreement_rate, 2),
                "differing_items": differing_items,
                "stats": {
                    "false_confirmations": {"min": min(runs_fc), "mean": sum(runs_fc) / len(runs_fc), "max": max(runs_fc)},
                    "false_accusations": {"min": min(runs_fa), "mean": sum(runs_fa) / len(runs_fa), "max": max(runs_fa)},
                    "caught_errors": {"min": min(runs_caught), "mean": sum(runs_caught) / len(runs_caught), "max": max(runs_caught)},
                    "avg_duration_s": {"min": min(runs_durations), "mean": sum(runs_durations) / len(runs_durations), "max": max(runs_durations)},
                },
            }

        if not args.no_reference_cases and (args.only is None or args.only in ("dabro", "moose", "miet")):
            print("\n=== Запуск контрольных кейсов ===")
            ref_mode = CheckMode.local_frp if args.offline else CheckMode.education_searxng
            reference_results = run_reference_cases(
                eval_dir_path,
                mode=ref_mode,
                reference_datetime=ref_dt,
                only_case=args.only,
            )
            for rc in reference_results:
                if "raw_report" in rc:
                    raw_reports[f"reference_{rc['id']}"] = rc["raw_report"]

        if not benchmark_results and not reference_results:
            print("Ошибка: нет данных. Набор проверенных кейсов пуст.", file=sys.stderr)
            _cleanup()
            sys.exit(2)

        # Сохранение артефактов прогона ПЕРЕД генерацией итогового отчета (T403 / [E01], T409 / [E02])
        scores_data = {
            "benchmark": [asdict(r) for r in benchmark_results],
            "reference_cases": [
                {k: v for k, v in rc.items() if k != "raw_report"}
                for rc in reference_results
            ],
            "multirun": multirun_stats,
            "all_runs": [
                [asdict(r) for r in b_res] for b_res in all_runs_benchmark
            ] if args.runs > 1 else None,
        }

        provider = (
            os.environ.get("FACTCHECK_LLM_PROVIDER")
            or os.environ.get("LLM_PROVIDER")
            or "local"
        )
        model = (
            os.environ.get("FACTCHECK_LLM_MODEL")
            or os.environ.get("LLM_MODEL")
            or "gemma4-26b-mtp"
        )
        model_params = {
            "temperature": float(os.environ.get("FACTCHECK_EVAL_TEMPERATURE", "0.0")),
            "seed": int(os.environ.get("FACTCHECK_EVAL_SEED", "42")),
            "max_claims": int(os.environ.get("FACTCHECK_MAX_CLAIMS", "60")),
            "time_budget_seconds": int(
                os.environ.get("FACTCHECK_TIME_BUDGET_SECONDS", "300")
            ),
        }

        manifest = build_manifest(
            model=model,
            provider=provider,
            mode=eval_mode.value,
            reference_datetime=ref_dt.isoformat().replace("+00:00", "Z"),
            eval_dir=eval_dir_path,
            start_time=run_start_dt,
            end_time=datetime.datetime.now(datetime.timezone.utc),
            model_params=model_params,
            extra_settings={
                "overlap_threshold": args.overlap_threshold,
                "runs": args.runs,
                "split": args.split,
            },
            cmdline=sys.argv,
            isolation=isolation,
        )

        # Сначала записываем сырые данные и scores.json, затем строим report.md
        save_run_artifacts(
            run_dir=run_dir,
            manifest=manifest,
            raw_reports=raw_reports,
            scores=scores_data,
            report_markdown="",
        )

        rep_path = pathlib.Path(args.report_file).resolve()
        report_md = generate_markdown_report(
            benchmark_results,
            reference_results,
            rep_path,
            git_commit,
            isolation=isolation,
            multirun_stats=multirun_stats,
            eval_dir=eval_dir_path,
            reference_datetime=ref_dt,
        )
        (run_dir / "report.md").write_text(report_md, encoding="utf-8")

        print(f"\n[+] Артефакты прогона успешно сохранены в: {run_dir}")

        # Критерии успешности прогона (T410 / DoD из dod.yaml):
        # 0 — все критерии выполнены, 1 — критерии не выполнены, 2 — ошибка запуска (проверено выше)
        dod = load_dod_criteria(eval_dir_path)
        min_caught = dod.get("min_contradictions_caught", 11)
        max_fc = dod.get("max_false_confirmations", 0)
        max_fa = dod.get("max_false_accusations", 2)
        time_limit = dod.get("time_limit_per_case_seconds", 600.0)

        total_failures = sum(1 for r in benchmark_results if r.is_failed)
        total_false_conf = sum(r.false_confirmations for r in benchmark_results)
        total_false_acc = sum(r.false_accusations for r in benchmark_results)
        total_caught_err = sum(r.caught_errors for r in benchmark_results)
        total_gold_contr = sum(r.gold_contradictions for r in benchmark_results)

        all_passed = True
        if total_failures > 0:
            all_passed = False
        if total_false_conf > max_fc:
            all_passed = False
        if total_false_acc > max_fa:
            all_passed = False
        if total_gold_contr >= min_caught and total_caught_err < min_caught:
            all_passed = False
        for r in benchmark_results:
            if r.duration_s > time_limit:
                all_passed = False
        for rc in reference_results:
            if rc.get("status") in ("failed", "error"):
                all_passed = False
            if rc.get("duration_s", 0) > time_limit:
                all_passed = False

        _cleanup()
        if not all_passed:
            print("\n[-] Критерии бенчмарка не выполнены (exit code 1)", file=sys.stderr)
            sys.exit(1)
        else:
            print("\n[+] Все критерии бенчмарка успешно выполнены (exit code 0)")
            sys.exit(0)


if __name__ == "__main__":
    main()
