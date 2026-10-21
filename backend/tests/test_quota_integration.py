from __future__ import annotations

import asyncio
import gc
import importlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.download_quota import QuotaScheduler, QuotaService
from backend.app.telegram_worker import TelegramWorker

MB = 1024 ** 2


class QuotaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        self.settings = Settings(data_dir=root / 'data', download_dir=root / 'downloads', static_dir=root / 'static')
        self.settings.ensure_directories()
        self.db = Database(self.settings.data_dir / 'state.db')
        self.quota = QuotaService(self.db)
        with patch('backend.app.config.get_settings', return_value=self.settings):
            self.main = importlib.import_module('backend.app.main')
        for name, value in {'database': self.db, 'settings': self.settings, 'quota_service': self.quota,
                            'ADMIN_TOKENS': {'test-token'}, 'bot_handler': None}.items():
            patcher = patch.object(self.main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = self.main.DownloadQueueManager(self.db)
        self.fake_worker = SimpleNamespace(restore_queued_download=AsyncMock(), resolve_download_size=AsyncMock(return_value=0))
        for name, value in {'download_queue_manager': self.manager, 'worker': self.fake_worker}.items():
            patcher = patch.object(self.main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.rule = self.db.add_group_rule(chat_id=100, quota_enabled=True)
        self.quota.replace_limits(self.rule, [{'period': 'day', 'limit_bytes': 100 * MB}])

    def download(self, size, **kwargs):
        return self.db.add_download(message_id=1, chat_id=100, bot_username='bot', file_name='f.mp4',
                                    source='rule', rule_id=self.rule, file_size=size, **kwargs)

    async def test_every_queue_entry_checks_quota_and_duplicate_start_is_rejected(self):
        first = self.download(90 * MB)
        self.assertTrue(await self.manager.try_start_download(first))
        self.assertFalse(await self.manager.try_start_download(first))
        large = self.download(20 * MB)
        small = self.download(10 * MB)
        self.assertFalse(await self.manager.try_start_download(large))
        self.assertEqual(self.db.get_download(large)['queue_reason'], 'quota')
        self.assertTrue(await self.manager.try_start_download(small))
        self.assertEqual(self.quota.usage(self.rule)[0]['reserved_bytes'], 100 * MB)

    async def test_completion_and_failure_settle_in_same_database_update(self):
        first = self.download(90 * MB)
        await self.manager.try_start_download(first)
        self.db.update_download(first, status='completed')
        self.assertEqual(self.quota.usage(self.rule)[0]['used_bytes'], 90 * MB)
        self.db.delete_download(first)
        self.assertEqual(self.quota.usage(self.rule)[0]['used_bytes'], 90 * MB)
        last = self.download(10 * MB)
        self.assertTrue(await self.manager.try_start_download(last))
        self.db.update_download(last, status='failed')
        self.db.update_download(last, status='failed')
        self.assertEqual(self.quota.usage(self.rule)[0]['reserved_bytes'], 0)

    async def test_release_wakes_waiter_before_scheduled_reset(self):
        active = self.download(90 * MB)
        waiting = self.download(20 * MB)
        await self.manager.try_start_download(active)
        await self.manager.try_start_download(waiting)
        self.db.update_download(active, status='failed')
        await self.manager.on_download_finished(active)
        await asyncio.sleep(0)
        self.assertEqual(self.db.get_download(waiting)['status'], 'downloading')
        self.fake_worker.restore_queued_download.assert_awaited_once_with(waiting, 1, 100)

    async def test_queue_drain_skips_blocked_rule_and_starts_small_file(self):
        active = self.download(90 * MB)
        await self.manager.try_start_download(active)
        self.db.update_download(active, status='completed')
        large = self.download(20 * MB, status='queued')
        small = self.download(10 * MB, status='queued')
        await self.manager.on_download_finished(0)
        await asyncio.sleep(0)
        self.assertEqual(self.db.get_download(large)['status'], 'queued')
        self.assertEqual(self.db.get_download(small)['status'], 'downloading')

    async def test_unknown_size_is_resolved_before_admission(self):
        download_id = self.download(0)
        self.fake_worker.resolve_download_size.return_value = 20 * MB
        self.assertTrue(await self.manager.try_start_download(download_id))
        self.assertEqual(self.db.get_download(download_id)['file_size'], 20 * MB)
        self.assertEqual(self.quota.usage(self.rule)[0]['reserved_bytes'], 20 * MB)

    async def test_concurrency_wait_does_not_reserve_quota(self):
        self.settings.download_options.concurrent_downloads = 1
        first, second = self.download(10 * MB), self.download(20 * MB)
        await self.manager.try_start_download(first)
        self.assertFalse(await self.manager.try_start_download(second))
        self.assertEqual(self.quota.usage(self.rule)[0]['reserved_bytes'], 10 * MB)

    async def test_retry_cannot_resurrect_completed_download_or_erase_ledger(self):
        download_id = self.download(20 * MB)
        await self.manager.try_start_download(download_id)
        self.db.update_download(download_id, status='completed')
        self.assertFalse(await self.manager.try_start_download(download_id))
        self.assertEqual(self.quota.usage(self.rule)[0]['used_bytes'], 20 * MB)

    def test_cross_process_claim_has_only_one_winner_for_same_download(self):
        download_id = self.download(20 * MB)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: QuotaService(self.db).claim(download_id, 5).admitted, range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.quota.usage(self.rule)[0]['reserved_bytes'], 20 * MB)

    async def test_new_constraint_counts_files_already_downloading(self):
        active = self.download(80 * MB)
        await self.manager.try_start_download(active)
        old = self.quota.list_limits(self.rule)[0]
        self.quota.replace_limits(self.rule, [old, {'period': 'hour', 'limit_bytes': 90 * MB}])
        hour = next(item for item in self.quota.usage(self.rule) if item['period'] == 'hour')
        self.assertEqual(hour['reserved_bytes'], 80 * MB)
        other = self.download(20 * MB)
        self.assertFalse(await self.manager.try_start_download(other))
        self.assertEqual(self.db.get_download(active)['status'], 'downloading')

    async def test_completed_ledger_cannot_be_reserved_again(self):
        download_id = self.download(20 * MB)
        await self.manager.try_start_download(download_id)
        self.db.update_download(download_id, status='completed')
        self.assertFalse(self.quota.reserve(download_id=download_id, rule_id=self.rule, file_size=20 * MB).admitted)
        self.assertEqual(self.quota.usage(self.rule)[0]['used_bytes'], 20 * MB)

    async def test_waiting_task_can_be_paused_without_auto_restarting(self):
        active, waiting = self.download(90 * MB), self.download(20 * MB)
        await self.manager.try_start_download(active)
        await self.manager.try_start_download(waiting)
        result = await self.main.pause_download(waiting)
        self.assertTrue(result['success'])
        self.db.update_download(active, status='failed')
        await self.manager.on_download_finished(active)
        self.assertEqual(self.db.get_download(waiting)['status'], 'paused')

    async def test_recovery_never_requeues_a_new_live_transfer(self):
        active = self.download(20 * MB)
        await self.manager.try_start_download(active)
        await self.main._resume_incomplete_downloads()
        await asyncio.sleep(0)
        self.assertEqual(self.db.get_download(active)['status'], 'downloading')
        self.fake_worker.restore_queued_download.assert_not_awaited()

    async def test_scheduler_restarts_waiter_in_new_period_without_other_activity(self):
        current = [datetime(2026, 10, 22, 2)]
        self.manager.quota._clock = lambda: current[0]
        active, waiting = self.download(90 * MB), self.download(20 * MB)
        await self.manager.try_start_download(active)
        self.db.update_download(active, status='completed')
        self.assertFalse(await self.manager.try_start_download(waiting))
        ready = asyncio.Event()
        self.fake_worker.restore_queued_download.side_effect = lambda *args: ready.set()
        scheduler = QuotaScheduler(self.manager.quota, lambda: self.manager.on_download_finished(0), tick_seconds=0.01)
        current[0] = datetime(2026, 10, 23, 2)
        scheduler.start()
        try:
            await asyncio.wait_for(ready.wait(), 0.5)
        finally:
            await scheduler.stop()
        self.assertEqual(self.db.get_download(waiting)['status'], 'downloading')

    def test_rule_api_roundtrip_and_invalid_update_is_atomic(self):
        client = TestClient(self.main.app, raise_server_exceptions=False)
        headers = {'X-Admin-Token': 'test-token'}
        invalid = client.post('/api/group-rules', headers=headers, json={'chat_id': 101, 'quota_enabled': True})
        self.assertEqual(invalid.status_code, 422, invalid.text)
        response = client.post('/api/group-rules', headers=headers, json={
            'chat_id': 101, 'quota_enabled': True,
            'quota_limits': [{'period': 'custom', 'custom_value': 6, 'custom_unit': 'hours', 'limit_value': 2, 'limit_unit': 'GB'}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        rule_id = response.json()['id']
        rule = response.json()['rule']
        self.assertTrue(rule['quota_enabled'])
        limit = rule['quota_limits'][0]
        self.assertEqual(limit['limit_bytes'], 2 * 1024 ** 3)
        response = client.put(f'/api/group-rules/{rule_id}', headers=headers, json={'quota_enabled': True, 'quota_limits': []})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.quota.list_limits(rule_id)[0]['id'], limit['id'])
        response = client.put(f'/api/group-rules/{rule_id}', headers=headers, json={'rule_name': 'renamed'})
        self.assertEqual(response.json()['rule']['quota_limits'][0]['anchor_at'], limit['anchor_at'])
        response = client.put(f'/api/group-rules/{rule_id}', headers=headers, json={'quota_limits': []})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['rule']['quota_enabled'])

    async def test_direct_worker_cannot_transfer_file_that_exceeds_remaining(self):
        worker = TelegramWorker(self.settings, self.db)
        message = SimpleNamespace(id=9, file=SimpleNamespace(size=101 * MB, name='video.mp4'),
                                  video=None, document=SimpleNamespace(id=9, access_hash=2), audio=None, photo=None)
        worker._download_media_with_retry = AsyncMock()
        await worker._download_file_by_rule(message, self.db.get_group_rule(self.rule), SimpleNamespace(id=100, title='group'), None)
        worker._download_media_with_retry.assert_not_awaited()
        self.assertEqual(self.db.list_downloads()[0]['status'], 'failed')
