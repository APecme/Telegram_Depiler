from __future__ import annotations

import asyncio
import gc
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.tl.types import Channel, User

from backend.app.bot_handler import BotCommandHandler
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.telegram_links import TelegramLink

PRIVATE_LINK = "https://t.me/c/3411693582/17748?thread=17724"
CHANNEL_ID = -1003411693582


def _channel() -> Channel:
    return Channel(id=3411693582, title="测试频道", photo=None, date=None)


def _video_message(message_id: int = 17748):
    return SimpleNamespace(
        id=message_id,
        video=SimpleNamespace(file_name="clip.mp4"),
        document=None,
        photo=None,
        audio=None,
        voice=None,
        video_note=None,
        file=SimpleNamespace(size=2048),
    )


class LinkDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.directory.name)
        self.settings = Settings(
            data_dir=self.root / "data",
            download_dir=self.root / "downloads",
            static_dir=self.root / "static",
        )
        self.settings.ensure_directories()
        self.database = Database(self.settings.data_dir / "state.db")

    def _handler(self, *, message=None, entity=None, user_client=None, queue_manager=None):
        self.user_client = user_client or SimpleNamespace(
            get_entity=AsyncMock(return_value=entity or _channel()),
            get_messages=AsyncMock(return_value=message if message is not None else _video_message()),
        )
        bot = BotCommandHandler(self.settings, self.database, self.user_client, queue_manager=queue_manager)
        bot._bot_client = SimpleNamespace(
            get_messages=AsyncMock(return_value=None),
            edit_message=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(id=1, chat_id=999)),
        )
        bot.restore_queued_download = AsyncMock()
        return bot

    @staticmethod
    def _event(text: str = PRIVATE_LINK):
        return SimpleNamespace(
            message=SimpleNamespace(id=500, message=text, text=text, video=None, document=None),
            chat_id=999,
            get_sender=AsyncMock(return_value=User(id=7)),
            reply=AsyncMock(return_value=SimpleNamespace(id=1000, chat_id=999)),
        )

    async def test_private_link_is_downloaded_with_the_user_account(self):
        bot = self._handler()
        await bot._handle_link_message(self._event(), TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))
        await asyncio.sleep(0)

        records = self.database.search_downloads()["items"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["source"], "link")
        self.assertEqual(record["chat_id"], CHANNEL_ID)
        self.assertEqual(record["message_id"], 17748)
        self.assertEqual(record["file_name"], "clip.mp4")
        self.assertEqual(record["file_size"], 2048)
        # 进度消息必须发回管理员与 Bot 的会话，而不是被下载的频道。
        self.assertEqual(record["reply_chat_id"], 999)
        bot.restore_queued_download.assert_awaited_once()
        self.user_client.get_messages.assert_awaited_once()

    async def test_link_without_media_is_rejected_without_a_record(self):
        bot = self._handler(message=SimpleNamespace(id=17748, video=None, document=None, photo=None, file=None))
        event = self._event()
        await bot._handle_link_message(event, TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))

        self.assertIn("没有可下载的媒体", event.reply.await_args.args[0])
        self.assertEqual(self.database.search_downloads()["items"], [])
        bot.restore_queued_download.assert_not_awaited()

    async def test_uncached_private_channel_warms_dialogs_before_failing(self):
        user_client = SimpleNamespace(
            get_entity=AsyncMock(side_effect=[ValueError("Could not find the input entity"), _channel()]),
            get_messages=AsyncMock(return_value=_video_message()),
            get_dialogs=AsyncMock(return_value=[]),
        )
        bot = self._handler(user_client=user_client)
        await bot._handle_link_message(self._event(), TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))
        await asyncio.sleep(0)

        user_client.get_dialogs.assert_awaited_once()
        self.assertEqual(user_client.get_entity.await_count, 2)
        self.assertEqual(len(self.database.search_downloads()["items"]), 1)

    async def test_unreadable_link_reports_an_actionable_error(self):
        user_client = SimpleNamespace(
            get_entity=AsyncMock(side_effect=ValueError("Cannot find any entity")),
            get_messages=AsyncMock(),
            get_dialogs=AsyncMock(return_value=[]),
        )
        bot = self._handler(user_client=user_client)
        event = self._event()
        await bot._handle_link_message(event, TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))

        self.assertIn("无法读取该链接指向的消息", event.reply.await_args.args[0])
        self.assertIn("已加入该群组/频道", event.reply.await_args.args[0])
        self.assertEqual(self.database.search_downloads()["items"], [])

    async def test_already_downloaded_file_is_not_downloaded_again(self):
        existing_id = self.database.add_download(
            message_id=1, chat_id=CHANNEL_ID, bot_username="bot", file_name="clip.mp4",
            status="completed", source="bot", tg_file_id=11, tg_access_hash=22,
        )
        self.database.update_download(existing_id, file_path=str(self.root / "downloads" / "clip.mp4"))
        message = _video_message()
        message.document = SimpleNamespace(id=11, access_hash=22)
        bot = self._handler(message=message)

        event = self._event()
        await bot._handle_link_message(event, TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))

        self.assertIn("已下载过", event.reply.await_args.args[0])
        self.assertIn(str(existing_id), event.reply.await_args.args[0])
        self.assertEqual(len(self.database.search_downloads()["items"]), 1)
        bot.restore_queued_download.assert_not_awaited()

    async def test_link_download_resumes_on_the_user_account(self):
        download_id = self.database.add_download(
            message_id=17748, chat_id=CHANNEL_ID, bot_username="bot", file_name="clip.mp4",
            status="downloading", source="link", file_size=2048, reply_chat_id=999,
            save_dir=str(self.root / "downloads"),
        )
        user_client = SimpleNamespace(
            is_connected=lambda: True,
            get_entity=AsyncMock(return_value=_channel()),
            get_messages=AsyncMock(return_value=_video_message()),
            download_media=AsyncMock(return_value=str(self.root / "downloads" / "clip.mp4")),
        )
        bot = BotCommandHandler(self.settings, self.database, user_client)
        bot._bot_client = SimpleNamespace(
            get_messages=AsyncMock(return_value=None),
            edit_message=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(id=1, chat_id=999)),
        )
        with patch("backend.app.bot_handler.generate_cover_cache", AsyncMock(return_value=None)):
            await bot.restore_queued_download(self.database.get_download(download_id))

        self.assertEqual(self.database.get_download(download_id)["status"], "completed")
        user_client.download_media.assert_awaited_once()
        # 进度消息由 Bot 账号编辑，用户账号不碰。
        bot._bot_client.edit_message.assert_awaited()

    async def test_text_link_message_is_routed_to_link_download(self):
        bot = self._handler()
        bot._handle_link_message = AsyncMock()
        await bot._handle_bot_message(self._event())
        bot._handle_link_message.assert_awaited_once()
        self.assertEqual(bot._handle_link_message.await_args.args[1], TelegramLink(chat_ref=CHANNEL_ID, message_id=17748))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
