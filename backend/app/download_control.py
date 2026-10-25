"""Shared transfer limits for Bot and channel downloads."""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from telethon.errors import FloodWaitError, RPCError

logger = logging.getLogger(__name__)

WAITING_SPEED_RESET_SECONDS = 5

#: Telegram also answers flood waits it has no telethon class for, such as
#: ``FLOOD_PREMIUM_WAIT_11``. Only the message carries the seconds there.
FLOOD_WAIT_PATTERN = re.compile(r"FLOOD(?:_[A-Z]+)*_WAIT_(\d+)", re.IGNORECASE)
FLOOD_WAIT_MARGIN_SECONDS = 2
FLOOD_WAIT_JITTER_SECONDS = 2
#: Flood waits are Telegram-side throttling, not transfer failures: they get
#: their own budget so they never eat the retries meant for broken transfers.
MAX_FLOOD_RETRIES = 5


class DownloadOptions(BaseModel):
    concurrent_downloads: int = Field(default=5, ge=1, le=32)
    speed_limit_kib: int = Field(default=0, ge=0, le=1048576)
    idle_timeout_seconds: float = Field(default=90, ge=0.01, le=3600)
    task_timeout_seconds: float = Field(default=0, ge=0, le=604800)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: float = Field(default=3, ge=0, le=300)


def flood_wait_seconds(exc: BaseException) -> float:
    """Return how long Telegram asked us to wait, or 0 for any other error.

    telethon 1.36 has no class for ``FLOOD_PREMIUM_WAIT_N``, so it falls back to
    a plain ``FloodError`` (code 420) whose ``seconds`` attribute does not exist.
    """
    if isinstance(exc, FloodWaitError):
        return float(getattr(exc, "seconds", 0) or 0)
    if isinstance(exc, RPCError) and getattr(exc, "code", None) == 420:
        match = FLOOD_WAIT_PATTERN.search(getattr(exc, "message", "") or "")
        if match:
            return float(match.group(1))
    return 0.0


class FloodGate:
    """Cooldown shared by every transfer of one Telegram account.

    Flood limits apply per account, so one transfer being told to wait means all
    of them must pause; retrying independently makes the wait snowball until
    each transfer burns its retry budget.
    """

    def __init__(self) -> None:
        self._not_before = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self._not_before - time.monotonic())

    def block(self, seconds: float) -> None:
        self._not_before = max(self._not_before, time.monotonic() + seconds)

    async def wait(self) -> None:
        while (delay := self.remaining) > 0:
            await asyncio.sleep(delay)


async def _notify(callback, *args):
    if callback is not None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result


async def download_with_control(
    transfer: Callable[[Callable], Awaitable[Any]], options: DownloadOptions,
    progress_callback=None, *, on_stalled=None, before_attempt=None, on_waiting=None,
    flood_gate: FloodGate | None = None,
) -> Any:
    """Cancel stalled attempts, throttle each task, and retry a bounded number of times.

    Retries restart the file from the beginning. Rate limiting waits are excluded
    from idle detection; task timeouts still bound the entire attempt. Flood waits
    pause every transfer sharing ``flood_gate`` and are retried on their own budget.
    """
    attempt = 0
    flood_retries = 0
    while True:
        if flood_gate is not None and flood_gate.remaining > 0:
            await _notify(on_waiting)
            await flood_gate.wait()

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
            flood_seconds = flood_wait_seconds(exc)
            if flood_seconds and flood_retries < MAX_FLOOD_RETRIES:
                flood_retries += 1
                delay = flood_seconds + FLOOD_WAIT_MARGIN_SECONDS + random.uniform(0, FLOOD_WAIT_JITTER_SECONDS)
                logger.warning(
                    "Telegram 限流 %s 秒（%s），等待 %.1f 秒后重试（限流重试 %s/%s）",
                    flood_seconds, exc, delay, flood_retries, MAX_FLOOD_RETRIES,
                )
                if flood_gate is not None:
                    flood_gate.block(delay)
                    await flood_gate.wait()
                else:
                    await asyncio.sleep(delay)
                continue
            await _notify(on_stalled, exc, attempt)
            if attempt >= options.max_retries:
                raise
            delay = max(options.retry_delay_seconds, flood_seconds)
            await asyncio.sleep(delay)
            attempt += 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
