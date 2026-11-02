import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from backend.app.telegram_worker import TelegramWorker


class CatchUpWarningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rule = {"id": 7, "chat_id": 100, "last_seen_message_id": 0}
        self.chat = SimpleNamespace(id=100, title="channel", megagroup=False)
        self.client = Mock(
            is_user_authorized=AsyncMock(return_value=True),
            get_entity=AsyncMock(return_value=self.chat),
            get_messages=AsyncMock(return_value=[SimpleNamespace(id=1200)]),
        )
        self.worker = object.__new__(TelegramWorker)
        self.worker.database = Mock(list_auto_catch_up_rules=Mock(return_value=[self.rule]))
        self.worker._get_client = AsyncMock(return_value=self.client)

    async def test_first_cursor_warns_but_still_skips_backlog(self):
        with self.assertLogs("backend.app.telegram_worker", level="WARNING") as logs:
            await self.worker.catch_up_missed_group_messages()
        self.worker.database.update_group_rules_last_seen_message_id.assert_called_once_with([7], 1200)
        self.client.get_messages.assert_awaited_once_with(self.chat, limit=1)
        self.client.iter_messages.assert_not_called()
        self.assertEqual(len(logs.output), 1)
        for value in ("rule_ids=[7]", "channel=channel", "chat_id=100", "cursor=message", "skipped_message_id_range=1..1200"):
            self.assertIn(value, logs.output[0])

    async def test_comment_cursor_also_warns_without_changing_backfill(self):
        self.rule.update(include_comments=1, last_seen_comment_message_id=0)
        discussion = SimpleNamespace(id=200, title="discussion")
        self.client.get_entity.side_effect = [self.chat, discussion]
        self.client.get_messages.side_effect = [[SimpleNamespace(id=1200)], [SimpleNamespace(id=800)]]
        self.worker._get_linked_discussion_id_for_channel = AsyncMock(return_value=200)
        with self.assertLogs("backend.app.telegram_worker", level="WARNING") as logs:
            await self.worker.catch_up_missed_group_messages()
        self.worker.database.update_group_rules_last_seen_comment_message_id.assert_called_once_with([7], 800)
        self.client.iter_messages.assert_not_called()
        self.assertEqual(len(logs.output), 2)
        self.assertIn("cursor=comment", logs.output[1])
        self.assertIn("skipped_message_id_range=1..800", logs.output[1])

    async def test_empty_channel_does_not_advance_cursor_or_warn(self):
        self.client.get_messages.return_value = []
        with self.assertNoLogs("backend.app.telegram_worker", level="WARNING"):
            await self.worker.catch_up_missed_group_messages()
        self.worker.database.update_group_rules_last_seen_message_id.assert_not_called()
        self.client.iter_messages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
