from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.app.telegram_worker import TelegramWorker


class FakeDatabase:
    def __init__(self, rules: list[dict]):
        self.rules = rules

    def get_group_rules_for_chat(
        self,
        chat_id: int,
        *,
        mode: str | None = None,
        only_enabled: bool = True,
    ) -> list[dict]:
        return [
            rule
            for rule in self.rules
            if int(rule["chat_id"]) == chat_id
            and (mode is None or rule["mode"] == mode)
            and (not only_enabled or rule["enabled"])
        ]

    def list_group_rules(self, *, chat_id=None, mode=None) -> list[dict]:
        return [
            rule
            for rule in self.rules
            if (chat_id is None or int(rule["chat_id"]) == chat_id)
            and (mode is None or rule["mode"] == mode)
        ]


class CommentRuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_linked_channel_rule_is_applied_to_discussion_group(self) -> None:
        channel_rule = {
            "id": 1,
            "chat_id": 100,
            "mode": "monitor",
            "enabled": 1,
            "include_comments": 1,
        }
        direct_rule = {
            "id": 2,
            "chat_id": 200,
            "mode": "monitor",
            "enabled": 1,
            "include_comments": 0,
        }
        worker = TelegramWorker(SimpleNamespace(), FakeDatabase([channel_rule, direct_rule]))
        worker._client = AsyncMock(
            return_value=SimpleNamespace(
                full_chat=SimpleNamespace(linked_chat_id=100),
            )
        )
        discussion = SimpleNamespace(id=200, megagroup=True)
        comment = SimpleNamespace(reply_to=object())

        rules = await worker._get_monitor_rules_for_chat(discussion, comment)

        self.assertEqual([rule["id"] for rule in rules], [2, 1])
        self.assertEqual(worker._client.await_count, 1)

        await worker._get_monitor_rules_for_chat(discussion, comment)
        self.assertEqual(worker._client.await_count, 1)

    async def test_linked_channel_rule_ignores_discussion_root_message(self) -> None:
        channel_rule = {
            "id": 1,
            "chat_id": 100,
            "mode": "monitor",
            "enabled": 1,
            "include_comments": 1,
        }
        worker = TelegramWorker(SimpleNamespace(), FakeDatabase([channel_rule]))
        worker._client = AsyncMock()
        discussion = SimpleNamespace(id=200, megagroup=True)
        root_message = SimpleNamespace(reply_to=None)

        resolved = await worker._get_monitor_rules_for_chat(discussion, root_message)

        self.assertEqual(resolved, [])
        worker._client.assert_not_awaited()

    async def test_disabled_or_non_comment_rules_are_not_inherited(self) -> None:
        rules = [
            {
                "id": 1,
                "chat_id": 100,
                "mode": "monitor",
                "enabled": 1,
                "include_comments": 0,
            },
            {
                "id": 2,
                "chat_id": 100,
                "mode": "monitor",
                "enabled": 0,
                "include_comments": 1,
            },
        ]
        worker = TelegramWorker(SimpleNamespace(), FakeDatabase(rules))
        worker._client = AsyncMock()
        discussion = SimpleNamespace(id=200, megagroup=True)

        resolved = await worker._get_monitor_rules_for_chat(
            discussion,
            SimpleNamespace(reply_to=object()),
        )

        self.assertEqual(resolved, [])
        worker._client.assert_not_awaited()

    async def test_regular_group_does_not_query_linked_channel(self) -> None:
        channel_rule = {
            "id": 1,
            "chat_id": 100,
            "mode": "monitor",
            "enabled": 1,
            "include_comments": 1,
        }
        worker = TelegramWorker(SimpleNamespace(), FakeDatabase([channel_rule]))
        worker._client = AsyncMock()
        regular_group = SimpleNamespace(id=200, megagroup=False)

        resolved = await worker._get_monitor_rules_for_chat(
            regular_group,
            SimpleNamespace(reply_to=object()),
        )

        self.assertEqual(resolved, [])
        worker._client.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
