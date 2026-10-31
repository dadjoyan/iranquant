"""Safely run a coroutine from synchronous code, including running event loops."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


def _run_in_thread(coro: Coroutine[Any, Any, T]) -> T:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion from synchronous code.

    A plain ``asyncio.run`` is used when no event loop is running.  Inside an
    already-running loop (Jupyter, Colab, async applications) the coroutine is
    executed on a fresh loop in a background thread, which avoids the usual
    "asyncio.run() cannot be called from a running event loop" error.

    Args:
        coro: An already-created coroutine object.

    Returns:
        Whatever the coroutine returns.

    Raises:
        BaseException: Any exception raised by the coroutine is re-raised in
            the calling thread.

    Examples:
        >>> async def answer() -> int:
        ...     return 42
        >>> run_sync(answer())
        42
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return _run_in_thread(coro)
