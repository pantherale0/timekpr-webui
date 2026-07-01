"""Helpers for running async coroutines from synchronous code."""

from __future__ import annotations

import asyncio
import concurrent.futures


def _run_in_new_loop(coro):
    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()
        asyncio.set_event_loop(None)


def _foreign_running_event_loop():
    """Return a running loop owned by another thread, if one exists."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None

    if not loop.is_running():
        return None

    try:
        if asyncio.get_running_loop() is loop:
            return None
    except RuntimeError:
        pass

    return loop


def run_async(coro):
    """Run a coroutine from synchronous code.

    Works when no event loop is running, when the caller is already inside one,
    and under gevent/gunicorn where a background asyncio loop is active but
  the caller is synchronous code in another thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        try:
            return asyncio.run(coro)
        except RuntimeError as exc:
            message = str(exc)
            if (
                "cannot be called from a running event loop" not in message
                and "another loop is running" not in message
            ):
                raise

        foreign_loop = _foreign_running_event_loop()
        if foreign_loop is not None:
            future = asyncio.run_coroutine_threadsafe(coro, foreign_loop)
            return future.result()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_run_in_new_loop, coro).result()

    if asyncio.current_task(loop) is not None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_run_in_new_loop, coro).result()

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
