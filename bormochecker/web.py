from __future__ import annotations

import asyncio
from collections.abc import Callable
import fcntl
import hmac
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import Cookie, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from bormochecker.crypto import RateLimiter, unsign
from bormochecker import llm
from bormochecker.log_masking import install_secret_masker
from bormochecker.paths import gpu_lock_path
from bormochecker.contract import CheckMode, CheckRequest, Report
from bormochecker.pipeline import check
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)
install_secret_masker(logger)
install_secret_masker()


class ErrorResponse(BaseModel):
    detail: str = Field(
        ...,
        description="Человекочитаемое описание ошибки",
        examples=["Очередь заполнена: ждут 5 проверок. Попробуйте через несколько минут."],
    )


class QueueStatusResponse(BaseModel):
    running: bool = Field(..., description="Выполняется ли проверка в данный момент")
    waiting: int = Field(..., description="Количество проверок в очереди ожидания")
    role: str = Field(..., description="Роль пользователя (guest, teacher, editor, admin)")
    allow_paid_search: bool = Field(..., description="Разрешён ли платный веб-поиск")
    guest_modes: list[str] = Field(..., description="Список режимов, доступных без авторизации редакции")


class HealthResponse(BaseModel):
    status: str = Field(..., description="Статус компонента", examples=["ok", "ready", "unavailable"])
    detail: str | None = Field(default=None, description="Дополнительное описание при сбое")


app = FastAPI(
    title="BormoChecker API",
    description="Автономный сервис фактчекинга и верификации фактов, новостей и нормативных актов РФ.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
INDEX = Path(__file__).parent / "static" / "index.html"
STATIC = Path(__file__).parent / "static"

if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
else:
    logger.warning("Static directory not found: %s", STATIC)

assets_dir = STATIC / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
else:
    logger.warning("Static assets directory not found: %s", assets_dir)

ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:8612",
    *[
        o.strip()
        for o in os.environ.get("FACTCHECK_CORS_ORIGINS", "").split(",")
        if o.strip()
    ],
]

cors_origins_env = os.environ.get("FACTCHECK_CORS_ORIGINS", "").strip()
allowed_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()] if cors_origins_env else ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_rate_limiter = RateLimiter()


def reset_rate_limiter() -> None:
    """Сбрасывает состояние лимитера частоты (для тестов)."""
    with _rate_limiter._lock:
        _rate_limiter._hits.clear()


def _check_rate_limit(request: Request) -> bool:
    limit = int(os.environ.get("FACTCHECK_RATE_LIMIT_MAX", "60"))
    window = int(os.environ.get("FACTCHECK_RATE_LIMIT_WINDOW", "60"))
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "127.0.0.1")
    ip_key = f"ip:{client_ip}"
    user_token = request.cookies.get("bc_session") or request.headers.get("x-factcheck-token")
    user_key = f"user:{user_token}" if user_token else None

    if _rate_limiter.blocked(ip_key, limit, window):
        return False
    if user_key and _rate_limiter.blocked(user_key, limit, window):
        return False
    _rate_limiter.hit(ip_key, window)
    if user_key:
        _rate_limiter.hit(user_key, window)
    return True


def validate_content_type_by_bytes(content: bytes) -> str:
    """Определяет реальный тип содержимого по сигнатуре (магическим байтам)."""
    if content.startswith(b"PK\x03\x04"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith((b"<!doctype html", b"<!DOCTYPE html", b"<html", b"<HTML")):
        return "text/html"
    return "text/plain"


@app.middleware("http")
async def security_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    # 1. Rate limiting (429)
    if not _check_rate_limit(request):
        return JSONResponse(
            status_code=429,
            content={"detail": "Слишком много запросов. Пожалуйста, подождите (429 Too Many Requests)."},
        )

    # 2. Body size limit (413)
    max_body_bytes = int(os.environ.get("FACTCHECK_MAX_BODY_BYTES", "1048576"))
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Размер запроса превышает допустимый лимит {max_body_bytes} байт (413 Payload Too Large)."},
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > max_body_bytes:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Размер тела запроса превышает допустимый лимит {max_body_bytes} байт (413 Payload Too Large)."},
        )

    response = await call_next(request)

    # 3. Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    logger.error("Необработанная ошибка сервера: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера."},
    )


def _get_guest_modes() -> set[str]:
    raw = os.environ.get("FACTCHECK_GUEST_MODES", "local_frp,education")
    return {m.strip() for m in raw.split(",") if m.strip()}


def _get_auth_info(
    bc_session: str | None = None,
    x_factcheck_token: str | None = None,
) -> dict:
    internal_secret = os.environ.get("FACTCHECK_INTERNAL_TOKEN", "").strip()
    if internal_secret and x_factcheck_token and hmac.compare_digest(x_factcheck_token, internal_secret):
        return {"role": "editor", "is_pro": True, "allow_paid_search": True}

    passport_secret = (
        os.environ.get("PASSPORT_HMAC_SECRET", "").strip()
        or os.environ.get("PASSPORT_SECRET_KEY", "").strip()
    )
    if bc_session and passport_secret and "." in bc_session:
        try:
            data = unsign(passport_secret.encode(), bc_session)
            if data and isinstance(data, dict):
                now = time.time()
                exp = data.get("exp")
                exp_valid = exp is None or (isinstance(exp, (int, float)) and exp > now)
                if exp_valid:
                    has_pro_field = "pro_until" in data or "pro" in data
                    pro_ts = data.get("pro_until") if "pro_until" in data else data.get("pro", 0)
                    is_pro = isinstance(pro_ts, (int, float)) and pro_ts > now
                    has_expired_pro = has_pro_field and not is_pro
                    has_any_valid_expiry = (isinstance(exp, (int, float)) and exp > now) or is_pro
                    role = data.get("role", "teacher" if is_pro else "guest")
                    if has_any_valid_expiry and (is_pro or (role in ("editor", "admin") and not has_expired_pro)):
                        allow_paid = role in ("editor", "admin") and not has_expired_pro
                        return {"role": role, "is_pro": is_pro, "allow_paid_search": allow_paid}
        except Exception:
            pass

    return {"role": "guest", "is_pro": False, "allow_paid_search": False}


def _verify_access(
    mode: str,
    bc_session: str | None = None,
    x_factcheck_token: str | None = None,
) -> dict:
    auth = _get_auth_info(bc_session, x_factcheck_token)
    guest_modes = _get_guest_modes()
    is_pro_or_editor = auth.get("is_pro", False) or auth.get("role") in ("editor", "admin")

    if not is_pro_or_editor:
        if mode not in guest_modes:
            raise HTTPException(
                status_code=403,
                detail=f"Режим {mode!r} недоступен в гостевом доступе. Доступные режимы: {', '.join(sorted(guest_modes))}. Для полного веб-поиска выполните вход.",
            )
        if os.environ.get("FACTCHECK_REQUIRE_AUTH", "1") == "1" and auth["role"] == "guest":
            raise HTTPException(
                status_code=401,
                detail="Для запуска ИИ-фактчекера выполните вход (cookie bc_session) или передайте заголовок X-Factcheck-Token.",
            )

    return auth


GPU_BUSY_MODES = frozenset({"GPU_MAINTENANCE_LOCK", "VRAM_BUSY_LOCK"})
gpu_admission_probe: Callable[[], str | None] | None = None


def _check_gpu_admission() -> None:
    if os.environ.get("BORMOCHECKER_GPU_LOCKED", "").strip().lower() in ("1", "true", "yes"):
        raise HTTPException(
            status_code=503,
            detail="GPU занят регламентной задачей (BORMOCHECKER_GPU_LOCKED). Повторите запрос позже или выберите локальный контур ФРП.",
        )
    if gpu_admission_probe is None:
        return
    try:
        vram_mode = gpu_admission_probe()
        if vram_mode in GPU_BUSY_MODES:
            raise HTTPException(
                status_code=503,
                detail=f"GPU занят регламентной задачей ({vram_mode}). Повторите запрос позже или выберите локальный контур ФРП.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Ошибка при проверке допуска GPU: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Сервис временно недоступен: сбой проверки статуса GPU ({exc}).",
        ) from exc


class GpuProcessLock:
    """Межпроцессная блокировка на основе fcntl.flock для GPU-критической секции."""

    def __init__(self, lock_path: Path | str | None = None) -> None:
        if lock_path is None:
            self.lock_path = gpu_lock_path()
        else:
            self.lock_path = Path(lock_path)
        self._fd: int | None = None

    def acquire(self, timeout: float | None = 120.0) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._fd is None:
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o666)

        start = time.time()
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (BlockingIOError, OSError):
                if timeout is not None and (time.time() - start) >= timeout:
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> GpuProcessLock:
        if not self.acquire():
            raise HTTPException(503, "Таймаут ожидания GPU: ресурс занят другим процессом.")
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()


def is_gpu_file_locked(lock_path: Path | str | None = None) -> bool:
    lp = Path(lock_path) if lock_path is not None else gpu_lock_path()
    if not lp.exists():
        return False
    try:
        fd = os.open(str(lp), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            os.close(fd)
    except Exception:
        return False


@app.get(
    "/",
    summary="Веб-интерфейс фактчекера",
    description="Возвращает HTML-страницу интерактивного пульта фактчекера.",
    response_class=FileResponse,
)
def index():
    return FileResponse(INDEX)


@app.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Проверка жизнеспособности (Liveness probe)",
    description="Проверяет запущенность HTTP-сервиса фактчекера.",
    responses={
        200: {
            "description": "Сервис активен и принимает соединения.",
            "model": HealthResponse,
        }
    },
)
def healthz() -> dict:
    """Проверка жизнеспособности процесса (Liveness probe)."""
    return {"status": "ok"}


@app.get(
    "/readyz",
    response_model=HealthResponse,
    summary="Проверка готовности (Readiness probe)",
    description="Проверяет доступность локальной LLM-модели (llama.cpp) и локальных индексов нормативных документов SQLite FTS5.",
    responses={
        200: {
            "description": "Сервис готов к обработке запросов фактчекинга.",
            "model": HealthResponse,
        },
        503: {
            "description": "Сервис не готов: LLM-модель или локальный FTS-индекс недоступны.",
            "model": ErrorResponse,
        },
    },
)
def readyz() -> JSONResponse:
    """Проверка готовности к обработке запросов (Readiness probe): модель отвечает, индекс доступен."""
    if not llm.check_health():
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": "LLM model is unavailable"},
        )
    try:
        from bormochecker.local_index import LocalKnowledgeIndex

        idx = LocalKnowledgeIndex()
        for p in idx.db_paths:
            with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as conn:
                conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        logger.warning("Readiness index check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": "Index check failed"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ready"},
    )



# Проверки идут строго по одной, в порядке прихода. Модель одна (llama.cpp --parallel 1), поиск
# платный: параллельные проверки не ускоряют дело, а толкаются в очереди модели, и у каждой
# сгорает свой бюджет времени. Внутри процесса uvicorn проверки упорядочивает TURN (asyncio.Lock),
# а между процессами — GpuProcessLock (fcntl.flock) в каталоге состояния.
TURN = asyncio.Lock()
MAX_WAITING = int(os.environ.get("FACTCHECK_MAX_QUEUE", "5"))
waiting = 0


@app.get(
    "/api/queue",
    response_model=QueueStatusResponse,
    summary="Состояние очереди и права доступа",
    description="Возвращает текущую загрузку сервиса (выполняется ли проверка, число ожидающих в очереди) и доступные режимы на основе авторизации.",
    responses={
        200: {
            "description": "Текущий статус очереди и права доступа пользователя.",
            "model": QueueStatusResponse,
        },
        429: {
            "description": "Превышен лимит запросов.",
            "model": ErrorResponse,
        },
    },
)
def api_queue(
    bc_session: str | None = Cookie(default=None),
    x_factcheck_token: str | None = Header(default=None),
) -> dict:
    auth = _get_auth_info(bc_session, x_factcheck_token)
    return {
        "running": TURN.locked() or is_gpu_file_locked(),
        "waiting": waiting,
        "role": auth["role"],
        "allow_paid_search": auth["allow_paid_search"],
        "guest_modes": sorted(_get_guest_modes()),
    }


@app.post(
    "/api/check",
    response_model=Report,
    summary="Проверить текст на фактологическую точность",
    description="Выполняет многоэтапную верификацию текста (извлечение утверждений, нормативный поиск, фактчекинг и формирование отчёта с разметкой фрагментов).",
    responses={
        200: {
            "description": "Успешный отчёт фактчекинга с утверждениями, цитатами источников и сметой затрат.",
            "model": Report,
        },
        400: {
            "description": "Некорректный запрос или невалидные входные данные.",
            "model": ErrorResponse,
        },
        401: {
            "description": "Требуется авторизация (cookie bc_session) или сервисный токен (X-Factcheck-Token).",
            "model": ErrorResponse,
        },
        403: {
            "description": "Доступ запрещён: выбранный режим проверки требует роли редактора или подписки PRO.",
            "model": ErrorResponse,
        },
        408: {
            "description": "Проверка отменена: страница закрыта пользователем до начала проверки.",
            "model": ErrorResponse,
        },
        413: {
            "description": "Размер тела запроса превышает допустимый лимит (413 Payload Too Large).",
            "model": ErrorResponse,
        },
        429: {
            "description": "Превышен лимит запросов: слишком частые обращения (429 Too Many Requests).",
            "model": ErrorResponse,
        },
        503: {
            "description": "Сервис временно недоступен: очередь переполнена, таймаут ожидания GPU или регламентная блокировка.",
            "model": ErrorResponse,
        },
    },
)
async def api_check(
    req: CheckRequest,
    request: Request,
    bc_session: str | None = Cookie(default=None),
    x_factcheck_token: str | None = Header(default=None),
) -> Report:
    mode_str = req.mode.value if isinstance(req.mode, CheckMode) else str(req.mode)
    _verify_access(mode_str, bc_session, x_factcheck_token)
    _check_gpu_admission()
    global waiting
    if waiting >= MAX_WAITING:
        raise HTTPException(503, f"Очередь заполнена: ждут {waiting} проверок. Попробуйте через несколько минут.")
    waiting += 1
    gpu_lock = GpuProcessLock()
    turn_acquired = False
    try:
        try:
            await TURN.acquire()
            turn_acquired = True
            gpu_timeout = float(os.environ.get("FACTCHECK_GPU_LOCK_TIMEOUT_SEC", "120.0"))
            acquired = await asyncio.to_thread(gpu_lock.acquire, timeout=gpu_timeout)
            if not acquired:
                raise HTTPException(503, "Таймаут ожидания GPU: ресурс занят другим процессом.")
        finally:
            waiting -= 1

        if await request.is_disconnected():  # ушёл, пока ждал, — не тратим на него поиск и GPU
            raise HTTPException(408, "Проверка отменена: страница закрыта до начала проверки.")

        # Проверка допуска к GPU непосредственно перед запуском после выхода из очереди
        _check_gpu_admission()

        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]

        # бюджет времени check() отсчитывает отсюда, а не с момента постановки в очередь
        return await run_in_threadpool(
            check,
            req.text,
            req.note,
            mode=req.mode,
            reference_datetime=req.reference_datetime,
            request_id=request_id,
        )

    finally:
        await asyncio.to_thread(gpu_lock.release)
        if turn_acquired:
            TURN.release()
