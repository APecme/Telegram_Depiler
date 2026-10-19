from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend.app.config import Settings
from backend.app.telegram_worker import TelegramWorker


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = TelegramWorker(SimpleNamespace(api_id=123, api_hash="test"), Mock())
        self.client = SimpleNamespace(get_me=AsyncMock(return_value=None))
        self.worker._get_client = AsyncMock(return_value=self.client)

    async def test_revoked_session_is_not_authorized_by_saved_login(self):
        self.worker.database.get_login_state.return_value = {"is_authorized": True}
        self.assertIsNone(await self.worker.get_authorized_user())
        with self.assertRaisesRegex(PermissionError, "重新登录"):
            await self.worker.list_dialogs()

    async def test_authorization_is_checked_again_after_session_revocation(self):
        user = SimpleNamespace(id=42)
        self.client.get_me.side_effect = [user, None]
        self.assertIs(await self.worker.get_authorized_user(), user)
        self.assertIsNone(await self.worker.get_authorized_user())

    async def test_network_error_is_not_reported_as_logged_out(self):
        self.client.get_me.side_effect = ConnectionError("unreachable")
        with self.assertRaises(ConnectionError):
            await self.worker.get_authorized_user()

    async def test_missing_api_configuration_does_not_connect(self):
        self.worker.settings.api_id = None
        self.assertIsNone(await self.worker.get_authorized_user())
        self.worker._get_client.assert_not_awaited()


class AuthorizationEndpointTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        settings = Settings(data_dir=root / "data", download_dir=root / "downloads", static_dir=root / "static")
        settings.ensure_directories()
        with patch("backend.app.config.get_settings", return_value=settings):
            cls.main = importlib.import_module("backend.app.main")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def test_stale_database_login_does_not_override_revocation(self):
        with patch.object(self.main.worker, "get_authorized_user", AsyncMock(return_value=None)), patch.object(
            self.main.database, "get_login_state", return_value={"is_authorized": True}
        ):
            result = await self.main.auth_status()
        self.assertFalse(result["is_authorized"])

    async def test_live_account_identity_takes_precedence_over_saved_account(self):
        user = SimpleNamespace(id=42, bot=False, username="current", first_name="Current", last_name=None, phone=None)
        with patch.object(self.main.worker, "get_authorized_user", AsyncMock(return_value=user)), patch.object(
            self.main.database, "get_login_state", return_value={"user_id": 1, "username": "old", "last_login": "old date"}
        ):
            result = await self.main.auth_status()
        self.assertTrue(result["is_authorized"])
        self.assertEqual(result["user_id"], 42)
        self.assertEqual(result["username"], "current")
        self.assertIsNone(result["last_login"])

    async def test_connection_timeout_returns_service_error(self):
        with patch.object(self.main.worker, "get_authorized_user", AsyncMock(side_effect=asyncio.TimeoutError)):
            with self.assertRaises(self.main.HTTPException) as error:
                await self.main.auth_status()
        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
