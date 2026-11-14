"""Точка входа проверки: check(text, note) -> Report.

текст → утверждения (extract) → поиск и загрузка источников (sources) → кандидаты и вердикт
(evaluate) → пять меток контракта. Мнения и редакционные данные в поиск не идут: первые
не проверяются, вторые искать запрещает промпт заказчика («не ищи публичный дубль»).

Лимиты — из окружения: FACTCHECK_MAX_CLAIMS проверяемых утверждений (самые важные первыми,
остальные честно помечаются «не проверено»), FACTCHECK_MAX_DOCS_PER_CLAIM прочитанных страниц
на утверждение, FACTCHECK_TIME_BUDGET_SECONDS на всю проверку.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import socket
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from bormochecker import derived, evaluate, extract, llm, spans
from bormochecker.contract import CheckMode, Claim, Label, Report, Source
from bormochecker.costs import Meter
from bormochecker.deadline import Deadline
from bormochecker.log_masking import install_secret_masker
from bormochecker.numerals import extract_quantities
from bormochecker.sources import Doc, Sources, extract_article_subject

logger = logging.getLogger(__name__)
install_secret_masker(logger)


time_budget_ctx: ContextVar[float | None] = ContextVar("time_budget_ctx", default=None)

IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
VERDICT_LABEL = {"supported": Label.confirmed, "partially_supported": Label.clarify,
                 "contradicted": Label.error, "unresolved": Label.clarify}
# У фрагмента одна метка — худшая из его утверждений. Содержательная проблема важнее
# редакционного происхождения (промпт заказчика §3), оценка — самая слабая.
SEVERITY = [Label.error, Label.clarify, Label.editorial, Label.confirmed, Label.opinion]
LABEL_RU = {Label.confirmed: "подтверждено", Label.clarify: "уточнить", Label.error: "ошибка",
            Label.opinion: "оценка", Label.editorial: "редакционные данные"}
EDITORIAL_TEXT = {
    "stated": "Сведения из собственного материала редакции, источник указан. Оригинал в этот запуск "
              "не передан — сверка за редакцией; отсутствие публикации в сети доводом против не является.",
    "unclear": "Похоже на собственные наблюдения или материалы редакции; необходимо подтвердить "
               "происхождение либо указать внешний источник.",
}


def _env_number(name: str, default: float) -> float:
    return float(os.environ.get(name, "").strip() or default)


def _preload_fact_ledger(sources: Sources, fact_ledger: object) -> None:
    """Предзагрузка реестра проверенных фактов FactLedger как официального источника сюжета."""
    facts = []
    if hasattr(fact_ledger, "facts"):
        facts = getattr(fact_ledger, "facts", [])
    elif isinstance(fact_ledger, dict):
        facts = fact_ledger.get("facts", [])
    elif isinstance(fact_ledger, list):
        facts = fact_ledger

    lines = []
    for f in facts:
        if isinstance(f, dict):
            fid = f.get("fact_id", "")
            quote = f.get("exact_quote", "")
            sid = f.get("source_id", "")
        else:
            fid = getattr(f, "fact_id", "")
            quote = getattr(f, "exact_quote", "")
            sid = getattr(f, "source_id", "")
        if quote:
            lines.append(f"[{fid}] ({sid}) {quote}")

    if lines:
        doc = Doc(
            id="fact_ledger",
            url="ledger://weekly_digest",
            title="Реестр проверенных фактов (FactLedger)",
            kind="ledger",
            text="\n".join(lines),
        )
        sources._docs["ledger://weekly_digest"] = doc
        sources.linked.append(doc)


_NEGATION_RE = re.compile(r"\b(?:не|нет|ни|без|отмен[её]н\w*|запрещ[её]н\w*|отказ\w*)\b", re.IGNORECASE)


def _has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text))


def quote_matches_claim(quote: str, claim_text: str, doc_title: str = "") -> bool:
    """Проверяет, подтверждает ли цитата (с учетом заголовка документа и канонических величин) утверждение."""
    if not quote or not claim_text:
        return False
    full_evidence = f"{doc_title}. {quote}".strip() if doc_title else quote
    c_quants = [q for q in extract_quantities(claim_text) if q.dim != "YEAR"]
    q_quants = extract_quantities(full_evidence)
    if c_quants and all(
        (cq.dim == "COUNT" and cq.value in (Decimal(0), Decimal(1))) or any(cq.matches(dq) for dq in q_quants)
        for cq in c_quants
    ):
        return True
    claim_words = {w.casefold() for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", claim_text) if not _NEGATION_RE.match(w)}
    ev_cf = full_evidence.casefold()
    return bool(claim_words) and all(w in ev_cf for w in claim_words)


def _find_attachment_body_quote(doc: Doc, claim_text: str) -> tuple[str, int, int] | None:
    doc_text = doc.text or ""
    if not doc_text or not claim_text:
        return None

    c_clean = claim_text.strip()
    idx = doc_text.casefold().find(c_clean.casefold())
    if idx != -1:
        return doc_text[idx:idx + len(c_clean)], idx, idx + len(c_clean)

    claim_words = [w.casefold() for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", claim_text) if not _NEGATION_RE.match(w)]
    claim_nums = re.findall(r"\b\d+(?:[\.,]\d+)?\b", claim_text)
    if not claim_words and not claim_nums:
        return None

    for m in re.finditer(r"[^.!?\n]+[.!?]?", doc_text):
        sent = m.group(0).strip()
        if len(sent) < 10:
            continue
        sent_cf = sent.casefold()
        if all(w in sent_cf for w in claim_words) and all(n in sent_cf for n in claim_nums):
            return sent, m.start(), m.end()

    return None


def _judge(atom: dict, sources: Sources, deadline: float, meter: Meter, notes: list[str],
           fact_ledger: object | None = None, note: str = "", reference_datetime: datetime | None = None) -> None:
    """Проставляет атому label, explanation, sources, suggestion. Ничего не бросает."""
    atom.setdefault("suggestion", None)
    atom.setdefault("sources", [])
    if atom.get("label") in (Label.error, Label.confirmed) and atom.get("explanation"):
        return

    if atom.get("is_arithmetic_premise"):
        atom["label"] = Label.clarify
        atom["explanation"] = atom.get("explanation") or "Исходная посылка расчёта: противоречие не доказано."
        return

    atom["sources"], atom["suggestion"] = [], None
    if atom["kind"] in ("opinion", "prediction"):
        atom["label"] = Label.opinion
        atom["explanation"] = "Прогноз — не проверяется как факт." if atom["kind"] == "prediction" \
            else "Оценка или реклама — не проверяется как факт."
        return

    # Проверка редакционных данных по локальным вложениям AttachmentManifest
    # Утверждения с конкретными ценами/процентами/годами НЕ скрываются под editorial — их оцениваем источниками!
    has_concrete_data = bool(re.search(r"(?:\d+\s*%(?!\w)|\d+\s*(?:руб|₽|\$|евро)|\b20\d\d\s*г\b)", atom["text"]))
    if atom["kind"] == "editorial" and not has_concrete_data:
        claim_text = atom["text"]
        for doc in sources.pool():
            is_local = (
                doc.url.startswith("file://")
                or doc.url.startswith("attachment://")
                or getattr(doc, "kind", "") in ("official", "user_attachment")
            )
            if not is_local:
                continue

            found = _find_attachment_body_quote(doc, claim_text)
            if not found:
                continue

            quote, q_start, q_end = found
            quote_clean = quote.strip()
            filename = (doc.url.split("/")[-1] or "").strip().casefold()
            title = (doc.title or "").strip().casefold()
            quote_cf = quote_clean.casefold()

            # (б) цитата совпадает с заголовком или именем файла -> clarify
            doc_kind = getattr(doc, "kind", None)
            loc_str = f"символы {q_start}-{q_end}"
            if quote_cf == filename or quote_cf == title:
                atom["label"] = Label.clarify
                atom["explanation"] = (
                    f"Цитата совпадает с заголовком или именем файла вложения («{quote_clean}»), "
                    f"в теле документа подтверждение отсутствует."
                )
                atom["sources"] = [Source(url=doc.url, quote=quote_clean, kind=doc_kind, location=loc_str)]
                return

            # (в) проверка отрицаний: если полярность различается, не выше clarify
            from bormochecker.evaluate import has_predicate_negation_conflict

            if has_predicate_negation_conflict(claim_text, quote_clean):
                atom["label"] = Label.clarify
                atom["explanation"] = (
                    f"Полярность утверждения («{claim_text}») и цитаты источника («{quote_clean}», "
                    f"символы {q_start}-{q_end}) различается (обнаружено отрицание)."
                )
                atom["sources"] = [Source(url=doc.url, quote=quote_clean, kind=doc_kind, location=loc_str)]
                return

            # (г) в отчёт включать цитату и её расположение
            atom["label"] = Label.confirmed
            atom["explanation"] = (
                f"Подтверждено по тексту локального вложения редакции "
                f"({doc.title or filename}, символы {q_start}-{q_end}): «{quote_clean}»"
            )
            atom["sources"] = [Source(url=doc.url, quote=quote_clean, kind=doc_kind, location=loc_str)]
            return

        origin = atom.get("origin") or "stated"
        atom["label"], atom["explanation"] = Label.editorial, EDITORIAL_TEXT.get(origin, EDITORIAL_TEXT["stated"])
        return

    # Детерминированный Python-аудит чисел и процентов перед LLM-критиком (Патч 5)
    pct_match = re.search(
        r"(\d+(?:[\.,]\d+)?)\s*(?:из|от)\s*(\d+(?:[\.,]\d+)?)[^\d%]*(\d+(?:[\.,]\d+)?)\s*%",
        atom["text"],
        re.IGNORECASE,
    )
    if pct_match:
        try:
            part = Decimal(pct_match.group(1).replace(",", "."))
            total = Decimal(pct_match.group(2).replace(",", "."))
            claimed = Decimal(pct_match.group(3).replace(",", "."))
            if total > 0:
                calc = (part / total) * Decimal(100)
                if abs(calc - claimed) > Decimal("0.6"):
                    atom["label"] = Label.error
                    atom["explanation"] = (
                        f"Арифметическая ошибка: {part} из {total} составляет {calc:.1f}%, а не {claimed}%."
                    )
                    atom["suggestion"] = f"{part} из {total} ({calc:.1f}%)"
                    return
                else:
                    atom["label"] = Label.confirmed
                    atom["explanation"] = f"Арифметика верна: {part} из {total} = {calc:.1f}%."
                    return
        except (InvalidOperation, ZeroDivisionError):
            pass

    # Сверка числительных с FactLedger
    if fact_ledger:
        atom_nums = set(re.findall(r"\b\d+(?:[\.,]\d+)?\b", atom["text"]))
        stat_nums = {n for n in atom_nums if not re.fullmatch(r"(19|20)\d\d", n)}
        if stat_nums:
            ledger_doc = sources._docs.get("ledger://weekly_digest")
            if ledger_doc and ledger_doc.text:
                ledger_nums = set(re.findall(r"\b\d+(?:[\.,]\d+)?\b", ledger_doc.text))
                missing_nums = stat_nums - ledger_nums
                if missing_nums:
                    words = [w.casefold() for w in re.findall(r"\w+", atom["text"]) if len(w) >= 4]
                    if words and sum(w in ledger_doc.text.casefold() for w in words) >= 2:
                        atom["label"] = Label.clarify
                        atom["explanation"] = (
                            f"Числа {', '.join(sorted(missing_nums))} не найдены в FactLedger сюжета."
                        )
                        atom["suggestion"] = "Сверить числовые показатели с реестром фактов"
                        return

    atom["label"] = Label.clarify
    if hasattr(sources, "claim_failures") and atom.get("text") in sources.claim_failures:
        atom["explanation"] = f"Проверка не выполнена: {sources.claim_failures[atom['text']]} (сбой провайдеров поиска)."
        atom["sources_unavailable"] = True
        return

    remaining = deadline.remaining() if hasattr(deadline, "remaining") else max(0.0, deadline - time.monotonic())
    if remaining <= 0:
        atom["explanation"] = "Не проверялось в этом запуске: исчерпан бюджет времени."
        atom["deferred"] = True
        return
    timeout_cap = deadline.timeout(120) if hasattr(deadline, "timeout") else min(120, remaining)
    try:
        # Всё прочитанное за проверку — каждому утверждению: одна карточка события подтверждает
        # и дату, и цену, и адрес (промпт заказчика §4). Таймаут вызова — не больше остатка бюджета.
        verdict, reason, evidence, suggestion = evaluate.evaluate_claim(
            atom["text"],
            sources.pool(),
            meter,
            notes,
            timeout=timeout_cap,
            note=note,
            is_arithmetic_premise=bool(atom.get("is_arithmetic_premise")),
            derived_verified=bool(atom.get("derived_verified")),
            in_text_arithmetic_consistent=bool(atom.get("in_text_arithmetic_consistent")),
            reference_datetime=reference_datetime,
            deadline=deadline if hasattr(deadline, "expired") else None,
        )
    except (llm.LLMError, ValueError, TimeoutError) as error:
        atom["explanation"] = "Проверка не выполнена: модель не ответила. Техническая причина, а не свойство утверждения."
        atom["failed"] = True
        notes.append(f"оценка «{atom['text'][:60]}» не выполнена: {error}")
        return
    if atom.get("deferred"):
        if verdict == "unresolved":  # не нашли там, где не искали, — это не проверка
            reason += " Отдельного поиска по этому утверждению не было: превышен лимит утверждений или времени."
        else:
            atom["deferred"], atom["pooled"] = False, True
    atom["label"], atom["explanation"] = VERDICT_LABEL[verdict], reason
    atom["sources"] = [
        Source(url=doc.url, quote=quote, kind=getattr(doc, "kind", None))
        for doc, quote, _ in evidence
    ]
    # правка нужна там, где есть что править; у «подтверждено» — шум
    atom["suggestion"] = suggestion if atom["label"] in (Label.error, Label.clarify) else None


def _merge(text: str, atoms: list[dict]) -> list[Claim]:
    """Перекрывающиеся фрагменты — один Claim с худшей меткой: форма красит фрагмент одним цветом."""
    valid_atoms = [a for a in atoms if a.get("start") is not None and a.get("end") is not None]
    groups: list[list[dict]] = []
    for atom in sorted(valid_atoms, key=lambda a: (a["start"], -a["end"])):
        is_premise = atom.get("is_arithmetic_premise") or "посылк" in (atom.get("explanation") or "").casefold()
        prev_has_error = groups and any(a.get("label") == Label.error for a in groups[-1])
        curr_is_error = atom.get("label") == Label.error
        prev_has_premise = groups and any(a.get("is_arithmetic_premise") or "посылк" in (a.get("explanation") or "").casefold() for a in groups[-1])

        if (prev_has_premise and curr_is_error) or (prev_has_error and is_premise):
            groups.append([atom])
        elif groups and atom["start"] < max(a["end"] for a in groups[-1]):
            groups[-1].append(atom)
        else:
            groups.append([atom])
    claims = []
    for group in groups:
        start, end = group[0]["start"], max(a["end"] for a in group)
        has_editorial = any(a.get("label") == Label.editorial for a in group)
        has_error = any(a.get("label") == Label.error for a in group)
        unresolved_markers = (
            "в найденных источниках нет подходящего фрагмента",
            "не проверялось в этом запуске",
            "отсутствие подтверждения не означает",
            "отдельного поиска по этому утверждению не было",
            "модель не ответила",
            "исчерпан бюджет времени",
        )
        has_substantive_clarify = any(
            a.get("label") == Label.clarify and not any(
                m in (a.get("explanation") or "").casefold() for m in unresolved_markers
            )
            for a in group
        )

        if has_error:
            label = Label.error
        elif has_substantive_clarify:
            label = Label.clarify
        elif has_editorial:
            label = Label.editorial
        else:
            group.sort(key=lambda a: SEVERITY.index(a["label"]))
            label = group[0]["label"]

        explanation = group[0].get("explanation", "") if len(group) == 1 else "\n".join(
            f"«{a.get('text', '')}» — {LABEL_RU.get(a.get('label', Label.clarify), '')}: {a.get('explanation', '')}" for a in group)
        sources = list({(s.url, s.quote): s for a in group for s in a.get("sources", [])}.values())
        origin = next((a.get("origin") for a in group if a.get("origin")), None)
        kind = next((a.get("kind") for a in group if a.get("kind")), None)
        sources_unavailable = any(bool(a.get("sources_unavailable")) for a in group)
        claims.append(
            Claim(
                start=start,
                end=end,
                text=text[start:end],
                label=label,
                explanation=explanation,
                sources=sources,
                suggestion=group[0].get("suggestion"),
                origin=origin,
                kind=kind,
                sources_unavailable=sources_unavailable,
            )
        )

    resolved = spans.resolve_span_overlaps(claims, text)
    unlocated_atoms = [a for a in atoms if a.get("start") is None or a.get("end") is None]
    for a in unlocated_atoms:
        resolved.append(
            Claim(
                start=None,
                end=None,
                text=a.get("text", ""),
                label=a.get("label", Label.clarify),
                explanation=a.get("explanation", ""),
                sources=list({(s.url, s.quote): s for s in a.get("sources", [])}.values()),
                suggestion=a.get("suggestion"),
                origin=a.get("origin"),
                kind=a.get("kind"),
                sources_unavailable=bool(a.get("sources_unavailable")),
            )
        )
    return resolved


def _summary(claims: list[Claim], reference_datetime: datetime | None = None) -> str:
    ref_dt = reference_datetime or datetime.now(ZoneInfo("Europe/Moscow"))
    n = Counter(c.label for c in claims)
    parts = [f"ошибок {n[Label.error]}", f"уточнить {n[Label.clarify]}",
             f"сверка за редакцией {n[Label.editorial]}", f"подтверждено {n[Label.confirmed]}"]
    return (f"Момент проверки: {ref_dt:%d.%m.%Y %H:%M}. Фактических фрагментов "
            f"{len(claims) - n[Label.opinion]}: {', '.join(parts)}; оценок отдельно — {n[Label.opinion]}.")


def _can_use_asyncio() -> bool:
    """Проверяет работоспособность socketpair для локального event loop (защита от monkeypatching socket)."""
    try:
        s1, s2 = socket.socketpair()
        s1.close()
        s2.close()
        return True
    except Exception:
        return False


def _run_coroutine_sync(coro: Any) -> Any:
    """Запускает корутину синхронно, корректно обрабатывая случаи внутри/вне event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _judge_atoms_concurrent(
    atoms: list[dict],
    sources: Sources,
    deadline: Deadline | float,
    meter: Meter,
    notes: list[str],
    concurrency: int = 3,
    fact_ledger: object | None = None,
    note: str = "",
    reference_datetime: datetime | None = None,
) -> None:
    """Параллельная оценка утверждений через asyncio.Semaphore с сохранением порядка (T503)."""
    bounded_concurrency = max(1, concurrency)
    sem = asyncio.Semaphore(bounded_concurrency)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=bounded_concurrency)
    loop = asyncio.get_running_loop()

    try:
        async def _worker(atom: dict) -> None:
            if atom.get("label") in (Label.error, Label.confirmed) and atom.get("explanation"):
                return
            async with sem:
                rem = deadline.remaining() if hasattr(deadline, "remaining") else max(0.0, deadline - time.monotonic())
                if rem <= 0:
                    if not atom.get("label"):
                        atom["label"] = Label.clarify
                        atom["explanation"] = "Не проверялось в этом запуске: исчерпан бюджет времени."
                        atom["deferred"] = True
                    return


                timeout_for_task = deadline.timeout(120) if hasattr(deadline, "timeout") else min(120.0, rem)
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            executor,
                            _judge,
                            atom,
                            sources,
                            deadline,
                            meter,
                            notes,
                            fact_ledger,
                            note,
                            reference_datetime,
                        ),
                        timeout=max(0.001, timeout_for_task),
                    )
                except TimeoutError:
                    atom.setdefault("sources", [])
                    atom.setdefault("suggestion", None)
                    atom["label"] = Label.clarify
                    atom["explanation"] = "Проверка не выполнена: превышен лимит времени на утверждение."
                    atom["failed"] = True
                    notes.append(f"оценка «{atom.get('text', '')[:60]}» прервана по таймауту")
                except Exception as e:
                    atom.setdefault("sources", [])
                    atom.setdefault("suggestion", None)
                    atom["label"] = Label.clarify
                    atom["explanation"] = f"Проверка не выполнена: технический сбой ({e})."
                    atom["failed"] = True
                    notes.append(f"оценка «{atom.get('text', '')[:60]}» завершилась с ошибкой: {e}")

        tasks = [asyncio.create_task(_worker(atom)) for atom in atoms]
        if tasks:
            rem = deadline.remaining() if hasattr(deadline, "remaining") else max(0.0, deadline - time.monotonic())
            done, pending = await asyncio.wait(tasks, timeout=max(0.0, rem))
            for p in pending:
                p.cancel()

        for a in atoms:
            if a.get("label") is None:
                a.setdefault("sources", [])
                a.setdefault("suggestion", None)
                a["label"] = Label.clarify
                a["explanation"] = "Не проверялось в этом запуске: исчерпан бюджет времени."
                a["deferred"] = True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _emit_metrics_log(
    report: Report,
    *,
    request_id: str,
    mode: str,
    meter: Meter,
    t_extract: float,
    t_search: float,
    t_eval: float,
    t_total: float,
) -> None:
    claims_by_label = Counter(
        c.label.value if hasattr(c.label, "value") else str(c.label)
        for c in (report.claims or [])
    )
    http_reqs = dict(getattr(meter.costs, "http_requests_by_provider", {}))
    metrics_data = {
        "request_id": request_id,
        "mode": mode,
        "outcome": report.status,
        "stage_durations_seconds": {
            "extract": round(t_extract, 4),
            "search": round(t_search, 4),
            "evaluate": round(t_eval, 4),
            "total": round(t_total, 4),
        },
        "claims_by_label": dict(claims_by_label),
        "requests_by_provider": http_reqs,
    }
    logger.info("FACTCHECK_METRICS %s", json.dumps(metrics_data, ensure_ascii=False))


def check(
    text: str,
    note: str = "",
    *,
    mode: CheckMode | str = CheckMode.education_searxng,
    attachments: list[object] | None = None,
    fact_ledger: object | None = None,
    reference_datetime: datetime | None = None,
    time_budget_seconds: float | None = None,
    eval_concurrency: int | None = None,
    request_id: str | None = None,
) -> Report:
    t_start = time.monotonic()
    t_extract = 0.0
    t_search = 0.0
    t_eval = 0.0
    req_id = request_id or uuid.uuid4().hex[:16]

    moscow_tz = ZoneInfo("Europe/Moscow")
    if reference_datetime is None:
        reference_datetime = datetime.now(moscow_tz)
    elif reference_datetime.tzinfo is None:
        reference_datetime = reference_datetime.replace(tzinfo=moscow_tz)

    if not note:
        note = f"Момент проверки: {reference_datetime:%d.%m.%Y}."

    mode_str = mode.value if isinstance(mode, CheckMode) else str(mode)
    network_allowed = (mode_str != "local_frp" and mode != CheckMode.local_frp)
    from bormochecker.limits import Settings
    from bormochecker.retrieval import network_allowed_ctx
    net_token = network_allowed_ctx.set(network_allowed)
    try:
        cfg = Settings.from_env()
        max_claims = cfg.max_claims
        max_docs = cfg.max_documents_per_claim
        if eval_concurrency is not None:
            concurrency = max(1, int(eval_concurrency))
        else:
            concurrency = max(1, cfg.eval_concurrency)
        if time_budget_seconds is not None:
            budget = float(time_budget_seconds)
        elif time_budget_ctx.get() is not None:
            budget = float(time_budget_ctx.get())
        else:
            budget = cfg.time_budget_seconds
        search_provider = "local_frp" if (mode_str == "local_frp" or mode == CheckMode.local_frp) else "FreeRuNetSearch"
        meter = Meter(
            provider=os.environ.get("LLM_PROVIDER") or None,
            model=os.environ.get("LLM_MODEL") or None,
            settings={
                "temperature": 0.1,
                "max_claims": max_claims,
                "max_docs_per_claim": max_docs,
                "eval_concurrency": concurrency,
                "time_budget_s": budget,
                "search": search_provider,
                "mode": mode_str,
            },
        )
        deadline = Deadline(budget)
        notes: list[str] = []

        def failed(summary: str, reason: str) -> Report:
            rep = Report(
                status="failed",
                summary=summary,
                text=text,
                claims=[],
                limitations=[reason, *notes],
                costs=meter.finish(),
                reference_datetime=reference_datetime,
            )
            t_total = time.monotonic() - t_start
            _emit_metrics_log(
                rep,
                request_id=req_id,
                mode=mode_str,
                meter=meter,
                t_extract=t_extract,
                t_search=t_search,
                t_eval=t_eval,
                t_total=t_total,
            )
            return rep

        try:
            t_ext_start = time.monotonic()
            atoms = extract.extract_claims(
                text,
                note,
                meter,
                notes,
                timeout=deadline.timeout(180),
                deadline=deadline,
            )
            t_extract = time.monotonic() - t_ext_start
        except (llm.LLMError, ValueError, TimeoutError) as error:
            t_extract = time.monotonic() - t_ext_start
            if deadline.expired():
                rep = Report(
                    status="partial",
                    summary="Проверка не завершена: исчерпан бюджет времени.",
                    text=text,
                    claims=[],
                    limitations=[f"Исчерпан бюджет времени ({budget:.1f} с): {error}", *notes],
                    costs=meter.finish(),
                    reference_datetime=reference_datetime,
                )
                t_total = time.monotonic() - t_start
                _emit_metrics_log(
                    rep,
                    request_id=req_id,
                    mode=mode_str,
                    meter=meter,
                    t_extract=t_extract,
                    t_search=t_search,
                    t_eval=t_eval,
                    t_total=t_total,
                )
                return rep
            return failed("Проверка не выполнена: модель не разобрала текст на утверждения.", str(error))

        if not atoms:
            rep = Report(
                status="ok",
                summary=f"Момент проверки: {reference_datetime:%d.%m.%Y %H:%M}. Проверяемых утверждений не найдено.",
                text=text,
                claims=[],
                limitations=notes,
                costs=meter.finish(),
                reference_datetime=reference_datetime,
            )
            t_total = time.monotonic() - t_start
            _emit_metrics_log(
                rep,
                request_id=req_id,
                mode=mode_str,
                meter=meter,
                t_extract=t_extract,
                t_search=t_search,
                t_eval=t_eval,
                t_total=t_total,
            )
            return rep

        try:
            # Детерминированный CPU-аудит (календарь, суммы часов, нормативные приказы)
            derived.audit_derived_claims(text, atoms, reference_datetime=reference_datetime)
            atoms = [
                a for a in atoms
                if (
                    span_txt := (
                        text[a["start"]:a["end"]].strip()
                        if a.get("start") is not None and a.get("end") is not None
                        else (a.get("quote") or a.get("text") or "").strip()
                    )
                )
                and (len(span_txt.split()) > 1 or re.search(r"\d", span_txt))
            ]

            checkable = sorted(
                (a for a in atoms if a["kind"] == "web_checkable"),
                key=lambda a: IMPORTANCE_ORDER[a["importance"]],
            )
            for atom in checkable[max_claims:]:
                atom["deferred"] = True
            t_search_start = time.monotonic()
            sources = Sources(meter, text, network_allowed=network_allowed)
            sources.preload(f"{text}\n{note}", deadline=deadline)
            if attachments:
                sources.preload_local_docs(attachments)
            if fact_ledger:
                _preload_fact_ledger(sources, fact_ledger)

            # Оставляем не менее 60% бюджета времени на этап LLM-оценки утверждений (поиск <= 160 с)
            rem_search = deadline.remaining()
            search_deadline = Deadline(min(rem_search * 0.40, 160.0)) if rem_search > 10.0 else deadline

            # Кластеризация запросов: приоритет у high-importance и числовых фактов
            _imp_rank = {"high": 0, "medium": 1, "low": 2}
            prioritized_checkable = sorted(
                checkable[:max_claims],
                key=lambda a: (
                    _imp_rank.get(str(a.get("importance", "medium")), 1),
                    0 if re.search(r"\d", a.get("text", "")) else 1,
                    a.get("start") or 0,
                ),
            )
            clusters: dict[str, list[dict]] = {}
            for atom in prioritized_checkable:
                cluster_key = atom.get("entity_cluster") or atom.get("text", "")
                clusters.setdefault(cluster_key, []).append(atom)

            _subject_anchor = extract_article_subject(text)
            _note_anchors = [
                w for w in re.findall(r"\b[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]{2,}\b", note or "")
                if w.casefold() not in ("при", "для", "про", "или", "без", "приемная", "кампания", "концерт", "момент", "проверки")
            ][:2]
            _head_anchors = [_subject_anchor] if _subject_anchor else _note_anchors

            def _anchored_query(atom_obj: dict) -> str:
                base_q = (atom_obj.get("query") or "").strip() or atom_obj.get("text", "")
                if _head_anchors and not any(a.casefold() in base_q.casefold() for a in _head_anchors):
                    return f"{' '.join(_head_anchors)} {base_q}"
                return base_q

            for cluster_key, cluster_atoms in clusters.items():
                if deadline.expired() or search_deadline.expired():
                    break
                # Главное утверждение кластера загружает документы первым
                primary = cluster_atoms[0]
                sources.gather_for(
                    primary["text"],
                    max_docs,
                    mode=mode_str,
                    importance=primary.get("importance", "medium"),
                    query=_anchored_query(primary),
                    deadline=search_deadline,
                )
                # Для остальных утверждений того же кластера вызываем gather_for (Шаг В отсекает лишний поиск)
                for subordinate in cluster_atoms[1:]:
                    if deadline.expired() or search_deadline.expired():
                        break
                    sources.gather_for(
                        subordinate["text"],
                        max_docs,
                        mode=mode_str,
                        importance=subordinate.get("importance", "medium"),
                        query=_anchored_query(subordinate),
                        deadline=search_deadline,
                    )
            t_search = time.monotonic() - t_search_start

            t_eval_start = time.monotonic()
            eval_atoms = sorted(
                atoms,
                key=lambda a: (_imp_rank.get(str(a.get("importance", "medium")), 1), a.get("start") or 0),
            )
            if _can_use_asyncio():
                _run_coroutine_sync(
                    _judge_atoms_concurrent(
                        eval_atoms,
                        sources,
                        deadline,
                        meter,
                        notes,
                        concurrency=concurrency,
                        fact_ledger=fact_ledger,
                        note=note,
                        reference_datetime=reference_datetime,
                    )
                )
            else:
                for atom in atoms:
                    _judge(
                        atom,
                        sources,
                        deadline,
                        meter,
                        notes,
                        fact_ledger=fact_ledger,
                        note=note,
                        reference_datetime=reference_datetime,
                    )
            t_eval = time.monotonic() - t_eval_start

            limitations = []
            if deferred := sum(bool(a.get("deferred")) for a in atoms):
                limitations.append(
                    f"Не проверено утверждений: {deferred} (лимит утверждений или времени) — помечены «уточнить»."
                )
            if pooled := sum(bool(a.get("pooled")) for a in atoms):
                limitations.append(f"Без отдельного поиска, по уже найденным страницам, проверено утверждений: {pooled}.")
            if failed_n := sum(bool(a.get("failed")) for a in atoms):
                limitations.append(f"Модель не ответила по {failed_n} утверждениям — помечены «уточнить».")
            limitations += [f"Поиск: {e}" for e in sources.errors]
            if sources.fetch_failed:
                limitations.append(
                    f"Не удалось прочитать найденных страниц: {sources.fetch_failed} (PDF, запрет, таймаут)."
                )
            has_sources_unavailable = any(a.get("sources_unavailable") for a in atoms) or bool(getattr(sources, "claim_failures", None))
            if has_sources_unavailable:
                limitations.append("Поиск: источники недоступны (сбой провайдеров) — проверка части утверждений не выполнена.")
            limitations += notes
            is_partial = bool(deferred or failed_n or sources.errors or has_sources_unavailable or deadline.expired())
            rep = Report(
                status="partial" if is_partial else "ok",
                summary=_summary(claims := _merge(text, atoms), reference_datetime=reference_datetime),
                text=text,
                claims=claims,
                limitations=limitations,
                costs=meter.finish(),
                reference_datetime=reference_datetime,
                claim_failures=dict(getattr(sources, "claim_failures", {})),
            )

            t_total = time.monotonic() - t_start
            _emit_metrics_log(
                rep,
                request_id=req_id,
                mode=mode_str,
                meter=meter,
                t_extract=t_extract,
                t_search=t_search,
                t_eval=t_eval,
                t_total=t_total,
            )
            return rep
        except (TimeoutError, Exception) as err:
            if deadline.expired():
                for a in atoms:
                    if not a.get("label"):
                        a["label"] = Label.clarify
                        a["explanation"] = "Не проверялось в этом запуске: исчерпан бюджет времени."
                        a["deferred"] = True
                claims = _merge(text, atoms)
                rep = Report(
                    status="partial",
                    summary="Проверка частично выполнена: исчерпан бюджет времени.",
                    text=text,
                    claims=claims,
                    limitations=[f"Бюджет времени исчерпан ({budget:.1f} с): {err}", *notes],
                    costs=meter.finish(),
                    reference_datetime=reference_datetime,
                    claim_failures=dict(getattr(sources, "claim_failures", {})),
                )

                t_total = time.monotonic() - t_start
                _emit_metrics_log(
                    rep,
                    request_id=req_id,
                    mode=mode_str,
                    meter=meter,
                    t_extract=t_extract,
                    t_search=t_search,
                    t_eval=t_eval,
                    t_total=t_total,
                )
                return rep
            raise

    finally:
        network_allowed_ctx.reset(net_token)


def verify_digest_blocks(
    clusters: list[dict],
    *,
    attachments: list[object] | None = None,
    fact_ledger: object | None = None,
    time_budget_per_block: int = 60,
    rewrite_callback: Any | None = None,
) -> list[dict]:
    """Проверяет дайджест поблочно по каждому из кластеров (2–4 тыс. знаков).

    Автоматически отправляет абзацы с Label.error / Label.clarify на точечный рерайт
    по полям claim.explanation и claim.suggestion.
    """
    verified_clusters = []
    for cluster in clusters:
        c = dict(cluster)
        block_text = c.get("draft_text") or c.get("text") or c.get("markdown") or ""
        note = c.get("title") or c.get("rubric") or ""
        if not block_text.strip():
            c["verified"] = True
            c["factcheck_report"] = None
            verified_clusters.append(c)
            continue

        try:
            report = check(
                block_text,
                note=note,
                attachments=attachments,
                fact_ledger=fact_ledger,
                time_budget_seconds=time_budget_per_block,
            )
        except Exception as err:
            c["verified"] = False
            c["needs_rewrite"] = True
            c["factcheck_error"] = str(err)
            c["reason"] = f"exception: {err}"
            c["factcheck_report"] = None
            verified_clusters.append(c)
            continue

        c["factcheck_report"] = report
        problematic = [claim for claim in report.claims if claim.label in (Label.error, Label.clarify)]
        is_failed = report.status in ("partial", "failed")

        if is_failed or problematic:
            c["needs_rewrite"] = True
            c["verified"] = False
            if is_failed:
                c["reason"] = report.summary or f"check status: {report.status}"
            else:
                c["reason"] = f"found {len(problematic)} problematic claim(s)"
            instructions = [
                {
                    "start": claim.start,
                    "end": claim.end,
                    "text": claim.text,
                    "label": claim.label.value,
                    "explanation": claim.explanation,
                    "suggestion": claim.suggestion,
                }
                for claim in problematic
            ]
            c["rewrite_instructions"] = instructions

            if callable(rewrite_callback):
                try:
                    c["revised_text"] = rewrite_callback(block_text, instructions)
                except Exception as err:
                    c["rewrite_error"] = str(err)
            elif any(claim.suggestion for claim in problematic):
                revised = block_text
                for p in sorted(problematic, key=lambda x: x.start, reverse=True):
                    if p.suggestion:
                        revised = revised[:p.start] + p.suggestion + revised[p.end:]
                c["auto_suggested_text"] = revised
        else:
            c["needs_rewrite"] = False
            c["verified"] = True

        verified_clusters.append(c)
    return verified_clusters
