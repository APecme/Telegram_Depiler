from __future__ import annotations

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


class FakeClient:
    def __init__(self, messages):
        self._messages = messages

    async def is_user_authorized(self):
        return True

    async def get_entity(self, chat_id):
        return SimpleNamespace(id=chat_id, title="新厕VIP3.3")

    def iter_messages(self, chat, limit=None):
        async def generate():
            for message in self._messages[:limit]:
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

    def _client(self, messages):
        client = FakeClient(messages)
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

    def test_diagnosis_uses_the_same_gate_as_the_download_path(self):
        rule = {"id": 9, "include_extensions": "mp4,mkv"}
        message = video_message(1)
        self.assertIsNone(self.worker._rule_rejection_reason(message, rule))
        self.assertTrue(self.worker._should_download_by_rule(message, rule))
        self.assertIsNotNone(self.worker._rule_rejection_reason(message, {"id": 9, "include_extensions": "jpg"}))
        self.assertFalse(self.worker._should_download_by_rule(message, {"id": 9, "include_extensions": "jpg"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
