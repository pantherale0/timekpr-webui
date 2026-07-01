"""Tests for asyncio compatibility helpers."""

import asyncio
import time

import pytest


def test_run_async_without_event_loop():
    from src.common.async_compat import run_async

    async def sample():
        return 'ok'

    assert run_async(sample()) == 'ok'


def test_run_async_rejects_nested_coroutine_call():
    from src.common.async_compat import run_async

    async def sample():
        return 'ok'

    async def runner():
        return run_async(sample())

    with pytest.raises(RuntimeError, match='cannot be called from inside a coroutine'):
        asyncio.run(runner())


def test_run_async_with_background_event_loop_under_gevent():
    gevent = pytest.importorskip('gevent')
    from gevent import monkey
    from gevent.monkey import get_original

    monkey.patch_all(thread=False, subprocess=False)

    from src.common.async_compat import run_async

    async def sample():
        return 'ok'

    RealThread = get_original('threading', 'Thread')

    def run_background_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = RealThread(target=run_background_loop, daemon=True)
    thread.start()
    time.sleep(0.2)

    assert run_async(sample()) == 'ok'
