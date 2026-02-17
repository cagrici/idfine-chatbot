"""Lightweight asyncio-based periodic task scheduler."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


class _Task:
    __slots__ = ("name", "coro_func", "interval", "handle")

    def __init__(self, name: str, coro_func: Callable[[], Coroutine], interval: float):
        self.name = name
        self.coro_func = coro_func
        self.interval = interval
        self.handle: asyncio.Task | None = None


class AsyncScheduler:
    """Simple periodic task runner on top of asyncio.

    Usage::

        scheduler = AsyncScheduler()
        scheduler.register("my_task", my_async_func, interval_seconds=60)
        await scheduler.start()   # non-blocking, spawns background tasks
        ...
        await scheduler.stop()    # graceful cancel
    """

    def __init__(self):
        self._tasks: dict[str, _Task] = {}
        self._stop_event = asyncio.Event()

    def register(self, name: str, coro_func: Callable[[], Coroutine], interval_seconds: float):
        self._tasks[name] = _Task(name, coro_func, interval_seconds)
        logger.info("Scheduler: registered task '%s' (every %ds)", name, int(interval_seconds))

    async def start(self):
        self._stop_event.clear()
        for task in self._tasks.values():
            task.handle = asyncio.create_task(self._run_loop(task))
        logger.info("Scheduler: started %d task(s)", len(self._tasks))

    async def stop(self):
        self._stop_event.set()
        for task in self._tasks.values():
            if task.handle and not task.handle.done():
                task.handle.cancel()
                try:
                    await task.handle
                except asyncio.CancelledError:
                    pass
        logger.info("Scheduler: stopped")

    async def run_now(self, name: str):
        """Run a registered task immediately (for manual trigger)."""
        task = self._tasks.get(name)
        if not task:
            raise ValueError(f"Unknown task: {name}")
        await task.coro_func()

    async def _run_loop(self, task: _Task):
        # Initial delay to let the app fully start
        await asyncio.sleep(10)
        logger.info("Scheduler: task '%s' loop started", task.name)

        while not self._stop_event.is_set():
            start = datetime.now(timezone.utc)
            try:
                await task.coro_func()
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                logger.info(
                    "Scheduler: task '%s' completed in %.1fs", task.name, elapsed
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler: task '%s' failed", task.name)

            # Wait for the next interval or until stopped
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=task.interval
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # interval elapsed, run again


scheduler = AsyncScheduler()
