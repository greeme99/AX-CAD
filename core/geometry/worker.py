"""Persistent spawn pool for kernel jobs. Only picklable args/results (BREP bytes, dicts) cross."""

import os
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from typing import Any

from core.geometry.errors import GeomError

TIMEOUT_S = 30
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


def _quiet(fn: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    """Worker side: OCCT writers print to fd 1; silence it for the duration of the job."""
    sys.stdout.flush()
    saved = os.dup(1)
    with open(os.devnull, "w") as null:
        os.dup2(null.fileno(), 1)
        try:
            return fn(*args)
        finally:
            os.dup2(saved, 1)
            os.close(saved)


def _get() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            # one job per worker at a time (Interface_Static is process-global); import OCP ~0.5 s
            _pool = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))
        return _pool


def _reset(pool: ProcessPoolExecutor) -> None:
    global _pool
    with _lock:
        if _pool is pool:
            _pool = None
    # ponytail: private _processes to kill a hung worker, no public API; revisit on Python upgrade
    for p in list(getattr(pool, "_processes", {}).values()):
        p.kill()
    pool.shutdown(wait=False, cancel_futures=True)


def run_kernel(fn: Callable[..., Any], *args: Any, timeout_s: float = TIMEOUT_S) -> Any:
    pool = _get()
    try:
        return pool.submit(_quiet, fn, args).result(timeout_s)
    except BrokenProcessPool:
        _reset(pool)
        raise GeomError("GEOM_KERNEL_CRASH", "Geometry kernel crashed", 422) from None
    except TimeoutError:
        _reset(pool)
        raise GeomError("GEOM_KERNEL_TIMEOUT", "Geometry kernel timed out", 422) from None
