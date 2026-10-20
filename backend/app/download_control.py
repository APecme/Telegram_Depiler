"""Shared transfer limits for Bot and channel downloads."""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from telethon.errors import FloodWaitError, RPCError

WAITING_SPEED_RESET_SECONDS = 5


class DownloadOptions(BaseModel):
    concurrent_downloads: int = Field(default=5, ge=1, le=32)
    speed_limit_kib: int = Field(default=0, ge=0, le=1048576)
    idle_timeout_seconds: float = Field(default=90, ge=0.01, le=3600)
    task_timeout_seconds: float = Field(default=0, ge=0, le=604800)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: float = Field(default=3, ge=0, le=300)


async def _notify(callback, *args):
    if callback is not None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result


async def download_with_control(
    transfer: Callable[[Callable], Awaitable[Any]], options: DownloadOptions,
    progress_callback=None, *, on_stalled=None, before_attempt=None, on_waiting=None,
) -> Any:
    """Cancel stalled attempts, throttle each task, and retry a bounded number of times.

    Retries restart the file from the beginning. Rate limiting waits are excluded
    from idle detection; task timeouts still bound the entire attempt.
    """
    for attempt in range(options.max_retries + 1):
        started = time.monotonic()
        last_advance = started
        last_bytes = 0
        throttling = False
        waiting_reported = False

        async def progress(current: int, total: int):
            nonlocal last_advance, last_bytes, throttling, waiting_reported
            if current > last_bytes:
                last_advance = time.monotonic()
                last_bytes = current
                waiting_reported = False
            if options.speed_limit_kib:
                delay = current / (options.speed_limit_kib * 1024) - (time.monotonic() - started)
                if delay > 0:
                    throttling = True
                    try:
                        await asyncio.sleep(delay)
                    finally:
                        throttling = False
                        last_advance = time.monotonic()
            await _notify(progress_callback, current, total)

        async def run():
            await _notify(before_attempt, attempt)
            return await transfer(progress)

        task = asyncio.create_task(run())
        try:
            interval = max(0.005, min(options.idle_timeout_seconds / 4, 1))
            while not task.done():
                await asyncio.wait([task], timeout=interval)
                if task.done():
                    break
                now = time.monotonic()
                if not throttling and now - last_advance >= min(WAITING_SPEED_RESET_SECONDS, options.idle_timeout_seconds) and not waiting_reported:
                    await _notify(on_waiting)
                    waiting_reported = True
                if options.task_timeout_seconds and now - started >= options.task_timeout_seconds:
                    raise TimeoutError("下载超过单次任务总时长限制")
                if not throttling and now - last_advance >= options.idle_timeout_seconds:
                    raise TimeoutError(f"下载进度连续 {options.idle_timeout_seconds:g} 秒无变化")
            result = await task
            if result is None:
                raise RuntimeError("Telegram 未返回下载文件")
            return result
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, RPCError) as exc:
            # Stop the old transfer before starting another attempt.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await _notify(on_stalled, exc, attempt)
            if attempt >= options.max_retries:
                raise
            delay = max(options.retry_delay_seconds, getattr(exc, "seconds", 0) if isinstance(exc, FloodWaitError) else 0)
            await asyncio.sleep(delay)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
