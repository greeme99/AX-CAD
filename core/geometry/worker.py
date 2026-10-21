"""Persistent spawn pool for kernel jobs. Only picklable args/results (BREP bytes, dicts) cross."""

import os
import resource
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from core.geometry.errors import GeomError

TIMEOUT_S = 30
WORKER_MEMORY_BYTES = 4 * 1024**3  # imported STEP/IGES is untrusted input: cap each worker
KERNEL_TMP_PREFIX = "axcad-kernel-"  # jobs' temp dirs, removed if their worker is killed
MAX_WORKERS = 2
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()
# no more jobs in flight than workers: a job's timeout then measures its own run, never the wait
# behind other users' jobs (the executor would pre-queue one more, which could not be cancelled)
_slots = threading.BoundedSemaphore(MAX_WORKERS)


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


def _limit() -> None:
    # ponytail: RLIMIT_AS is enforced on Linux only (macOS ignores it), container limits in deploy
    try:
        resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY_BYTES, WORKER_MEMORY_BYTES))
    except (ValueError, OSError):
        pass


def _get() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            # one job per worker at a time (Interface_Static is process-global); import OCP ~0.5 s
            _pool = ProcessPoolExecutor(
                max_workers=MAX_WORKERS, mp_context=get_context("spawn"), initializer=_limit
            )
        return _pool


def _reset(pool: ProcessPoolExecutor) -> None:
    global _pool
    with _lock:
        if _pool is pool:
            _pool = None
    killed_at = time.time()
    # ponytail: private _processes to kill a hung worker, no public API; revisit on Python upgrade
    for p in list(getattr(pool, "_processes", {}).values()):
        p.kill()
    pool.shutdown(wait=False, cancel_futures=True)
    # a killed job never runs its cleanup; /tmp is RAM (tmpfs) in deploy. Newer dirs belong to
    # jobs of the next pool.
    for d in Path(tempfile.gettempdir()).glob(KERNEL_TMP_PREFIX + "*"):
        try:
            if d.stat().st_mtime < killed_at:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def run_kernel(fn: Callable[..., Any], *args: Any, timeout_s: float = TIMEOUT_S) -> Any:
    if not _slots.acquire(timeout=timeout_s):  # busy: give up waiting, kill nobody
        raise GeomError("SERVER_BUSY", "Geometry kernel busy, retry later", 503)
    try:
        pool = _get()
        try:
            return pool.submit(_quiet, fn, args).result(timeout_s)
        except BrokenProcessPool:
            _reset(pool)
            raise GeomError("GEOM_KERNEL_CRASH", "Geometry kernel crashed", 422) from None
        except TimeoutError:
            _reset(pool)
            raise GeomError("GEOM_KERNEL_TIMEOUT", "Geometry kernel timed out", 422) from None
    finally:
        _slots.release()
