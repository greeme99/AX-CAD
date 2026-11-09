"""Underlay files are untrusted input read by native code (PDFium) or large XML: every parse and
render runs in a worker process with a memory cap and a time limit. Unlike the DXF reader (one
process per call) the workers are kept, so a zoom or pan that needs a new image does not pay
the start-up of a Python process each time. A call that runs too long, or kills its worker,
resets the pool: the next call gets fresh processes."""

from __future__ import annotations

import logging
import multiprocessing
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from . import UnderlayError

try:  # POSIX only
    import resource
except ImportError:  # pragma: no cover - Windows has the Job Object limits of the installer
    resource = None  # type: ignore[assignment]

WORKERS = 2
MEMORY_BYTES = 3 * 1024**3
CPU_SECONDS = 120  # a runaway parse ends itself, whatever the caller's timeout does
MAX_FILES = 64
TASKS_PER_WORKER = 200  # a worker is replaced now and then: nothing a file did to one lasts
SECRET_ENV = re.compile(
    r"SECRET|TOKEN|PASSWORD|PASSWD|DATABASE|_KEY$|CREDENTIAL|DSN", re.IGNORECASE
)
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()
log = logging.getLogger(__name__)


def _init() -> None:
    for name in [k for k in os.environ if SECRET_ENV.search(k)]:
        del os.environ[name]  # the file parser never needs the server's secrets
    if resource is not None:
        for which, value in (
            (resource.RLIMIT_AS, MEMORY_BYTES),
            (resource.RLIMIT_CPU, CPU_SECONDS),
            (resource.RLIMIT_NOFILE, MAX_FILES),
        ):
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass  # a platform that refuses a limit still runs, under the container's own


def _get() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_init,
                max_tasks_per_child=TASKS_PER_WORKER,
            )
        return _pool


def reset() -> None:
    """Throws the workers away (a stuck one is killed); the next call starts new ones."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is None:
        return
    for proc in list(getattr(pool, "_processes", {}).values()):
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001, S110 - already gone
            pass
    pool.shutdown(wait=False, cancel_futures=True)


_slots = threading.BoundedSemaphore(WORKERS)


def run(func: Callable[..., Any], *args: Any, timeout_s: float = 30) -> Any:
    """func(*args) in a worker; UnderlayError passes through, anything else is a parse failure.
    Calls wait for a free worker first, so the time limit counts only the work itself and a call
    that merely queued is never taken for a stuck one."""
    if not _slots.acquire(timeout=timeout_s * 4):
        raise UnderlayError(
            "UNDERLAY_BUSY", "처리 대기열이 가득 찼습니다. 잠시 뒤 다시 시도하세요", 503
        )
    try:
        return _run(func, args, timeout_s)
    finally:
        _slots.release()


def _run(func: Callable[..., Any], args: tuple[Any, ...], timeout_s: float) -> Any:
    fut = _get().submit(func, *args)
    try:
        return fut.result(timeout_s)
    except FutureTimeout:
        reset()
        raise UnderlayError("UNDERLAY_TIMEOUT", "처리 시간이 너무 오래 걸립니다", 504) from None
    except BrokenProcessPool:
        reset()
        raise UnderlayError("UNDERLAY_FAILED", "파일을 처리하다 중단되었습니다") from None
    except UnderlayError:
        raise
    except Exception as exc:  # noqa: BLE001 - the library's own errors on corrupt input
        log.warning("underlay worker failed: %r", exc)  # detail stays on the server
        raise UnderlayError("UNDERLAY_PARSE_ERROR", "파일을 해석할 수 없습니다") from None
