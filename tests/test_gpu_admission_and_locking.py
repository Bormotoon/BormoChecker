import multiprocessing
import time
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import bormochecker.web
from bormochecker.web import GpuProcessLock, _check_gpu_admission, app
import bormochecker.extract


def _install_fake_admission(monkeypatch: pytest.MonkeyPatch, get_mode_fn) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bormochecker.web, "gpu_admission_probe", get_mode_fn)


def test_gpu_admission_env_lock_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменная BORMOCHECKER_GPU_LOCKED=1 блокирует запуск на GPU с кодом 503."""
    monkeypatch.setenv("BORMOCHECKER_GPU_LOCKED", "1")
    with pytest.raises(HTTPException) as exc_info:
        _check_gpu_admission()
    assert exc_info.value.status_code == 503


def test_gpu_admission_error_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в проверке допуска даёт 503 (ошибки допуска не глотаются)."""
    def failing_get_mode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Сбой соединения с базой при проверке VRAM режима")

    _install_fake_admission(monkeypatch, failing_get_mode)

    with pytest.raises(HTTPException) as exc_info:
        _check_gpu_admission()
    assert exc_info.value.status_code == 503
    assert "Сбой соединения с базой" in exc_info.value.detail


def _lock_worker(lock_path: str, shared_val: multiprocessing.Value) -> None:  # type: ignore[type-arg]
    lock = GpuProcessLock(lock_path)
    with lock:
        with shared_val.get_lock():
            shared_val.value += 1
        time.sleep(0.08)
        with shared_val.get_lock():
            shared_val.value -= 1


def test_two_processes_do_not_enter_critical_section_simultaneously(tmp_path: Path) -> None:
    """Два процесса не входят в критическую секцию одновременно (тест на файловой блокировке)."""
    lock_file = tmp_path / "test_process.lock"
    val = multiprocessing.Value("i", 0)

    p1 = multiprocessing.Process(target=_lock_worker, args=(str(lock_file), val))
    p2 = multiprocessing.Process(target=_lock_worker, args=(str(lock_file), val))

    p1.start()
    p2.start()

    max_concurrent = 0
    start = time.time()
    while p1.is_alive() or p2.is_alive():
        with val.get_lock():
            current = val.value
        if current > max_concurrent:
            max_concurrent = current
        time.sleep(0.005)
        if time.time() - start > 5.0:
            break

    p1.join(timeout=2.0)
    p2.join(timeout=2.0)

    assert max_concurrent == 1


def test_gpu_admission_checked_after_queue_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Проверка доступности GPU выполняется после ожидания в очереди перед запуском."""
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    monkeypatch.setenv("FACTCHECK_LOCK_PATH", str(tmp_path / "gpu_queue.lock"))

    call_count = 0

    def alternating_mode(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        # Первый вызов при входе - OK, второй вызов после очереди - GPU_MAINTENANCE_LOCK
        if call_count == 1:
            return "DAY_ONLINE"
        return "GPU_MAINTENANCE_LOCK"

    _install_fake_admission(monkeypatch, alternating_mode)

    def dummy_extract(*args, **kwargs):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(bormochecker.extract, "extract_claims", dummy_extract)

    client = TestClient(app)
    res = client.post(
        "/api/check",
        json={"text": "Новость для проверки.", "mode": "local_frp"},
    )
    assert res.status_code == 503
    assert "GPU занят регламентной задачей" in res.json()["detail"]
