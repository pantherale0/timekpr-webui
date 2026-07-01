"""Helpers for running asyncio coroutines from synchronous Flask/gevent code."""

from __future__ import annotations

import asyncio


def run_async(coro):
    """Run a coroutine from synchronous code.

    Works when no event loop is running, and under gevent/gunicorn when an
    asyncio loop is already active in the worker process.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(coro)
        except RuntimeError as exc:
            if 'cannot be called from a running event loop' not in str(exc):
                raise
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
            return loop.run_until_complete(coro)

    if asyncio.current_task() is not None:
        raise RuntimeError(
            'run_async() cannot be called from inside a coroutine; await directly instead'
        )

    return asyncio.run_coroutine_threadsafe(coro, loop).result()
