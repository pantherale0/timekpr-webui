"""Tests for running async coroutines from synchronous code."""

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.common.asyncio_sync import run_async


async def _sample_coro():
    return 'ok'


def test_run_async_without_event_loop():
    assert run_async(_sample_coro()) == 'ok'


def test_run_async_with_running_event_loop():
    async def runner():
        return run_async(_sample_coro())

    assert asyncio.run(runner()) == 'ok'


def test_run_async_with_gevent_and_background_loop():
    pytest.importorskip('gevent')

    script = textwrap.dedent(
        '''
        from gevent import monkey
        monkey.patch_all()

        import asyncio
        import threading

        from src.common.asyncio_sync import run_async

        async def sample():
            return 'ok'

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run_loop():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        threading.Thread(target=run_loop, daemon=True).start()
        ready.wait()

        result = {}
        error = {}

        def call_from_background_thread():
            try:
                result['value'] = run_async(sample())
            except Exception as exc:
                error['value'] = repr(exc)

        worker = threading.Thread(target=call_from_background_thread)
        worker.start()
        worker.join()

        if error:
            raise SystemExit(error['value'])
        if result.get('value') != 'ok':
            raise SystemExit(result)
        '''
    )

    completed = subprocess.run(
        [sys.executable, '-c', script],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "run_async failed under gevent: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
