from __future__ import annotations

import asyncio
import gc
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.download_control import DownloadOptions
from backend.app.telegram_worker import TelegramWorker
from backend.app.bot_handler import BotCommandHandler


class DownloadSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.directory.name)
        self.settings = Settings(data_dir=self.root / "data", download_dir=self.root / "downloads", static_dir=self.root / "static")
        self.settings.ensure_directories()
        self.database = Database(self.settings.data_dir / "state.db")
        with patch("backend.app.config.get_settings", return_value=self.settings):
            self.main = importlib.import_module("backend.app.main")
        for name, value in {"database": self.database, "settings": self.settings, "ADMIN_TOKENS": {"test-token"}}.items():
            patcher = patch.object(self.main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_api_validates_and_persists_options_without_telegram_configuration(self):
        with patch.object(self.main.download_queue_manager, "on_download_finished", AsyncMock()):
            client = TestClient(self.main.app)
            self.assertEqual(client.put("/api/download-settings", json={"concurrent_downloads": 2}).status_code, 401)
            headers = {"X-Admin-Token": "test-token"}
            self.assertEqual(client.put("/api/download-settings", json={"concurrent_downloads": 0}, headers=headers).status_code, 422)
            response = client.put("/api/download-settings", json={"concurrent_downloads": 2, "speed_limit_kib": 500}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/download-settings", headers=headers).json()["speed_limit_kib"], 500)
        restored = Settings(data_dir=self.settings.data_dir)
        restored.load_from_mapping(self.database.get_config())
        self.assertEqual(restored.download_options.concurrent_downloads, 2)
        self.assertEqual(restored.download_options.speed_limit_kib, 500)

    async def test_lowering_concurrency_waits_and_increasing_it_drains_queue(self):
        self.settings.download_options.concurrent_downloads = 1
        first = self.database.add_download(1, 100, "bot", "first.mp4", status="downloading", source="rule")
        second = self.database.add_download(2, 100, "bot", "second.mp4", status="pending", source="rule")
        manager = self.main.DownloadQueueManager(self.database)
        self.assertFalse(await manager.try_start_download(second))
        worker = SimpleNamespace(restore_queued_download=AsyncMock())
        with patch.object(self.main, "worker", worker):
            await manager.on_download_finished(first)
            self.assertEqual(self.database.get_download(second)["status"], "queued")
            self.settings.download_options.concurrent_downloads = 2
            await manager.on_download_finished(0)
            await asyncio.sleep(0)
        self.assertEqual(self.database.get_download(second)["status"], "downloading")
        worker.restore_queued_download.assert_awaited_once_with(second, 2, 100)

    async def test_both_bot_and_rule_transfers_timeout_and_clear_speed(self):
        self.settings.download_options = DownloadOptions(idle_timeout_seconds=0.02, max_retries=0)
        download_id = self.database.add_download(1, 100, "bot", "file.mp4", status="downloading")
        self.database.update_download(download_id, download_speed=123456)
        stopped = asyncio.Event()

        async def hang(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        worker = TelegramWorker(self.settings, self.database)
        worker._ensure_client_connected = AsyncMock()
        with self.assertRaises(TimeoutError):
            await worker._download_media_with_retry(SimpleNamespace(download_media=hang), file=self.root / "file.mp4", download_id=download_id)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.database.get_download(download_id)["download_speed"], 0)
        stopped.clear()
        bot = BotCommandHandler(self.settings, self.database, None)
        client = SimpleNamespace(is_connected=lambda: True, download_media=hang)
        with self.assertRaises(TimeoutError):
            await bot._download_with_control(client, object(), download_id, file=self.root / "file.mp4", progress_callback=None)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.database.get_download(download_id)["download_speed"], 0)

    async def test_version_endpoint_uses_runtime_version_and_does_not_block_event_loop(self):
        with patch.object(self.main.updater, "check", return_value={"current": "bata.20261020000000", "target": "1.0.61", "available": True, "error": ""}):
            result = await self.main.version_check()
        self.assertEqual(result["current_version"], "bata.20261020000000")
        self.assertTrue(result["has_update"])

    async def test_slow_telegram_connection_does_not_block_web_startup(self):
        async def blocked():
            await asyncio.Event().wait()
        with patch.object(self.main, "_ensure_bot_handler_running", side_effect=blocked), patch.object(
            self.main.updater, "start_scheduler",
        ), patch.object(self.main, "bot_handler", None), patch.object(self.main.worker, "stop", AsyncMock()):
            context = self.main.lifespan(self.main.app)
            await asyncio.wait_for(context.__aenter__(), timeout=0.1)
            await context.__aexit__(None, None, None)

    async def test_bot_preflight_failure_releases_queue_slot(self):
        for missing in (False, True):
            download_id = self.database.add_download(1, 100, "bot", "file.mp4", status="downloading")
            queue = SimpleNamespace(on_download_finished=AsyncMock())
            bot = BotCommandHandler(self.settings, self.database, None, queue_manager=queue)
            bot._bot_client = SimpleNamespace(
                get_entity=AsyncMock(return_value=object(), side_effect=None if missing else ConnectionError("offline")),
                get_messages=AsyncMock(return_value=None),
            )
            await bot.restore_queued_download(self.database.get_download(download_id))
            self.assertEqual(self.database.get_download(download_id)["status"], "failed")
            queue.on_download_finished.assert_awaited_once_with(download_id)

    async def test_bot_metadata_request_cannot_hang_forever(self):
        self.settings.download_options.idle_timeout_seconds = 0.02
        download_id = self.database.add_download(1, 100, "bot", "file.mp4", status="downloading")
        async def blocked(*args):
            await asyncio.Event().wait()
        queue = SimpleNamespace(on_download_finished=AsyncMock())
        bot = BotCommandHandler(self.settings, self.database, None, queue_manager=queue)
        bot._bot_client = SimpleNamespace(get_entity=blocked)
        await asyncio.wait_for(bot.restore_queued_download(self.database.get_download(download_id)), 0.1)
        self.assertEqual(self.database.get_download(download_id)["status"], "failed")

    async def test_rule_client_connection_timeout_releases_queue_slot(self):
        self.settings.download_options.idle_timeout_seconds = 0.02
        rule_id = self.database.add_group_rule(chat_id=100)
        download_id = self.database.add_download(1, 100, "bot", "file.mp4", status="downloading", rule_id=rule_id)
        async def blocked():
            await asyncio.Event().wait()
        queue = SimpleNamespace(on_download_finished=AsyncMock())
        worker = TelegramWorker(self.settings, self.database, queue_manager=queue)
        worker._get_client = blocked
        await asyncio.wait_for(worker.restore_queued_download(download_id, 1, 100), 0.1)
        self.assertEqual(self.database.get_download(download_id)["status"], "failed")
        queue.on_download_finished.assert_awaited_once_with(download_id)

    async def test_bot_zero_retries_really_attempts_the_transfer_once(self):
        self.settings.download_options.max_retries = 0
        bot = BotCommandHandler(self.settings, self.database, None)
        bot._bot_client = SimpleNamespace(
            is_connected=lambda: True,
            download_media=AsyncMock(side_effect=ConnectionError("offline")),
            edit_message=AsyncMock(),
        )
        message = SimpleNamespace(id=42, video=SimpleNamespace(file_name="file.mp4"), document=None,
            photo=None, file=SimpleNamespace(size=1024), message="", text="", grouped_id=None)
        event = SimpleNamespace(message=message, chat_id=100,
            get_sender=AsyncMock(return_value=SimpleNamespace(id=1)),
            reply=AsyncMock(return_value=SimpleNamespace(id=1000, chat_id=100)))
        await bot._handle_media_message(event)
        self.assertEqual(bot._bot_client.download_media.await_count, 1)
        self.assertEqual(self.database.search_downloads()["items"][0]["status"], "failed")
