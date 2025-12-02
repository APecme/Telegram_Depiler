from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    chat_id INTEGER,
                    bot_username TEXT,
                    file_name TEXT,
                    file_path TEXT,
                    status TEXT,
                    progress REAL,
                    download_speed REAL,
                    source TEXT,
                    tg_file_id INTEGER,
                    tg_access_hash INTEGER,
                    priority INTEGER DEFAULT 0,
                    error TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    chat_id INTEGER,
                    sender_id INTEGER,
                    sender_username TEXT,
                    sender_first_name TEXT,
                    sender_last_name TEXT,
                    message_text TEXT,
                    has_media BOOLEAN DEFAULT 0,
                    media_type TEXT,
                    file_name TEXT,
                    forward_from_id INTEGER,
                    forward_from_username TEXT,
                    forward_from_first_name TEXT,
                    forward_from_last_name TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS login_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_type TEXT NOT NULL,
                    user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone_number TEXT,
                    is_authorized BOOLEAN DEFAULT 0,
                    last_login DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_download_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    chat_title TEXT,
                    mode TEXT NOT NULL DEFAULT 'monitor', -- 'monitor' 或 'history'
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    include_extensions TEXT,
                    min_size_bytes INTEGER DEFAULT 0,
                    max_size_bytes INTEGER DEFAULT 0,
                    size_range TEXT DEFAULT '0',
                    save_dir TEXT,
                    filename_template TEXT,
                    include_keywords TEXT,
                    exclude_keywords TEXT,
                    match_mode TEXT DEFAULT 'all', -- 'all'、'include'、'exclude'
                    start_time DATETIME,
                    end_time DATETIME,
                    min_message_id INTEGER,
                    max_message_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """确保现有数据库表包含代码期望的所有列，用于兼容旧版本 state.db。"""

        def has_column(table: str, column: str) -> bool:
            cur = conn.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall()
            return any(row[1] == column or (isinstance(row, sqlite3.Row) and row["name"] == column) for row in rows)

        # downloads 表 - 确保文件路径、进度、下载速度、来源字段、错误信息和时间戳字段存在
        if has_column("downloads", "id"):
            if not has_column("downloads", "file_path"):
                conn.execute("ALTER TABLE downloads ADD COLUMN file_path TEXT")
            if not has_column("downloads", "status"):
                conn.execute("ALTER TABLE downloads ADD COLUMN status TEXT")
            if not has_column("downloads", "progress"):
                conn.execute("ALTER TABLE downloads ADD COLUMN progress REAL DEFAULT 0")
            if not has_column("downloads", "download_speed"):
                conn.execute("ALTER TABLE downloads ADD COLUMN download_speed REAL DEFAULT 0")
            if not has_column("downloads", "source"):
                conn.execute("ALTER TABLE downloads ADD COLUMN source TEXT DEFAULT 'bot'")
            if not has_column("downloads", "tg_file_id"):
                conn.execute("ALTER TABLE downloads ADD COLUMN tg_file_id INTEGER")
            if not has_column("downloads", "tg_access_hash"):
                conn.execute("ALTER TABLE downloads ADD COLUMN tg_access_hash INTEGER")
            if not has_column("downloads", "priority"):
                conn.execute("ALTER TABLE downloads ADD COLUMN priority INTEGER DEFAULT 0")
            if not has_column("downloads", "error"):
                conn.execute("ALTER TABLE downloads ADD COLUMN error TEXT")
            if not has_column("downloads", "created_at"):
                conn.execute(
                    "ALTER TABLE downloads ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                )
            if not has_column("downloads", "updated_at"):
                conn.execute(
                    "ALTER TABLE downloads ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                )

        # messages 表 - 确保媒体及转发相关字段和时间戳存在
        if has_column("messages", "id"):
            if not has_column("messages", "has_media"):
                conn.execute("ALTER TABLE messages ADD COLUMN has_media BOOLEAN DEFAULT 0")
            if not has_column("messages", "media_type"):
                conn.execute("ALTER TABLE messages ADD COLUMN media_type TEXT")
            if not has_column("messages", "file_name"):
                conn.execute("ALTER TABLE messages ADD COLUMN file_name TEXT")
            if not has_column("messages", "forward_from_id"):
                conn.execute("ALTER TABLE messages ADD COLUMN forward_from_id INTEGER")
            if not has_column("messages", "forward_from_username"):
                conn.execute("ALTER TABLE messages ADD COLUMN forward_from_username TEXT")
            if not has_column("messages", "forward_from_first_name"):
                conn.execute("ALTER TABLE messages ADD COLUMN forward_from_first_name TEXT")
            if not has_column("messages", "forward_from_last_name"):
                conn.execute("ALTER TABLE messages ADD COLUMN forward_from_last_name TEXT")
            if not has_column("messages", "created_at"):
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                )

        # login_state 表 - 确保 updated_at 字段存在（get_login_state 中会使用）
        if has_column("login_state", "id") and not has_column("login_state", "updated_at"):
            conn.execute(
                "ALTER TABLE login_state ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            )

        # group_download_rules 表 - 为后续新增字段补齐列
        if has_column("group_download_rules", "id"):
            if not has_column("group_download_rules", "start_time"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN start_time DATETIME")
            if not has_column("group_download_rules", "end_time"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN end_time DATETIME")
            if not has_column("group_download_rules", "max_size_bytes"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN max_size_bytes INTEGER DEFAULT 0")
            if not has_column("group_download_rules", "size_range"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN size_range TEXT DEFAULT '0'")
            if not has_column("group_download_rules", "min_message_id"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN min_message_id INTEGER")
            if not has_column("group_download_rules", "max_message_id"):
                conn.execute("ALTER TABLE group_download_rules ADD COLUMN max_message_id INTEGER")

        conn.commit()

    # Config helpers -----------------------------------------------------
    def set_config(self, values: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "REPLACE INTO config (key, value) VALUES (?, ?)",
                [(key, str(value) if value is not None else "") for key, value in values.items()],
            )
            conn.commit()

    def get_config(self) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
            return {row["key"]: row["value"] for row in rows}

    # Download helpers ---------------------------------------------------
    def add_download(
        self,
        message_id: int,
        chat_id: int,
        bot_username: str,
        file_name: str,
        status: str = "pending",
        source: str = "bot",
        tg_file_id: int | None = None,
        tg_access_hash: int | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO downloads (
                    message_id, chat_id, bot_username, file_name,
                    file_path, status, progress, download_speed,
                    source, tg_file_id, tg_access_hash
                )
                VALUES (?, ?, ?, ?, '', ?, 0, 0, ?, ?, ?)
                """,
                (
                    message_id,
                    chat_id,
                    bot_username,
                    file_name,
                    status,
                    source,
                    tg_file_id,
                    tg_access_hash,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_download(
        self,
        download_id: int,
        *,
        file_path: str | None = None,
        status: str | None = None,
        progress: float | None = None,
        download_speed: float | None = None,
        priority: int | None = None,
        error: str | None = None,
    ) -> None:
        updates = []
        params: List[Any] = []

        if file_path is not None:
            updates.append("file_path = ?")
            params.append(file_path)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if download_speed is not None:
            updates.append("download_speed = ?")
            params.append(download_speed)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE downloads SET {', '.join(updates)} WHERE id = ?"
        params.append(download_id)

        with self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def list_downloads(self, limit: int = 50) -> List[Dict[str, Any]]:
        """按时间倒序列出最近的下载记录。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_download(self, download_id: int) -> None:
        """删除指定的下载记录。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
            conn.commit()

    def find_download_by_telegram_file(
        self,
        tg_file_id: int,
        tg_access_hash: int,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM downloads
                WHERE tg_file_id = ? AND tg_access_hash = ? AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tg_file_id, tg_access_hash),
            ).fetchone()
            return dict(row) if row else None

    def get_download_stats(self) -> Dict[str, int]:
        """统计下载任务总体情况，避免仅基于有限条目计算不准确。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'downloading' THEN 1 ELSE 0 END) AS downloading
                FROM downloads
                """
            ).fetchone()

        return {
            "total": (row["total"] or 0) if row is not None else 0,
            "completed": (row["completed"] or 0) if row is not None else 0,
            "failed": (row["failed"] or 0) if row is not None else 0,
            "downloading": (row["downloading"] or 0) if row is not None else 0,
        }

    # Message helpers ---------------------------------------------------
    def add_message(
        self,
        message_id: int,
        chat_id: int,
        sender_id: int,
        sender_username: Optional[str] = None,
        sender_first_name: Optional[str] = None,
        sender_last_name: Optional[str] = None,
        message_text: Optional[str] = None,
        has_media: bool = False,
        media_type: Optional[str] = None,
        file_name: Optional[str] = None,
        forward_from_id: Optional[int] = None,
        forward_from_username: Optional[str] = None,
        forward_from_first_name: Optional[str] = None,
        forward_from_last_name: Optional[str] = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (
                    message_id, chat_id, sender_id, sender_username,
                    sender_first_name, sender_last_name, message_text,
                    has_media, media_type, file_name,
                    forward_from_id, forward_from_username,
                    forward_from_first_name, forward_from_last_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, chat_id, sender_id, sender_username,
                    sender_first_name, sender_last_name, message_text,
                    1 if has_media else 0, media_type, file_name,
                    forward_from_id, forward_from_username,
                    forward_from_first_name, forward_from_last_name,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    # Login state helpers ------------------------------------------------
    def save_login_state(
        self,
        account_type: str,  # 'user' or 'bot'
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        is_authorized: bool = True,
    ) -> None:
        """保存登录状态"""
        with self._connect() as conn:
            # 先删除旧的记录
            conn.execute("DELETE FROM login_state WHERE account_type = ?", (account_type,))
            # 插入新记录
            conn.execute(
                """
                INSERT INTO login_state (
                    account_type, user_id, username, first_name, last_name,
                    phone_number, is_authorized, last_login
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (account_type, user_id, username, first_name, last_name, phone_number, 1 if is_authorized else 0),
            )
            conn.commit()

    def get_login_state(self) -> Optional[Dict[str, Any]]:
        """获取登录状态"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM login_state ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def clear_login_state(self) -> None:
        """清除登录状态"""
        with self._connect() as conn:
            conn.execute("DELETE FROM login_state")
            conn.commit()

    # Group download rule helpers -----------------------------------------
    @staticmethod
    def parse_size_range(size_range: str) -> tuple[int, int]:
        """解析体积范围字符串，返回 (min_bytes, max_bytes)。
        
        示例：
        - "0" -> (0, 0) 不限制
        - "10" -> (10MB, 0) >= 10MB
        - "10-100" -> (10MB, 100MB) 10MB ~ 100MB
        - "0-100" -> (0, 100MB) <= 100MB
        """
        size_range = (size_range or "0").strip()
        if not size_range or size_range == "0":
            return (0, 0)
        
        if "-" in size_range:
            parts = size_range.split("-", 1)
            try:
                min_mb = float(parts[0].strip()) if parts[0].strip() else 0
                max_mb = float(parts[1].strip()) if parts[1].strip() else 0
                return (int(min_mb * 1024 * 1024), int(max_mb * 1024 * 1024))
            except ValueError:
                return (0, 0)
        else:
            try:
                min_mb = float(size_range)
                return (int(min_mb * 1024 * 1024), 0)
            except ValueError:
                return (0, 0)

    def add_group_rule(
        self,
        *,
        chat_id: int,
        chat_title: str | None = None,
        mode: str = "monitor",
        enabled: bool = True,
        include_extensions: str | None = None,
        min_size_bytes: int = 0,
        max_size_bytes: int = 0,
        size_range: str = "0",
        save_dir: str | None = None,
        filename_template: str | None = None,
        include_keywords: str | None = None,
        exclude_keywords: str | None = None,
        match_mode: str = "all",
        start_time: str | None = None,
        end_time: str | None = None,
        min_message_id: int | None = None,
        max_message_id: int | None = None,
    ) -> int:
        """新增一条群聊下载规则，返回规则ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO group_download_rules (
                    chat_id, chat_title, mode, enabled,
                    include_extensions, min_size_bytes, max_size_bytes, size_range, save_dir,
                    filename_template, include_keywords, exclude_keywords,
                    match_mode, start_time, end_time,
                    min_message_id, max_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    chat_title,
                    mode,
                    1 if enabled else 0,
                    (include_extensions or ""),
                    int(min_size_bytes or 0),
                    int(max_size_bytes or 0),
                    (size_range or "0"),
                    (save_dir or ""),
                    (filename_template or ""),
                    (include_keywords or ""),
                    (exclude_keywords or ""),
                    (match_mode or "all"),
                    start_time,
                    end_time,
                    min_message_id,
                    max_message_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_group_rule(
        self,
        rule_id: int,
        *,
        chat_title: str | None = None,
        mode: str | None = None,
        enabled: bool | None = None,
        include_extensions: str | None = None,
        min_size_bytes: int | None = None,
        max_size_bytes: int | None = None,
        size_range: str | None = None,
        save_dir: str | None = None,
        filename_template: str | None = None,
        include_keywords: str | None = None,
        exclude_keywords: str | None = None,
        match_mode: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        min_message_id: int | None = None,
        max_message_id: int | None = None,
    ) -> None:
        """更新一条群聊下载规则。"""
        updates: list[str] = []
        params: list[Any] = []

        if chat_title is not None:
            updates.append("chat_title = ?")
            params.append(chat_title)
        if mode is not None:
            updates.append("mode = ?")
            params.append(mode)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if include_extensions is not None:
            updates.append("include_extensions = ?")
            params.append(include_extensions)
        if min_size_bytes is not None:
            updates.append("min_size_bytes = ?")
            params.append(int(min_size_bytes))
        if max_size_bytes is not None:
            updates.append("max_size_bytes = ?")
            params.append(int(max_size_bytes))
        if size_range is not None:
            updates.append("size_range = ?")
            params.append(size_range)
        if save_dir is not None:
            updates.append("save_dir = ?")
            params.append(save_dir)
        if filename_template is not None:
            updates.append("filename_template = ?")
            params.append(filename_template)
        if include_keywords is not None:
            updates.append("include_keywords = ?")
            params.append(include_keywords)
        if exclude_keywords is not None:
            updates.append("exclude_keywords = ?")
            params.append(exclude_keywords)
        if match_mode is not None:
            updates.append("match_mode = ?")
            params.append(match_mode)
        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time)
        if end_time is not None:
            updates.append("end_time = ?")
            params.append(end_time)
        if min_message_id is not None:
            updates.append("min_message_id = ?")
            params.append(int(min_message_id))
        if max_message_id is not None:
            updates.append("max_message_id = ?")
            params.append(int(max_message_id))

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE group_download_rules SET {', '.join(updates)} WHERE id = ?"
        params.append(rule_id)

        with self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def delete_group_rule(self, rule_id: int) -> None:
        """删除一条群聊下载规则。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM group_download_rules WHERE id = ?", (rule_id,))
            conn.commit()

    def get_group_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取单条规则。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_download_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_group_rules(
        self,
        *,
        chat_id: int | None = None,
        mode: str | None = None,
    ) -> List[Dict[str, Any]]:
        """列出群聊下载规则，支持按 chat_id 和 mode 过滤。"""
        sql = "SELECT * FROM group_download_rules WHERE 1=1"
        params: list[Any] = []
        if chat_id is not None:
            sql += " AND chat_id = ?"
            params.append(chat_id)
        if mode is not None:
            sql += " AND mode = ?"
            params.append(mode)
        sql += " ORDER BY created_at DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def get_group_rules_for_chat(
        self,
        chat_id: int,
        *,
        mode: str | None = None,
        only_enabled: bool = True,
    ) -> List[Dict[str, Any]]:
        """获取某个群聊的规则列表，通常用于实际下载逻辑。

        :param chat_id: 群聊 ID
        :param mode: 规则模式（monitor/history），为 None 时不过滤
        :param only_enabled: 是否只返回启用的规则
        """
        sql = "SELECT * FROM group_download_rules WHERE chat_id = ?"
        params: list[Any] = [chat_id]
        if mode is not None:
            sql += " AND mode = ?"
            params.append(mode)
        if only_enabled:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]


