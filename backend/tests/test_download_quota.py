from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.database import Database
from backend.app.download_quota import (
    QuotaConfigError,
    QuotaScheduler,
    QuotaService,
    format_bytes,
    parse_capacity,
    period_window,
    resolve_timezone,
)

MB = 1024 ** 2
GB = 1024 ** 3


class _FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value


class QuotaTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database(self.root / "state.db")
        self.clock = _FixedClock(datetime(2026, 10, 22, 2, 0, 0))  # UTC -> 10:00 Asia/Shanghai
        self.quota = QuotaService(self.database, clock=self.clock)

    # -- helpers -------------------------------------------------------
    def make_rule(self, *, quota_enabled: bool = True, content_type: str = "media") -> int:
        return self.database.add_group_rule(
            chat_id=100,
            chat_title="测试群",
            rule_name="测试规则",
            content_type=content_type,
            quota_enabled=quota_enabled,
        )

    def set_limits(self, rule_id: int, limits: list[dict]) -> list[dict]:
        return self.quota.replace_limits(rule_id, limits)

    def make_download(self, rule_id: int, size: int, *, status: str = "pending") -> int:
        return self.database.add_download(
            message_id=1,
            chat_id=100,
            bot_username="bot",
            file_name="a.bin",
            status=status,
            source="rule",
            file_size=size,
            rule_id=rule_id,
        )

    def usage_of(self, rule_id: int, limit_id: int | None = None) -> dict:
        limits = self.quota.usage(rule_id)
        if limit_id is None:
            self.assertEqual(len(limits), 1)
            return limits[0]
        return next(item for item in limits if item["id"] == limit_id)

    def used_bytes(self, limit_id: int, *, state: str = "committed") -> int:
        conn = sqlite3.connect(str(self.database.db_path))
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(bytes), 0) FROM group_rule_quota_usage WHERE limit_id = ? AND state = ?",
                (limit_id, state),
            ).fetchone()
            return int(row[0] or 0)
        finally:
            conn.close()

    def usage_rows(self, download_id: int) -> list[tuple]:
        conn = sqlite3.connect(str(self.database.db_path))
        try:
            return conn.execute(
                "SELECT limit_id, bytes, state FROM group_rule_quota_usage WHERE download_id = ? ORDER BY limit_id",
                (download_id,),
            ).fetchall()
        finally:
            conn.close()


class CapacityParsingTests(QuotaTestBase):
    def test_capacity_uses_1024_units_and_integer_bytes(self):
        self.assertEqual(parse_capacity(1, "MB"), MB)
        self.assertEqual(parse_capacity(1, "GB"), GB)
        self.assertEqual(parse_capacity(1, "TB"), 1024 ** 4)
        self.assertEqual(parse_capacity(0.5, "GB"), GB // 2)
        self.assertIsInstance(parse_capacity(1.5, "GB"), int)

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(QuotaConfigError):
            parse_capacity(0, "GB")
        with self.assertRaises(QuotaConfigError):
            parse_capacity(-5, "GB")
        with self.assertRaises(QuotaConfigError):
            parse_capacity(1, "PB")

    def test_format_bytes_readable(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1 KB")
        self.assertEqual(format_bytes(GB), "1 GB")


class PeriodWindowTests(QuotaTestBase):
    def test_hour_window_aligns_to_whole_hour_in_shanghai(self):
        start, end = period_window({"period": "hour"}, datetime(2026, 10, 22, 1, 37), resolve_timezone("Asia/Shanghai"))
        self.assertEqual(start, datetime(2026, 10, 22, 1, 0))
        self.assertEqual(end, datetime(2026, 10, 22, 2, 0))

    def test_day_window_aligns_to_local_midnight(self):
        # 2026-10-22 17:30 UTC == 2026-10-23 01:30 Asia/Shanghai -> 周期起点是 10-22 16:00 UTC
        start, end = period_window({"period": "day"}, datetime(2026, 10, 22, 17, 30), resolve_timezone("Asia/Shanghai"))
        self.assertEqual(start, datetime(2026, 10, 22, 16, 0))
        self.assertEqual(end, datetime(2026, 10, 23, 16, 0))

    def test_week_starts_monday_local_midnight(self):
        # 2026-10-22 是周四
        start, end = period_window({"period": "week"}, datetime(2026, 10, 22, 3, 0), resolve_timezone("Asia/Shanghai"))
        self.assertEqual(start, datetime(2026, 10, 18, 16, 0))  # 2026-10-19 00:00 +08:00
        self.assertEqual(end, datetime(2026, 10, 25, 16, 0))

    def test_month_window_handles_leap_february(self):
        tz = resolve_timezone("Asia/Shanghai")
        start, end = period_window({"period": "month"}, datetime(2028, 2, 10, 3, 0), tz)
        self.assertEqual(start, datetime(2028, 1, 31, 16, 0))  # 2028-02-01 00:00 +08:00
        self.assertEqual(end, datetime(2028, 2, 29, 16, 0))  # 2028-03-01 00:00 +08:00（闰年）

        start, end = period_window({"period": "month"}, datetime(2027, 2, 10, 3, 0), tz)
        self.assertEqual(end, datetime(2027, 2, 28, 16, 0))  # 2027-03-01 00:00 +08:00（平年）

    def test_custom_window_rolls_forward_from_anchor(self):
        tz = resolve_timezone("Asia/Shanghai")
        anchor = datetime(2026, 10, 20, 4, 0)  # 12:00 Asia/Shanghai
        limit = {
            "period": "custom",
            "custom_value": 6,
            "custom_unit": "hours",
            "anchor_at": anchor.strftime("%Y-%m-%d %H:%M:%S"),
        }
        start, end = period_window(limit, datetime(2026, 10, 22, 5, 30), tz)
        self.assertEqual(start, datetime(2026, 10, 22, 4, 0))
        self.assertEqual(end, datetime(2026, 10, 22, 10, 0))


class AdmissionTests(QuotaTestBase):
    def test_disabled_quota_keeps_legacy_behaviour(self):
        rule_id = self.make_rule(quota_enabled=False)
        download_id = self.make_download(rule_id, 500 * GB)
        admission = self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=500 * GB)
        self.assertTrue(admission.admitted)
        self.assertEqual(self.usage_rows(download_id), [])

    def test_exact_boundary_is_allowed(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        first = self.make_download(rule_id, 90 * MB)
        self.assertTrue(self.quota.reserve(download_id=first, rule_id=rule_id, file_size=90 * MB).admitted)
        self.quota.commit(first)

        second = self.make_download(rule_id, 10 * MB)
        admission = self.quota.reserve(download_id=second, rule_id=rule_id, file_size=10 * MB)
        self.assertTrue(admission.admitted, "剩余恰好等于文件大小时必须允许")

    def test_insufficient_remaining_waits_for_whole_file(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        used = self.make_download(rule_id, 90 * MB)
        self.assertTrue(self.quota.reserve(download_id=used, rule_id=rule_id, file_size=90 * MB).admitted)
        self.quota.commit(used)

        big = self.make_download(rule_id, 20 * MB)
        admission = self.quota.reserve(download_id=big, rule_id=rule_id, file_size=20 * MB)
        self.assertFalse(admission.admitted)
        self.assertEqual(admission.blocking, "quota")
        self.assertIsNotNone(admission.next_retry_at)
        self.assertIn("等待下载额度", admission.reason)
        self.assertEqual(self.usage_rows(big), [], "未准入的任务不能留下预占")

    def test_file_larger_than_period_limit_is_rejected_with_hint(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "hour", "limit_bytes": 50 * MB}])
        download_id = self.make_download(rule_id, 200 * MB)
        admission = self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=200 * MB)
        self.assertFalse(admission.admitted)
        self.assertEqual(admission.blocking, "too_large")
        self.assertIsNone(admission.next_retry_at, "超过总上限不应安排无意义的重试")
        self.assertIn("请修改", admission.reason)

    def test_multiple_limits_must_all_pass_atomically(self):
        rule_id = self.make_rule()
        limits = self.set_limits(
            rule_id,
            [
                {"period": "hour", "limit_bytes": 100 * MB},
                {"period": "day", "limit_bytes": 50 * MB},
            ],
        )
        download_id = self.make_download(rule_id, 60 * MB)
        admission = self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=60 * MB)
        self.assertFalse(admission.admitted)
        self.assertEqual(self.usage_rows(download_id), [], "任一限额不满足时不能留下任何预占")
        for limit in limits:
            self.assertEqual(self.used_bytes(limit["id"], state="reserved"), 0)

    def test_unknown_size_cannot_bypass_limits(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 0)
        admission = self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=0)
        self.assertFalse(admission.admitted)
        self.assertEqual(admission.blocking, "unknown_size")
        self.assertIn("无法获取文件大小", admission.reason)

    def test_multiple_blocking_periods_show_when_all_can_recover(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{'period': 'hour', 'limit_bytes': 100 * MB},
                                  {'period': 'day', 'limit_bytes': 100 * MB}])
        first = self.make_download(rule_id, 90 * MB)
        self.quota.reserve(download_id=first, rule_id=rule_id, file_size=90 * MB)
        self.quota.commit(first)
        second = self.make_download(rule_id, 20 * MB)
        result = self.quota.reserve(download_id=second, rule_id=rule_id, file_size=20 * MB)
        self.assertEqual(result.next_retry_at, '2026-10-22 16:00:00')
        self.assertEqual(result.limit_label, '每日')

    def test_message_text_is_never_billed_or_blocked(self):
        rule_id = self.make_rule(content_type="message_text")
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 1 * MB}])
        download_id = self.make_download(rule_id, 0)
        admission = self.quota.reserve(
            download_id=download_id, rule_id=rule_id, file_size=0, content_type="message_text"
        )
        self.assertTrue(admission.admitted)
        self.assertEqual(self.usage_rows(download_id), [])
        self.assertEqual(self.used_bytes(self.quota.list_limits(rule_id)[0]["id"], state="reserved"), 0)

    def test_rule_disabled_prevents_reservation(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        self.database.update_group_rule(rule_id, enabled=False)
        download_id = self.make_download(rule_id, 10 * MB)
        admission = self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=10 * MB)
        self.assertFalse(admission.admitted)
        self.assertEqual(admission.blocking, "rule")


class ConcurrencyTests(QuotaTestBase):
    def test_two_competing_reservations_only_one_wins(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        first = self.make_download(rule_id, 60 * MB)
        second = self.make_download(rule_id, 60 * MB)

        def attempt(download_id: int) -> bool:
            service = QuotaService(self.database)  # 独立连接，模拟另一个进程/线程
            return service.reserve(download_id=download_id, rule_id=rule_id, file_size=60 * MB).admitted

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [first, second]))

        self.assertEqual(sorted(results), [False, True], "SQLite 事务下只允许一笔通过")
        reserved = self.used_bytes(self.quota.list_limits(rule_id)[0]["id"], state="reserved")
        self.assertEqual(reserved, 60 * MB, "预占总量不能超过一笔")

    def test_repeated_reserve_is_idempotent(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 60 * MB)
        self.assertTrue(self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=60 * MB).admitted)
        self.assertTrue(self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=60 * MB).admitted)
        self.assertEqual(self.usage_rows(download_id), [(self.quota.list_limits(rule_id)[0]["id"], 60 * MB, "reserved")])


class SettlementTests(QuotaTestBase):
    def test_release_is_idempotent_and_restores_capacity(self):
        rule_id = self.make_rule()
        limit = self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])[0]
        download_id = self.make_download(rule_id, 80 * MB)
        self.assertTrue(self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=80 * MB).admitted)
        self.assertEqual(self.usage_of(rule_id)["reserved_bytes"], 80 * MB)

        self.assertEqual(self.quota.release(download_id), 1)
        self.assertEqual(self.quota.release(download_id), 0)
        self.assertEqual(self.usage_of(rule_id)["reserved_bytes"], 0)
        self.assertEqual(self.used_bytes(limit["id"]), 0)

        other = self.make_download(rule_id, 80 * MB)
        self.assertTrue(self.quota.reserve(download_id=other, rule_id=rule_id, file_size=80 * MB).admitted)

    def test_commit_is_idempotent_and_charges_once(self):
        rule_id = self.make_rule()
        limit = self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])[0]
        download_id = self.make_download(rule_id, 70 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=70 * MB)
        self.assertEqual(self.quota.commit(download_id), 1)
        self.assertEqual(self.quota.commit(download_id), 0)
        self.assertEqual(self.used_bytes(limit["id"]), 70 * MB)

    def test_successful_usage_survives_download_record_deletion(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 70 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=70 * MB)
        self.quota.commit(download_id)

        self.database.delete_download(download_id)
        usage = self.usage_of(rule_id)
        self.assertEqual(usage["used_bytes"], 70 * MB, "删除下载记录不返还成功用量")
        self.assertEqual(usage["remaining_bytes"], 30 * MB)

    def test_release_after_commit_does_not_refund(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 70 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=70 * MB)
        self.quota.commit(download_id)
        self.assertEqual(self.quota.release(download_id), 0)
        self.assertEqual(self.usage_of(rule_id)["used_bytes"], 70 * MB)


class PeriodAccountingTests(QuotaTestBase):
    def test_reservation_started_in_previous_period_still_counts_today(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        # 2026-10-22 15:50 UTC == 23:50 Asia/Shanghai
        self.clock.set(datetime(2026, 10, 22, 15, 50))
        running = self.make_download(rule_id, 80 * MB, status="downloading")
        self.assertTrue(self.quota.reserve(download_id=running, rule_id=rule_id, file_size=80 * MB).admitted)

        # 跨过零点，仍在下载的预占继续占用当前可用额度
        self.clock.set(datetime(2026, 10, 22, 16, 10))  # 00:10 Asia/Shanghai（新的一天）
        blocked = self.make_download(rule_id, 30 * MB)
        admission = self.quota.reserve(download_id=blocked, rule_id=rule_id, file_size=30 * MB)
        self.assertFalse(admission.admitted)

        # 结束后，成功用量归属开始的周期，新的一天从 0 开始
        self.quota.commit(running)
        self.clock.set(datetime(2026, 10, 22, 16, 20))
        usage = self.usage_of(rule_id)
        self.assertEqual(usage["used_bytes"], 0, "成功用量归属开始的周期，不应计入新周期")
        self.assertEqual(usage["reserved_bytes"], 0)
        self.assertTrue(self.quota.reserve(download_id=blocked, rule_id=rule_id, file_size=30 * MB).admitted)

    def test_usage_returns_to_zero_when_period_rolls_over(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "hour", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 90 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=90 * MB)
        self.quota.commit(download_id)
        self.assertEqual(self.usage_of(rule_id)["remaining_bytes"], 10 * MB)

        self.clock.set(self.clock.value + timedelta(hours=1))
        self.assertEqual(self.usage_of(rule_id)["remaining_bytes"], 100 * MB)

    def test_custom_anchor_is_persisted_and_not_reset_by_plain_save(self):
        rule_id = self.make_rule()
        created = self.set_limits(
            rule_id, [{"period": "custom", "custom_value": 6, "custom_unit": "hours", "limit_bytes": 100 * MB}]
        )[0]
        anchor = created["anchor_at"]
        self.assertIsNotNone(anchor)

        download_id = self.make_download(rule_id, 90 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=90 * MB)
        self.quota.commit(download_id)

        # 相同配置普通保存：锚点与用量都必须保留
        self.clock.set(self.clock.value + timedelta(minutes=30))
        saved = self.set_limits(
            rule_id,
            [
                {
                    "id": created["id"],
                    "period": "custom",
                    "custom_value": 6,
                    "custom_unit": "hours",
                    "limit_bytes": 100 * MB,
                }
            ],
        )[0]
        self.assertEqual(saved["anchor_at"], anchor, "普通保存不能重置锚点")
        self.assertEqual(self.usage_of(rule_id)["used_bytes"], 90 * MB, "普通保存不能洗掉用量")

    def test_period_change_keeps_ledger_history(self):
        rule_id = self.make_rule()
        limit = self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])[0]
        download_id = self.make_download(rule_id, 40 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=40 * MB)
        self.quota.commit(download_id)

        self.set_limits(rule_id, [{"id": limit["id"], "period": "month", "limit_bytes": 100 * MB}])
        self.assertEqual(self.used_bytes(limit["id"]), 40 * MB, "改周期保留历史账本")
        self.assertEqual(self.usage_of(rule_id)["used_bytes"], 40 * MB)

    def test_limit_increase_applies_immediately(self):
        rule_id = self.make_rule()
        limit = self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])[0]
        used = self.make_download(rule_id, 95 * MB)
        self.quota.reserve(download_id=used, rule_id=rule_id, file_size=95 * MB)
        self.quota.commit(used)

        waiting = self.make_download(rule_id, 50 * MB)
        self.assertFalse(self.quota.reserve(download_id=waiting, rule_id=rule_id, file_size=50 * MB).admitted)

        self.set_limits(rule_id, [{"id": limit["id"], "period": "day", "limit_bytes": 200 * MB}])
        self.assertTrue(self.quota.reserve(download_id=waiting, rule_id=rule_id, file_size=50 * MB).admitted)

    def test_clearing_limits_removes_configuration_but_keeps_ledger(self):
        rule_id = self.make_rule()
        limit = self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])[0]
        download_id = self.make_download(rule_id, 40 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=40 * MB)
        self.quota.commit(download_id)

        self.set_limits(rule_id, [])
        self.assertEqual(self.quota.list_limits(rule_id), [])
        self.assertEqual(self.used_bytes(limit["id"]), 40 * MB)

        other = self.make_download(rule_id, 900 * MB)
        self.assertTrue(self.quota.reserve(download_id=other, rule_id=rule_id, file_size=900 * MB).admitted)


class RestartRecoveryTests(QuotaTestBase):
    def test_reconcile_releases_stale_reservations_only(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        active = self.make_download(rule_id, 30 * MB, status="downloading")
        dead = self.make_download(rule_id, 40 * MB, status="failed")
        for download_id, size in ((active, 30 * MB), (dead, 40 * MB)):
            self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=size)

        result = self.quota.reconcile()
        self.assertEqual(result["released"], 1)
        self.assertEqual(self.usage_rows(active)[0][2], "reserved")
        self.assertEqual(self.usage_rows(dead)[0][2], "released")
        self.assertEqual(self.usage_of(rule_id)["reserved_bytes"], 30 * MB)

    def test_reconcile_releases_reservation_of_vanished_download(self):
        rule_id = self.make_rule()
        self.set_limits(rule_id, [{"period": "day", "limit_bytes": 100 * MB}])
        download_id = self.make_download(rule_id, 40 * MB)
        self.quota.reserve(download_id=download_id, rule_id=rule_id, file_size=40 * MB)
        # Simulate external deletion, bypassing the normal immediate release hook.
        with self.database._connect() as conn:
            conn.execute('DELETE FROM downloads WHERE id=?', (download_id,))
        self.assertEqual(self.quota.reconcile()["released"], 1)
        self.assertEqual(self.usage_of(rule_id)["reserved_bytes"], 0)

    def test_quota_waiting_downloads_are_queryable_without_1000_limit(self):
        rule_id = self.make_rule()
        for index in range(30):
            self.make_download(rule_id, 1 * MB)
        waiting = self.make_download(rule_id, 1 * MB)
        self.database.update_download(
            waiting, status="queued", queue_reason="quota", queue_reason_text="等待额度", next_retry_at=None
        )
        rows = self.database.quota_waiting_downloads()
        self.assertEqual([row["id"] for row in rows], [waiting])
        self.assertTrue(self.database.has_quota_waiting_downloads())


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_wakes_periodically_and_stops_cleanly(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        database = Database(Path(tmp.name) / "state.db")
        rule_id = database.add_group_rule(chat_id=1, chat_title="g", quota_enabled=True)
        quota = QuotaService(database)
        quota.replace_limits(rule_id, [{"period": "hour", "limit_bytes": 10 * MB}])

        calls: list[int] = []

        async def wake() -> None:
            calls.append(1)

        scheduler = QuotaScheduler(quota, wake, tick_seconds=0.02)
        scheduler.start()
        self.assertTrue(scheduler.running)
        await asyncio.sleep(0.1)
        await scheduler.stop()
        self.assertFalse(scheduler.running)
        self.assertGreaterEqual(len(calls), 1, "调度器应在周期边界唤醒等待任务")


if __name__ == "__main__":
    unittest.main()
