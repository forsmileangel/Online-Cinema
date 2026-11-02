"""Two bounded thread pools shared by every request, instead of a new executor per request.

- ``fanout`` runs request-level fan-outs: a multi-source search, the alternatives lookup, name
  translation, receiver discovery. Its tasks may hand leaf work to ``io`` and wait for it.
- ``io`` runs the leaf page fetches one site makes while building a page (home rows, the detail and
  play pages of a video). An ``io`` task never waits on either pool, so the two can never deadlock.

A per-request ThreadPoolExecutor spawned up to eight threads each time and, when a request gave up
waiting, left them running until their own socket timeouts. These pools cap that at a fixed number.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

fanout = ThreadPoolExecutor(max_workers=16, thread_name_prefix="fanout")
io = ThreadPoolExecutor(max_workers=24, thread_name_prefix="io")


def cancel_pending(futures: Iterable[Future]) -> None:
    """Drop the tasks that have not started; a running one keeps its worker until it finishes."""
    for future in futures:
        future.cancel()


def map_in(pool: ThreadPoolExecutor, fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """Like ``pool.map`` but every item's exception is raised in order, after all of them ran."""
    futures = [pool.submit(fn, item) for item in items]
    return [future.result() for future in futures]


def each_done(pool: ThreadPoolExecutor, fn: Callable[[T], R], items: Iterable[T], timeout: float | None = None):
    """Yield ``(item, future)`` as the calls finish; on timeout the rest are cancelled and left out."""
    futures = {pool.submit(fn, item): item for item in items}
    try:
        for future in as_completed(futures, timeout=timeout):
            yield futures[future], future
    except FuturesTimeout:
        pass
    finally:
        cancel_pending(futures)
