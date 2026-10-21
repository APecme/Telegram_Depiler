"""群聊下载容量限额（quota）核心实现。

设计要点：

* 限额按"每条群聊规则"配置，一条规则可以同时开启多个限额（每小时 / 每日 /
  每周 / 每月 / 自定义 N 小时或 N 天），**所有限额都满足**才允许下载。
* 计费单位内部统一使用整数 bytes（1 MB = 1024 * 1024 bytes）。
* 只统计媒体文件下载；``content_type == "message_text"`` 的文本导出永远不会被限额。
* 准入是"整文件"准入：``已用 + 预占 + 文件大小 <= 限额``，绝不出现下到一半因为额度
  用完而被截断的情况；剩余额度不足时任务整笔等待，等周期恢复后自动重跑。
* 预占（reserve）/ 转已用（commit）/ 释放（release）全部在 SQLite 事务里完成
  （``BEGIN IMMEDIATE``），因此跨进程也只允许"两笔竞争只有一笔通过"。
* 用量账本 ``group_rule_quota_usage`` 独立于 ``downloads`` 记录：成功用量按**开始时**
  所在周期记账，删除下载记录或删除文件都不返还。

时间约定：数据库里保存的时间统一为 **UTC 朴素时间字符串**
（``YYYY-MM-DD HH:MM:SS``），周期边界按配置时区（默认 ``Asia/Shanghai``）计算。
"""

from __future__ import annotations

import calendar
import math
import sqlite3
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

try:  # pragma: no cover - zoneinfo 在 3.9+ 一定存在，这里只防御 tzdata 缺失
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

DEFAULT_TIMEZONE = "Asia/Shanghai"

CAPACITY_UNITS: Dict[str, int] = {
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
}

QUOTA_PERIODS = ("hour", "day", "week", "month", "custom")
CUSTOM_UNITS = ("hours", "days")

PERIOD_LABELS = {
    "hour": "每小时",
    "day": "每日",
    "week": "每周",
    "month": "每月",
}

STATE_RESERVED = "reserved"
STATE_COMMITTED = "committed"
STATE_RELEASED = "released"

#: 预占仍然有效的下载状态；其余状态（已完成 / 失败 / 取消 / 暂停 / 记录已删除）
#: 在启动自检时会被释放，避免"永久预占"。
ACTIVE_DOWNLOAD_STATUSES = ("pending", "queued", "downloading")

DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class QuotaConfigError(ValueError):
    """限额配置非法。"""


# ---------------------------------------------------------------------------
# 基础工具：容量、格式化、周期
# ---------------------------------------------------------------------------
def parse_capacity(value: Any, unit: str | None = "GB") -> int:
    """把 ``(数值, 单位)`` 转成整数 bytes（1024 进制）。"""
    unit_key = str(unit or "GB").strip().upper()
    if unit_key not in CAPACITY_UNITS:
        raise QuotaConfigError(f"不支持的容量单位: {unit}")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise QuotaConfigError(f"容量数值非法: {value}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise QuotaConfigError("容量必须大于 0")
    result = int(round(amount * CAPACITY_UNITS[unit_key]))
    if not 0 < result <= 2 ** 53 - 1:
        raise QuotaConfigError("容量必须在 1 字节到 8192 TB 之间")
    return result


def format_bytes(num_bytes: Any) -> str:
    """人类可读的容量字符串（1024 进制）。"""
    try:
        value = float(num_bytes or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def utc_now() -> datetime:
    """当前 UTC 朴素时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_db_time(value: datetime) -> str:
    """把 datetime 转成数据库时间字符串（UTC 朴素）。"""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime(DB_TIME_FORMAT)


def from_db_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip().replace("T", " ")
    for fmt in (DB_TIME_FORMAT, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise QuotaConfigError(f"无法解析时间: {value}")


def resolve_timezone(name: str | None) -> tzinfo:
    """解析时区；tzdata 缺失时退化为固定 UTC+8。"""
    candidate = (name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    if ZoneInfo is not None:
        try:
            return ZoneInfo(candidate)
        except Exception:
            pass
    return timezone(timedelta(hours=8))


def add_months(value: datetime, months: int) -> datetime:
    """按自然月加减，正确处理大小月与闰年 2 月。"""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def custom_interval(limit: Mapping[str, Any]) -> timedelta:
    unit = str(limit.get("custom_unit") or "hours").strip().lower()
    try:
        amount = int(limit.get("custom_value") or 1)
    except (TypeError, ValueError):
        amount = 1
    amount = max(1, amount)
    if unit == "days":
        return timedelta(days=amount)
    return timedelta(hours=amount)


def period_label(limit: Mapping[str, Any]) -> str:
    period = str(limit.get("period") or "day")
    if period == "custom":
        unit = "小时" if str(limit.get("custom_unit") or "hours").lower() != "days" else "天"
        return f"每{int(limit.get('custom_value') or 1)}{unit}"
    return PERIOD_LABELS.get(period, period)


def period_window(
    limit: Mapping[str, Any],
    now_utc: datetime,
    tz: tzinfo,
) -> tuple[datetime, datetime]:
    """返回 ``now`` 所在周期的 ``(start_utc, end_utc)``（UTC 朴素时间）。

    自然周期按 ``tz`` 的整点 / 零点 / 周一 / 月初对齐；custom 周期以限额启用时的
    锚点（``anchor_at``）为基准向前滚动。
    """
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    period = str(limit.get("period") or "day")

    if period == "hour":
        start_local = local_now.replace(minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(hours=1)
    elif period == "day":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
    elif period == "week":
        start_local = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_local = start_local + timedelta(days=7)
    elif period == "month":
        start_local = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = add_months(start_local, 1)
    elif period == "custom":
        interval = custom_interval(limit)
        anchor_utc = from_db_time(limit.get("anchor_at")) or now_utc
        anchor_local = anchor_utc.replace(tzinfo=timezone.utc).astimezone(tz)
        if local_now <= anchor_local:
            start_local = anchor_local
        else:
            elapsed = local_now - anchor_local
            steps = int(elapsed // interval)
            start_local = anchor_local + interval * steps
        end_local = start_local + interval
    else:  # 未知周期按每日处理，保证不会"无限额"
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


# ---------------------------------------------------------------------------
# 准入结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Admission:
    """一次准入判定的结果。"""

    admitted: bool
    blocking: Optional[str] = None  # 'quota' | 'too_large' | 'unknown_size' | 'rule'
    reason: str = ""
    next_retry_at: Optional[str] = None
    limit_label: Optional[str] = None
    remaining_bytes: Optional[int] = None

    @property
    def too_large(self) -> bool:
        return self.blocking == "too_large"


# ---------------------------------------------------------------------------
# QuotaService
# ---------------------------------------------------------------------------
class QuotaService:
    """群聊规则下载限额：配置读写 + 事务化预占/结算 + 用量统计。"""

    def __init__(self, database: Any, *, timezone_name: str | None = None, clock=None):
        self.database = database
        self._timezone_name = timezone_name
        self._clock = clock
        self._tz_cache: tuple[str, tzinfo] | None = None

    # -- 基础 ---------------------------------------------------------
    @property
    def timezone_name(self) -> str:
        if self._timezone_name:
            return self._timezone_name
        try:
            configured = self.database.get_config("quota_timezone")
        except Exception:  # pragma: no cover - 防御性
            configured = None
        return str(configured or DEFAULT_TIMEZONE)

    @property
    def timezone(self) -> tzinfo:
        name = self.timezone_name
        if self._tz_cache is None or self._tz_cache[0] != name:
            self._tz_cache = (name, resolve_timezone(name))
        return self._tz_cache[1]

    def now(self) -> datetime:
        if self._clock is not None:
            value = self._clock()
        else:
            value = utc_now()
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _db_path(self):
        return self.database.db_path

    @contextmanager
    def _transaction(self):
        """开启 ``BEGIN IMMEDIATE`` 事务，保证检查 + 预占的原子性。"""
        conn = sqlite3.connect(str(self._db_path()), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def _read(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path()), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    # -- 配置读写 -----------------------------------------------------
    def list_limits(self, rule_id: int) -> List[Dict[str, Any]]:
        conn = self._read()
        try:
            rows = conn.execute(
                "SELECT * FROM group_rule_quota_limits WHERE rule_id = ? ORDER BY id ASC",
                (int(rule_id),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def rule_quota_enabled(self, rule: Mapping[str, Any] | None) -> bool:
        if not rule:
            return False
        try:
            return bool(int(rule.get("quota_enabled") or 0))
        except (TypeError, ValueError):
            return False

    def replace_limits(self, rule_id: int, limits: Sequence[Mapping[str, Any]], *, _conn: sqlite3.Connection | None = None) -> List[Dict[str, Any]]:
        """整体替换某条规则的限额列表。

        * 携带 ``id`` 且属于该规则的条目原地更新（保留 ``anchor_at`` 与历史账本）。
        * 新增条目不带 ``id``，custom 周期此时写入锚点。
        * 未出现在列表里的旧条目会被删除（账本行保留，仅不再参与计算）。
        """
        rule_id = int(rule_id)
        now = self.now()
        normalized = [self._normalize_limit(item, rule_id=rule_id) for item in limits]

        with (self._transaction() if _conn is None else nullcontext(_conn)) as conn:
            existing_rows = conn.execute(
                "SELECT * FROM group_rule_quota_limits WHERE rule_id = ? ORDER BY id ASC",
                (rule_id,),
            ).fetchall()
            existing = {int(row["id"]): dict(row) for row in existing_rows}
            kept: set[int] = set()
            supplied = [int(item['id']) for item in normalized if item.get('id') is not None]
            if len(supplied) != len(set(supplied)) or any(value not in existing for value in supplied):
                raise QuotaConfigError("限额 ID 重复或不属于当前规则")

            for item in normalized:
                limit_id = item.get("id")
                anchor = item.get("anchor_at")
                if limit_id is not None and int(limit_id) in existing:
                    current = existing[int(limit_id)]
                    anchor = current.get("anchor_at") or anchor
                    if item["period"] == "custom" and not anchor:
                        anchor = to_db_time(now)
                    conn.execute(
                        """
                        UPDATE group_rule_quota_limits
                        SET period = ?, custom_value = ?, custom_unit = ?, limit_bytes = ?,
                            anchor_at = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND rule_id = ?
                        """,
                        (
                            item["period"],
                            item["custom_value"],
                            item["custom_unit"],
                            item["limit_bytes"],
                            anchor,
                            int(limit_id),
                            rule_id,
                        ),
                    )
                    kept.add(int(limit_id))
                else:
                    if item["period"] == "custom" and not anchor:
                        anchor = to_db_time(now)
                    cur = conn.execute(
                        """
                        INSERT INTO group_rule_quota_limits
                            (rule_id, period, custom_value, custom_unit, limit_bytes, anchor_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rule_id,
                            item["period"],
                            item["custom_value"],
                            item["custom_unit"],
                            item["limit_bytes"],
                            anchor,
                        ),
                    )
                    new_id = int(cur.lastrowid)
                    kept.add(new_id)
                    # A new constraint must include transfers already in progress.
                    # Existing transfers continue even if their total exceeds the new cap.
                    conn.execute(
                        "INSERT INTO group_rule_quota_usage (rule_id, limit_id, download_id, bytes, state, started_at) "
                        "SELECT ?, ?, d.id, d.file_size, 'reserved', "
                        "COALESCE((SELECT MIN(u.started_at) FROM group_rule_quota_usage u "
                        "WHERE u.download_id=d.id AND u.state='reserved'), ?) "
                        "FROM downloads d WHERE d.rule_id=? AND d.source='rule' "
                        "AND d.status='downloading' AND d.file_size>0",
                        (rule_id, new_id, to_db_time(now), rule_id),
                    )

            for limit_id in existing.keys() - kept:
                conn.execute(
                    "DELETE FROM group_rule_quota_limits WHERE id = ? AND rule_id = ?",
                    (limit_id, rule_id),
                )
        if _conn is not None:
            return [dict(row) for row in _conn.execute('SELECT * FROM group_rule_quota_limits WHERE rule_id=? ORDER BY id', (rule_id,))]
        return self.list_limits(rule_id)

    def delete_rule_limits(self, rule_id: int) -> None:
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM group_rule_quota_limits WHERE rule_id = ?", (int(rule_id),)
            )

    def _normalize_limit(self, item: Mapping[str, Any], *, rule_id: int) -> Dict[str, Any]:
        period = str(item.get("period") or "day").strip().lower()
        if period not in QUOTA_PERIODS:
            raise QuotaConfigError(f"不支持的限额周期: {item.get('period')}")

        custom_value: Optional[int] = None
        custom_unit: Optional[str] = None
        limit_bytes = item.get("limit_bytes")

        if limit_bytes not in (None, "", 0):
            try:
                limit_bytes = int(limit_bytes)
            except (TypeError, ValueError) as exc:
                raise QuotaConfigError(f"限额字节数非法: {item.get('limit_bytes')}") from exc
            if not 0 < limit_bytes <= 2 ** 53 - 1:
                raise QuotaConfigError("限额必须大于 0")
        else:
            value = item.get("limit_value", item.get("value"))
            unit = item.get("limit_unit", item.get("unit") or "GB")
            limit_bytes = parse_capacity(value, unit)

        if period == "custom":
            custom_unit = str(item.get("custom_unit") or "hours").strip().lower()
            if custom_unit not in CUSTOM_UNITS:
                raise QuotaConfigError(f"不支持的自定义周期单位: {item.get('custom_unit')}")
            try:
                custom_value = int(item.get("custom_value") or 0)
            except (TypeError, ValueError) as exc:
                raise QuotaConfigError("自定义周期数值非法") from exc
            if custom_value <= 0:
                raise QuotaConfigError("自定义周期必须大于 0")
            if custom_value > 100000:
                raise QuotaConfigError("自定义周期过大")

        limit_id = item.get("id")
        try:
            limit_id = int(limit_id) if limit_id not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise QuotaConfigError(f"限额 id 非法: {item.get('id')}") from exc

        return {
            "id": limit_id,
            "period": period,
            "custom_value": custom_value,
            "custom_unit": custom_unit,
            "limit_bytes": int(limit_bytes),
            "anchor_at": item.get("anchor_at"),
        }

    # -- 用量统计 -----------------------------------------------------
    def _committed_bytes(
        self, conn: sqlite3.Connection, limit_id: int, window_start: datetime
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(bytes), 0) AS total FROM group_rule_quota_usage
            WHERE limit_id = ? AND state = ? AND started_at >= ?
            """,
            (int(limit_id), STATE_COMMITTED, to_db_time(window_start)),
        ).fetchone()
        return int(row["total"] or 0)

    def _reserved_bytes(
        self, conn: sqlite3.Connection, limit_id: int, exclude_download_id: int | None = None
    ) -> int:
        sql = (
            "SELECT COALESCE(SUM(bytes), 0) AS total FROM group_rule_quota_usage "
            "WHERE limit_id = ? AND state = ?"
        )
        params: list[Any] = [int(limit_id), STATE_RESERVED]
        if exclude_download_id is not None:
            sql += " AND (download_id IS NULL OR download_id != ?)"
            params.append(int(exclude_download_id))
        row = conn.execute(sql, params).fetchone()
        return int(row["total"] or 0)

    def usage(self, rule_id: int, *, now: datetime | None = None) -> List[Dict[str, Any]]:
        """返回某条规则每个限额的已用 / 预占 / 剩余 / 下次恢复时间。"""
        now = now or self.now()
        limits = self.list_limits(rule_id)
        if not limits:
            return []
        conn = self._read()
        try:
            items: List[Dict[str, Any]] = []
            for limit in limits:
                window_start, window_end = period_window(limit, now, self.timezone)
                used = self._committed_bytes(conn, int(limit["id"]), window_start)
                reserved = self._reserved_bytes(conn, int(limit["id"]))
                limit_bytes = int(limit["limit_bytes"] or 0)
                remaining = max(0, limit_bytes - used - reserved)
                items.append(
                    {
                        "id": int(limit["id"]),
                        "period": limit["period"],
                        "period_label": period_label(limit),
                        "custom_value": limit.get("custom_value"),
                        "custom_unit": limit.get("custom_unit"),
                        "limit_bytes": limit_bytes,
                        "used_bytes": used,
                        "reserved_bytes": reserved,
                        "remaining_bytes": remaining,
                        "anchor_at": limit.get("anchor_at"),
                        "period_start": to_db_time(window_start),
                        "next_reset_at": to_db_time(window_end),
                        "next_reset_at_local": self._local_text(window_end),
                    }
                )
            return items
        finally:
            conn.close()

    def _local_text(self, utc_value: datetime) -> str:
        return (
            utc_value.replace(tzinfo=timezone.utc)
            .astimezone(self.timezone)
            .strftime("%Y-%m-%d %H:%M")
        )

    def summary(self, rule_id: int, *, now: datetime | None = None) -> Dict[str, Any]:
        limits = self.usage(rule_id, now=now)
        return {
            "enabled": bool(limits),
            "timezone": self.timezone_name,
            "limits": limits,
        }

    def summaries(self, rule_ids: Iterable[int], *, now: datetime | None = None) -> Dict[int, Dict[str, Any]]:
        now = now or self.now()
        return {int(rule_id): self.summary(int(rule_id), now=now) for rule_id in rule_ids}

    # -- 准入 / 结算 ---------------------------------------------------
    def reserve(
        self,
        *,
        download_id: int,
        rule_id: int | None,
        file_size: int | None,
        content_type: str = "media",
        now: datetime | None = None,
        _conn: sqlite3.Connection | None = None,
    ) -> Admission:
        """尝试为一次完整下载预占额度（原子）。"""
        if str(content_type or "media") != "media":
            # 文本导出不计费，也不允许被限额挡住。
            return Admission(admitted=True)
        if rule_id is None:
            return Admission(admitted=True)
        now = now or self.now()
        size = int(file_size or 0)

        with (self._transaction() if _conn is None else nullcontext(_conn)) as conn:
            rule_row = conn.execute(
                "SELECT * FROM group_download_rules WHERE id = ?", (int(rule_id),)
            ).fetchone()
            if rule_row is None:
                return Admission(admitted=False, blocking="rule", reason="下载规则不存在，已停止下载")
            if not int(rule_row["enabled"] or 0):
                return Admission(admitted=False, blocking="rule", reason="规则已禁用，已停止下载")
            if not int(rule_row["quota_enabled"] or 0):
                self._release_locked(conn, download_id)
                return Admission(admitted=True)

            limits = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM group_rule_quota_limits WHERE rule_id = ? ORDER BY id ASC",
                    (int(rule_id),),
                ).fetchall()
            ]
            if not limits:
                self._release_locked(conn, download_id)
                return Admission(admitted=True)

            if size <= 0:
                return Admission(
                    admitted=False,
                    blocking="unknown_size",
                    reason="无法获取文件大小，为避免绕过下载限额已暂不下载",
                )

            existing = {
                int(row["limit_id"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM group_rule_quota_usage WHERE download_id = ? AND state IN (?, ?)",
                    (int(download_id), STATE_RESERVED, STATE_COMMITTED),
                ).fetchall()
            }
            if any(item['state'] == STATE_COMMITTED for item in existing.values()):
                return Admission(False, blocking='busy', reason='该文件已结算，不能重复预占')
            limit_ids = {int(limit["id"]) for limit in limits}
            if limit_ids and limit_ids <= set(existing.keys()) and all(
                int(existing[lid]["bytes"]) == size and existing[lid]["state"] == STATE_RESERVED
                for lid in limit_ids
            ):
                # 幂等：本次下载已经预占过同样的额度，无需重复操作。
                return Admission(admitted=True)

            blocking: list[tuple[Dict[str, Any], str, int, datetime]] = []
            for limit in limits:
                window_start, window_end = period_window(limit, now, self.timezone)
                limit_bytes = int(limit["limit_bytes"] or 0)
                used = self._committed_bytes(conn, int(limit["id"]), window_start)
                reserved = self._reserved_bytes(conn, int(limit["id"]), exclude_download_id=download_id)
                remaining = limit_bytes - used - reserved
                if size > limit_bytes:
                    blocking.append((limit, "too_large", remaining, window_end))
                elif used + reserved + size > limit_bytes:
                    blocking.append((limit, "quota", remaining, window_end))

            if blocking:
                return self._blocked_admission(blocking, size)

            started = to_db_time(now)
            for limit in limits:
                limit_id = int(limit["id"])
                conn.execute(
                    """
                    INSERT INTO group_rule_quota_usage
                        (rule_id, limit_id, download_id, bytes, state, started_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(download_id, limit_id) DO UPDATE SET
                        rule_id = excluded.rule_id,
                        bytes = excluded.bytes,
                        state = excluded.state,
                        started_at = CASE
                            WHEN group_rule_quota_usage.state = 'committed'
                                THEN group_rule_quota_usage.started_at
                            ELSE excluded.started_at
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (int(rule_id), limit_id, int(download_id), size, STATE_RESERVED, started),
                )
            return Admission(admitted=True)

    def claim(self, download_id: int, max_concurrent: int) -> Admission:
        """Atomically claim a queue slot and reserve the whole file exactly once."""
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
            if row is None or row['status'] in ('downloading', 'completed', 'cancelled'):
                return Admission(False, blocking='busy', reason='任务不存在或已启动')
            count = conn.execute("SELECT COUNT(*) FROM downloads WHERE status = 'downloading'").fetchone()[0]
            if count >= max_concurrent:
                conn.execute("UPDATE downloads SET status='queued', queue_reason='concurrency', "
                             "queue_reason_text=?, next_retry_at=NULL, download_speed=0 WHERE id=?",
                             (f'已达到并发上限（{max_concurrent} 个任务），等待空闲位置', download_id))
                return Admission(False, blocking='concurrency')
            rule_id = row['rule_id'] if row['source'] == 'rule' else None
            rule = conn.execute("SELECT * FROM group_download_rules WHERE id=?", (rule_id,)).fetchone()
            content_type = rule['content_type'] if rule else 'media'
            result = self.reserve(download_id=download_id, rule_id=rule_id,
                                  file_size=row['file_size'], content_type=content_type, _conn=conn)
            if not result.admitted:
                failed = result.blocking in ('too_large', 'rule')
                retry = result.next_retry_at or to_db_time(self.now() + timedelta(hours=1))
                conn.execute("UPDATE downloads SET status=?, queue_reason=?, queue_reason_text=?, "
                             "error=?, next_retry_at=?, download_speed=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             ('failed' if failed else 'queued', '' if failed else 'quota', result.reason,
                              result.reason if failed else '', None if failed else retry, download_id))
                self._release_locked(conn, download_id)
                return result
            conn.execute("UPDATE downloads SET status='downloading', queue_reason=NULL, queue_reason_text=NULL, "
                         "next_retry_at=NULL, error='', download_speed=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (download_id,))
            return result

    def _blocked_admission(
        self,
        blocking: Sequence[tuple[Dict[str, Any], str, int, datetime]],
        size: int,
    ) -> Admission:
        too_large = [item for item in blocking if item[1] == "too_large"]
        chosen = too_large[0] if too_large else max(blocking, key=lambda item: item[3])
        limit, kind, remaining, window_end = chosen
        label = period_label(limit)
        limit_bytes = int(limit["limit_bytes"] or 0)
        if kind == "too_large":
            reason = (
                f"文件大小 {format_bytes(size)} 超过{label}限额 {format_bytes(limit_bytes)}，"
                f"当前限额下无法下载，请修改该规则的下载限额"
            )
            return Admission(
                admitted=False,
                blocking="too_large",
                reason=reason,
                limit_label=label,
                remaining_bytes=max(0, remaining),
            )
        # 全部约束都满足才可下载，显示最后一个阻塞周期的恢复时间。
        # 释放预占时仍会立即重新评估，不必等到这个预计时间。
        retry_at = max(item[3] for item in blocking)
        reason = (
            f"等待下载额度：{label}限额 {format_bytes(limit_bytes)}，"
            f"当前剩余 {format_bytes(max(0, remaining))}，本文件需要 {format_bytes(size)}，"
            f"预计 {self._local_text(retry_at)}（{self.timezone_name}）恢复"
        )
        return Admission(
            admitted=False,
            blocking="quota",
            reason=reason,
            next_retry_at=to_db_time(retry_at),
            limit_label=label,
            remaining_bytes=max(0, remaining),
        )

    def commit(self, download_id: int) -> int:
        """把预占转为已用（成功完成）。幂等。"""
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE group_rule_quota_usage SET state = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE download_id = ? AND state = ?",
                (STATE_COMMITTED, int(download_id), STATE_RESERVED),
            )
            return int(cur.rowcount or 0)

    def release(self, download_id: int) -> int:
        """释放预占（失败 / 取消 / 暂停 / 删除）。幂等，且不影响已成功用量。"""
        with self._transaction() as conn:
            return self._release_locked(conn, download_id)

    def _release_locked(self, conn: sqlite3.Connection, download_id: int) -> int:
        cur = conn.execute(
            "UPDATE group_rule_quota_usage SET state = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE download_id = ? AND state = ?",
            (STATE_RELEASED, int(download_id), STATE_RESERVED),
        )
        return int(cur.rowcount or 0)

    # -- 启动自检 / 唤醒 -----------------------------------------------
    def reconcile(self) -> Dict[str, int]:
        """启动自检：释放失效预占，并让等待额度任务立即重新评估。"""
        released = 0
        with self._transaction() as conn:
            conn.execute("UPDATE group_rule_quota_usage SET state='committed' WHERE state='reserved' "
                         "AND download_id IN (SELECT id FROM downloads WHERE status='completed')")
            placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
            stale = conn.execute(
                f"""
                SELECT id FROM group_rule_quota_usage
                WHERE state = ?
                  AND (
                        download_id IS NULL
                        OR download_id NOT IN (
                            SELECT id FROM downloads WHERE status IN ({placeholders})
                        )
                  )
                """,
                (STATE_RESERVED, *ACTIVE_DOWNLOAD_STATUSES),
            ).fetchall()
            if stale:
                ids = [int(row["id"]) for row in stale]
                conn.executemany(
                    "UPDATE group_rule_quota_usage SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [(STATE_RELEASED, usage_id) for usage_id in ids],
                )
                released = len(ids)
            # 让所有额度等待中的任务立刻被调度器重新评估。
            conn.execute(
                "UPDATE downloads SET next_retry_at = CURRENT_TIMESTAMP "
                "WHERE status = 'queued' AND queue_reason = 'quota'"
            )
        return {"released": released}

    def next_wake_delay(self, *, now: datetime | None = None, cap: float = 60.0) -> float:
        """距离下一次"可能恢复额度"的秒数（用于调度器）。"""
        now = now or self.now()
        conn = self._read()
        try:
            rows = conn.execute(
                "SELECT * FROM group_rule_quota_limits WHERE rule_id IN "
                "(SELECT id FROM group_download_rules WHERE quota_enabled = 1)"
            ).fetchall()
            next_end: datetime | None = None
            for row in rows:
                _, window_end = period_window(dict(row), now, self.timezone)
                if next_end is None or window_end < next_end:
                    next_end = window_end
            waiting = conn.execute(
                "SELECT MIN(next_retry_at) AS next FROM downloads "
                "WHERE status = 'queued' AND queue_reason = 'quota'"
            ).fetchone()
        finally:
            conn.close()

        candidates: list[datetime] = []
        if next_end is not None:
            candidates.append(next_end)
        retry_at = from_db_time(waiting["next"] if waiting else None) if waiting else None
        if retry_at is not None:
            candidates.append(retry_at)
        if not candidates:
            return float(cap)
        delay = min(candidates, key=lambda value: value) - now
        seconds = min(float(delay.total_seconds()) + 1.0, float(cap))
        return max(seconds, min(0.05, float(cap)))


def record_block(
    database: Any,
    download_id: int,
    admission: Admission,
    *,
    now: datetime | None = None,
    unknown_size_retry_seconds: int = 3600,
) -> None:
    """把一次"未准入"的判定结果写回下载记录。

    * ``too_large`` / ``rule``：明确失败，不做无意义的网络重试；
    * ``quota``：留在队列里等待额度，带 ``queue_reason='quota'`` 与 ``next_retry_at``；
    * ``unknown_size``：同样等待（定期重新补取文件大小），而不是盲下载。
    """
    now = now or utc_now()
    if admission.blocking in ("too_large", "rule"):
        database.update_download(
            download_id,
            status="failed",
            error=admission.reason,
            queue_reason="",
            queue_reason_text=admission.reason,
            next_retry_at="",
            download_speed=0,
        )
        return

    retry_at = admission.next_retry_at
    if not retry_at:
        retry_at = to_db_time(now + timedelta(seconds=int(unknown_size_retry_seconds)))
    database.update_download(
        download_id,
        status="queued",
        queue_reason="quota",
        queue_reason_text=admission.reason or "等待下载额度",
        next_retry_at=retry_at,
        download_speed=0,
    )


def clear_queue_wait_state(database: Any, download_id: int) -> None:
    """清理排队原因（开始下载 / 重试时调用）。"""
    database.update_download(
        download_id,
        queue_reason="",
        queue_reason_text="",
        next_retry_at="",
    )


class QuotaScheduler:
    """周期恢复调度器。

    * 周期到期（整点 / 零点 / 周一 / 月初 / custom 锚点）时唤醒等待额度任务，
      即使当前没有任何下载任务在跑、也没有新消息。
    * 程序关闭时 ``stop()`` 会取消后台任务，不会留下悬挂协程。
    """

    def __init__(self, quota: QuotaService, wake_callback, *, tick_seconds: float = 30.0, logger=None):
        self.quota = quota
        self.wake_callback = wake_callback
        self.tick_seconds = float(tick_seconds)
        self._task = None
        self._logger = logger

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        import asyncio

        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        import asyncio

        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - 正常取消路径
            pass
        except Exception as exc:  # pragma: no cover - 防御性
            if self._logger:
                self._logger.warning("下载限额调度器退出异常: %s", exc)

    async def _run(self) -> None:  # pragma: no cover - 由集成测试覆盖具体行为
        import asyncio

        try:
            while True:
                delay = self.quota.next_wake_delay(cap=self.tick_seconds)
                await asyncio.sleep(delay)
                try:
                    await self.wake_callback()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._logger:
                        self._logger.warning("下载限额调度器唤醒失败: %s", exc)
        except asyncio.CancelledError:
            raise
