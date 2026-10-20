from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.app.download_control import DownloadOptions, download_with_control


class DownloadControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_transfer_is_cancelled_retried_and_finishes(self):
        attempts = 0
        cancelled = []
        progress = []

        async def transfer(callback):
            nonlocal attempts
            attempts += 1
            await callback(10, 100)
            if attempts == 1:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
            await callback(100, 100)
            return "saved.mp4"

        options = DownloadOptions(idle_timeout_seconds=0.03, max_retries=1, retry_delay_seconds=0)
        stalled = AsyncMock()
        result = await download_with_control(transfer, options, lambda a, b: progress.append((a, b)), on_stalled=stalled)
        self.assertEqual(result, "saved.mp4")
        self.assertEqual(attempts, 2)
        self.assertEqual(cancelled, [True])
        self.assertEqual(progress[-1], (100, 100))
        stalled.assert_awaited_once()

    async def test_repeated_identical_progress_does_not_reset_idle_timeout(self):
        async def transfer(callback):
            while True:
                await callback(10, 100)
                await asyncio.sleep(0.005)

        with self.assertRaisesRegex(TimeoutError, "进度"):
            await download_with_control(transfer, DownloadOptions(idle_timeout_seconds=0.02, max_retries=0))

    async def test_user_cancellation_does_not_retry_or_leak_transfer(self):
        cancelled = asyncio.Event()
        started = asyncio.Event()
        async def transfer(callback):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        task = asyncio.create_task(download_with_control(transfer, DownloadOptions()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_speed_limit_delays_transfer_without_causing_idle_timeout(self):
        async def transfer(callback):
            await callback(2048, 2048)
            return "saved.mp4"
        started = asyncio.get_running_loop().time()
        await download_with_control(transfer, DownloadOptions(speed_limit_kib=20, idle_timeout_seconds=0.02, max_retries=0))
        self.assertGreaterEqual(asyncio.get_running_loop().time() - started, 0.09)

    async def test_total_timeout_bounds_a_transfer_that_keeps_progressing(self):
        async def transfer(callback):
            for current in range(1000):
                await callback(current, 1000)
                await asyncio.sleep(0.005)
        with self.assertRaisesRegex(TimeoutError, "总时长"):
            await download_with_control(transfer, DownloadOptions(task_timeout_seconds=0.03, max_retries=0))

    def test_invalid_concurrency_and_limits_are_rejected(self):
        for values in ({"concurrent_downloads": 0}, {"speed_limit_kib": -1}, {"max_retries": 99}):
            with self.assertRaises(ValueError):
                DownloadOptions(**values)

    async def test_speed_is_cleared_again_if_resumed_transfer_stalls_again(self):
        waiting = AsyncMock()
        async def transfer(callback):
            await callback(10, 100)
            await asyncio.sleep(0.04)
            await callback(20, 100)
            await asyncio.sleep(0.04)
            return "saved.mp4"
        with patch("backend.app.download_control.WAITING_SPEED_RESET_SECONDS", 0.01):
            await download_with_control(transfer, DownloadOptions(idle_timeout_seconds=0.1), on_waiting=waiting)
        self.assertEqual(waiting.await_count, 2)
