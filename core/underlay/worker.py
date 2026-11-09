"""Underlay files are untrusted input read by native code (PDFium) or large XML: every parse and
render runs in a worker process with a memory cap and a time limit. Unlike the DXF reader (one
process per call) the workers are kept, so a zoom or pan that needs a new image does not pay
the start-up of a Python process each time. A call that runs too long, or kills its worker,
resets the pool: the next call gets fresh processes."""

from __future__ import annotations

import logging
import multiprocessing
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
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()
log = logging.getLogger(__name__)


def _init() -> None:
    if resource is not None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        except (ValueError, OSError):
            pass  # a platform that refuses the limit still runs, under the container's own


def _get() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                WORKERS, mp_context=multiprocessing.get_context("spawn"), initializer=_init
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


def run(func: Callable[..., Any], *args: Any, timeout_s: float = 30) -> Any:
    """func(*args) in a worker; UnderlayError passes through, anything else is a parse failure."""
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
