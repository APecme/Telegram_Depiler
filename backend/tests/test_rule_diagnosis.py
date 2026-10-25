from __future__ import annotations

import asyncio
import gc
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.telegram_worker import TelegramWorker


def photo_message(message_id: int, *, photo_id: int | None = None) -> SimpleNamespace:
    photo = SimpleNamespace(id=photo_id, access_hash=7) if photo_id is not None else SimpleNamespace()
    return SimpleNamespace(
        id=message_id,
        date=None,
        message="",
        video=None,
        document=None,
        audio=None,
        photo=photo,
        file=SimpleNamespace(ext=".jpg", name=None, size=2048),
    )


def video_message(message_id: int, *, document_id: int | None = None) -> SimpleNamespace:
    document = SimpleNamespace(id=document_id, access_hash=9) if document_id is not None else SimpleNamespace()
    return SimpleNamespace(
        id=message_id,
        date=None,
        message="",
        video=SimpleNamespace(file_name=None),
        document=document,
        audio=None,
        photo=None,
        file=SimpleNamespace(ext=".mp4", name=None, size=4096),
    )


CHANNEL_ID = 3997085467
DISCUSSION_ID = 3650058618


def channel(megagroup: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=CHANNEL_ID, title="新厕VIP3.3", megagroup=megagroup)


def discussion() -> SimpleNamespace:
    return SimpleNamespace(id=DISCUSSION_ID, title="新厕VIP3.3 讨论组", megagroup=True)


class FakeClient:
    """Minimal Telethon stand-in: entities, message iteration and GetFullChannel."""

    def __init__(self, messages, *, comment_messages=None, entities=None, linked=True,
                 reverse_channel_id=None):
        self._messages = messages
        self._comment_messages = comment_messages or []
        self._entities = entities or {}
        self._linked = linked
        # 讨论组反查频道时返回的 ID，用来模拟"实时评论不会挂到这条规则上"。
        self._reverse_channel_id = reverse_channel_id

    async def is_user_authorized(self):
        return True

    async def get_entity(self, chat_id):
        if chat_id in self._entities:
            return self._entities[chat_id]
        return SimpleNamespace(id=chat_id, title="新厕VIP3.3")

    async def __call__(self, request):
        target = getattr(request, "channel", None)
        target_id = int(getattr(target, "id", 0) or 0)
        if not self._linked:
            return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=None))
        if target_id == CHANNEL_ID:
            return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=DISCUSSION_ID))
        linked_id = self._reverse_channel_id or CHANNEL_ID
        return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=linked_id))

    def iter_messages(self, chat, limit=None, min_id=None, max_id=None, reverse=False):
        source = self._comment_messages if int(getattr(chat, "id", 0) or 0) == DISCUSSION_ID else self._messages

        async def generate():
            for message in source[:limit] if limit else source:
                if min_id is not None and int(message.id) <= min_id:
                    continue
                if max_id is not None and int(message.id) >= max_id:
                    continue
                yield message

        return generate()


class RuleDiagnosisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(self.directory.name)
        self.settings = Settings(data_dir=root / "data", download_dir=root / "downloads")
        self.settings.ensure_directories()
        self.database = Database(self.settings.data_dir / "state.db")
        self.worker = TelegramWorker(self.settings, self.database)

    def _rule(self, **overrides):
        values = {
            "chat_id": 3997085467,
            "chat_title": "新厕VIP3.3",
            "mode": "monitor",
            "enabled": 1,
            "content_type": "media",
            "include_extensions": "mp4,mkv",
            "include_comments": 1,
        }
        values.update(overrides)
        return self.database.add_group_rule(**values)

    def _client(self, messages, **kwargs):
        client = FakeClient(messages, **kwargs)
        self.worker._client = client
        self.worker._get_client = AsyncMock(return_value=client)
        return client

    async def test_reports_which_gate_rejects_each_message(self):
        rule_id = self._rule()
        self._client([photo_message(12778), video_message(12777)])

        result = await self.worker.diagnose_rule(rule_id, limit=10)

        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["would_download"], 1)
        self.assertEqual(result["reasons"], {"extension": 1})
        photo, video = result["items"]
        self.assertEqual(photo["verdict"], "skip")
        self.assertIn("jpg", photo["reason"])
        self.assertEqual(video["verdict"], "download")
        self.assertEqual(video["extension"], "mp4")

    async def test_monitor_rule_explains_that_older_messages_are_not_backfilled(self):
        rule_id = self._rule(last_seen_message_id=12778)
        self._client([video_message(12778), video_message(100)])

        result = await self.worker.diagnose_rule(rule_id)

        self.assertTrue(result["items"][0]["below_last_seen"])
        self.assertTrue(result["items"][1]["below_last_seen"])
        self.assertTrue(any("monitor 规则只处理新消息" in note for note in result["notes"]))

    async def test_detects_files_that_global_dedupe_would_skip(self):
        rule_id = self._rule()
        self.database.add_download(
            message_id=1, chat_id=-1003650058618, bot_username="bot", file_name="clip.mp4",
            status="completed", source="bot", tg_file_id=555, tg_access_hash=9,
        )
        self._client([video_message(12778, document_id=555)])

        result = await self.worker.diagnose_rule(rule_id)

        self.assertEqual(result["would_download"], 0)
        self.assertEqual(result["reasons"], {"duplicate": 1})
        self.assertIn("去重", result["items"][0]["reason"])

    async def test_text_rule_reports_missing_keyword(self):
        rule_id = self._rule(content_type="message_text", include_extensions="", match_mode="include",
                             include_keywords="关键字")
        message = SimpleNamespace(id=1, date=None, message="毫无关系的内容", video=None, document=None,
                                  audio=None, photo=None, file=None)
        self._client([message])

        result = await self.worker.diagnose_rule(rule_id)

        self.assertEqual(result["reasons"], {"keyword": 1})
        self.assertIn("必需关键词", result["items"][0]["reason"])

    async def test_missing_rule_is_reported(self):
        with self.assertRaises(ValueError):
            await self.worker.diagnose_rule(9999)

    async def test_comment_scan_reads_the_linked_discussion(self):
        rule_id = self._rule()
        self._client(
            [photo_message(12778)],
            comment_messages=[video_message(47003), photo_message(47004)],
            entities={CHANNEL_ID: channel(), DISCUSSION_ID: discussion()},
        )

        result = await self.worker.diagnose_rule(rule_id)

        comments = result["comments"]
        self.assertTrue(comments["enabled"])
        self.assertEqual(comments["linked_discussion_id"], DISCUSSION_ID)
        self.assertTrue(comments["live_rule_match"])
        self.assertEqual(comments["scanned"], 2)
        self.assertEqual(comments["would_download"], 1)
        self.assertEqual(comments["reasons"], {"extension": 1})
        self.assertEqual(comments["items"][0]["message_id"], 47003)
        self.assertEqual(comments["items"][0]["verdict"], "download")

    async def test_comment_scan_explains_a_missing_discussion(self):
        rule_id = self._rule()
        self._client([photo_message(1)], entities={CHANNEL_ID: channel()}, linked=False)

        result = await self.worker.diagnose_rule(rule_id)

        self.assertEqual(result["comments"]["linked_discussion_id"], None)
        self.assertIn("未找到关联讨论组", result["comments"]["note"])

    async def test_comment_scan_flags_rules_that_live_matching_would_miss(self):
        rule_id = self._rule()
        # 讨论组反查到的频道与规则 chat_id 不一致（例如规则指向了别的群）。
        self._client(
            [photo_message(1)],
            comment_messages=[video_message(47003)],
            entities={CHANNEL_ID: channel(), DISCUSSION_ID: discussion()},
            reverse_channel_id=1548946818,
        )

        result = await self.worker.diagnose_rule(rule_id)

        self.assertFalse(result["comments"]["live_rule_match"])
        self.assertIn("不会被这条规则接管", result["comments"]["note"])

    def test_diagnosis_uses_the_same_gate_as_the_download_path(self):
        rule = {"id": 9, "include_extensions": "mp4,mkv"}
        message = video_message(1)
        self.assertIsNone(self.worker._rule_rejection_reason(message, rule))
        self.assertTrue(self.worker._should_download_by_rule(message, rule))
        self.assertIsNotNone(self.worker._rule_rejection_reason(message, {"id": 9, "include_extensions": "jpg"}))
        self.assertFalse(self.worker._should_download_by_rule(message, {"id": 9, "include_extensions": "jpg"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class HistoryBackfillTests(unittest.IsolatedAsyncioTestCase):
    """history 规则在 include_comments 时要把关联讨论组一起扫。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(self.directory.name)
        self.settings = Settings(data_dir=root / "data", download_dir=root / "downloads")
        self.settings.ensure_directories()
        self.database = Database(self.settings.data_dir / "state.db")
        self.worker = TelegramWorker(self.settings, self.database)
        self.worker._download_file_by_rule = AsyncMock()
        self.worker._get_client = AsyncMock()

    def _client(self, **kwargs):
        client = FakeClient([], **kwargs)
        self.worker._client = client
        self.worker._get_client = AsyncMock(return_value=client)
        return client

    def _history_rule(self, **overrides):
        values = {
            "chat_id": CHANNEL_ID,
            "chat_title": "新厕VIP3.3",
            "mode": "history",
            "enabled": 1,
            "content_type": "media",
            "include_extensions": "mp4,mkv",
            "include_comments": 1,
            "min_message_id": 1,
            "max_message_id": 47005,
        }
        values.update(overrides)
        return self.database.add_group_rule(**values)

    async def test_comment_history_scans_the_linked_discussion(self):
        rule_id = self._history_rule()
        self._client(
            comment_messages=[video_message(47003)],
            entities={CHANNEL_ID: channel(), DISCUSSION_ID: discussion()},
        )

        result = await self.worker.download_history_for_rule(rule_id)
        await asyncio.sleep(0)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["scopes"], {"chat": {"scanned": 0}, "comments": {"scanned": 1}})
        self.worker._download_file_by_rule.assert_awaited_once()
        scanned_chat = self.worker._download_file_by_rule.await_args.args[2]
        self.assertEqual(int(scanned_chat.id), DISCUSSION_ID)
        self.assertEqual(result["added_tasks"], 1)

    async def test_rules_without_comments_only_scan_their_own_chat(self):
        rule_id = self._history_rule(include_comments=0)
        self._client(
            comment_messages=[video_message(47003)],
            entities={CHANNEL_ID: channel(), DISCUSSION_ID: discussion()},
        )

        result = await self.worker.download_history_for_rule(rule_id)

        self.assertEqual(result["scopes"], {"chat": {"scanned": 0}})
        self.worker._download_file_by_rule.assert_not_awaited()

    async def test_missing_linked_discussion_still_scans_the_channel(self):
        rule_id = self._history_rule()
        self._client(
            entities={CHANNEL_ID: channel(), DISCUSSION_ID: discussion()},
            linked=False,
        )

        result = await self.worker.download_history_for_rule(rule_id)

        self.assertEqual(result["scopes"], {"chat": {"scanned": 0}})
