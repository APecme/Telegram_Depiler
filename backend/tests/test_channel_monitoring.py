from __future__ import annotations

import asyncio
import gc
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telethon.tl.custom.message import Message
from telethon.tl.types import Document, DocumentAttributeFilename, DocumentAttributeVideo, MessageMediaDocument, PeerChannel

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.telegram_worker import TelegramWorker


def video_message(message_id: int = 1) -> Message:
    return Message(
        id=message_id,
        peer_id=PeerChannel(100),
        media=MessageMediaDocument(document=Document(
            id=message_id, access_hash=42, file_reference=b"", date=None,
            mime_type="video/mp4", size=1024, dc_id=2,
            attributes=[DocumentAttributeVideo(duration=1, w=16, h=16)],
        )),
    )


class VideoRuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(self.directory.name)
        self.database = Database(root / "state.db")
        self.worker = TelegramWorker(Settings(data_dir=root, download_dir=root / "downloads"), self.database)
        self.rule = {"id": 1, "include_extensions": "mp4", "save_dir": str(root / "downloads")}

    def test_video_without_filename_matches_mp4_filter(self):
        message = video_message()
        self.assertIsNone(message.file.name)
        self.assertTrue(self.worker._should_download_by_rule(message, self.rule))

    def test_video_without_filename_does_not_match_other_extensions(self):
        self.assertFalse(self.worker._should_download_by_rule(video_message(), dict(self.rule, include_extensions="jpg")))

    def test_original_video_filename_is_preserved(self):
        message = video_message()
        message.document.attributes.append(DocumentAttributeFilename(file_name="original.MP4"))
        self.assertTrue(self.worker._should_download_by_rule(message, self.rule))

    async def download_two_videos(self):
        async def save(message, *, file, progress_callback=None, download_id=None):
            file.write_bytes(b"video")
            return str(file)

        self.worker._download_media_with_retry = AsyncMock(side_effect=save)
        with patch("backend.app.telegram_worker.generate_cover_cache", AsyncMock(return_value=None)):
            for message_id in (1, 2):
                await self.worker._download_file_by_rule(
                    video_message(message_id), self.rule, SimpleNamespace(id=100, title="channel"), None,
                )
        return self.database.search_downloads()["items"]

    async def test_two_unnamed_videos_download_with_mp4_names(self):
        records = await self.download_two_videos()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["status"], "completed")
            self.assertTrue(record["file_name"].endswith(".mp4"))
            self.assertTrue(Path(record["file_path"]).is_file())

    async def test_completed_history_survives_restart_and_missing_file(self):
        completed = await self.download_two_videos()
        Path(completed[0]["file_path"]).unlink()
        reopened = Database(self.database.db_path)
        records = reopened.search_downloads()["items"]
        self.assertEqual(len(records), 2)
        self.assertEqual({record["id"] for record in records}, {record["id"] for record in completed})
        self.assertFalse(next(record for record in records if record["id"] == completed[0]["id"])["file_exists"])


class ListenerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        settings = Settings(data_dir=root / "data", download_dir=root / "downloads", static_dir=root / "static")
        with patch("backend.app.config.get_settings", return_value=settings):
            cls.main = importlib.import_module("backend.app.main")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        patcher = patch.object(self.main, "database", Mock(get_login_state=Mock(return_value=None)))
        patcher.start()
        self.addCleanup(patcher.stop)
        lock_patch = patch.object(self.main, "_bot_start_lock", asyncio.Lock())
        lock_patch.start()
        self.addCleanup(lock_patch.stop)

    async def test_relogin_restores_listener_when_bot_handler_already_exists(self):
        worker = TelegramWorker(Settings(bot_token="token", bot_username="bot"), Mock())
        old_client = SimpleNamespace(disconnect=AsyncMock())
        worker._client = old_client
        worker._event_handler_added = True
        await worker.restart_client(reset_session=False)
        new_client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=True), add_event_handler=Mock(),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42, username="user")),
        )
        worker._get_client = AsyncMock(return_value=new_client)
        worker.catch_up_missed_group_messages = AsyncMock()
        handler = SimpleNamespace(user_client=old_client, _bot_client=object())
        with patch.object(self.main, "worker", worker), patch.object(self.main, "bot_handler", handler), patch.object(
            self.main, "settings", worker.settings,
        ):
            await self.main._ensure_bot_handler_running()
            await self.main._ensure_bot_handler_running()
            await asyncio.sleep(0)
        new_client.add_event_handler.assert_called_once()
        self.assertIs(handler.user_client, new_client)
        self.assertTrue(worker._event_handler_added)
        worker.catch_up_missed_group_messages.assert_awaited_once()

    async def test_failed_bot_start_can_be_retried(self):
        client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42, username="user")),
        )
        worker = SimpleNamespace(_get_client=AsyncMock(return_value=client), _event_handler_added=False)
        first = SimpleNamespace(start=AsyncMock(side_effect=ConnectionError("unreachable")), stop=AsyncMock())
        second = SimpleNamespace(start=AsyncMock(), _bot_client=object())
        worker.set_bot_client = Mock()
        worker.start_bot_listener = AsyncMock()
        worker.catch_up_missed_group_messages = AsyncMock()
        with patch.object(self.main, "worker", worker), patch.object(self.main, "bot_handler", None), patch.object(
            self.main, "settings", Settings(bot_token="token", bot_username="bot"),
        ), patch.object(self.main, "BotCommandHandler", side_effect=[first, second]) as constructor:
            await self.main._ensure_bot_handler_running()
            await self.main._ensure_bot_handler_running()
            await asyncio.sleep(0)
            self.assertEqual(constructor.call_count, 2)
            self.assertIs(self.main.bot_handler, second)
        first.stop.assert_awaited_once()

    async def test_concurrent_login_checks_start_only_one_bot(self):
        client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42, username="user")),
        )
        worker = TelegramWorker(Settings(bot_token="token", bot_username="bot"), Mock())
        worker._get_client = AsyncMock(return_value=client)
        worker.catch_up_missed_group_messages = AsyncMock()
        client.add_event_handler = Mock()

        async def delayed_start():
            await asyncio.sleep(0)

        handler = SimpleNamespace(start=AsyncMock(side_effect=delayed_start), _bot_client=object())
        with patch.object(self.main, "worker", worker), patch.object(self.main, "bot_handler", None), patch.object(
            self.main, "settings", worker.settings,
        ), patch.object(self.main, "BotCommandHandler", return_value=handler) as constructor:
            await asyncio.gather(self.main._ensure_bot_handler_running(), self.main._ensure_bot_handler_running())
            await asyncio.sleep(0)
        constructor.assert_called_once()
        client.add_event_handler.assert_called_once()


if __name__ == "__main__":
    unittest.main()
