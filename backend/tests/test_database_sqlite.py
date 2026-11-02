import gc
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.app.database import Database


class SQLiteHardeningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.path = Path(directory.name) / "state.db"
        self.database = Database(self.path)

    def test_connection_pragmas_and_idempotent_startup(self):
        Database(self.path)
        with closing(self.database._connect()) as conn:
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"idx_downloads_status_created_at_id", "idx_downloads_created_at_id"} <= names)

    def test_filters_pagination_and_query_plans(self):
        with closing(self.database._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO downloads (status, file_name, created_at) VALUES (?, ?, ?)",
                [(s, f"{s}-{i}.mp4", f"2026-01-01 00:00:{i:02}")
                 for i, s in enumerate(("completed", "downloading", "queued", "completed", "downloading"))],
            )
        for statuses, expected in ((None, 5), (["downloading"], 2), (["completed"], 2)):
            result = self.database.search_downloads(statuses=statuses, page_size=10)
            self.assertEqual(result["total"], expected)
            self.assertEqual(len(result["items"]), expected)
            if statuses:
                self.assertTrue(all(r["status"] in statuses for r in result["items"]))
            self.assertTrue(all(r["file_exists"] is False for r in result["items"]))
        result = self.database.search_downloads(page=2, page_size=2)
        self.assertEqual([r["id"] for r in result["items"]], [3, 2])
        self.assertEqual(result["total"], 5)
        with closing(self.database._connect()) as conn:
            for where, params, index in (("", (), "idx_downloads_created_at_id"),
                    ("WHERE status IN (?)", ("downloading",), "idx_downloads_status_created_at_id")):
                plan = " ".join(r["detail"] for r in conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT * FROM downloads {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    params + (20, 0),
                ))
                self.assertIn(index, plan)
                self.assertNotIn("TEMP B-TREE", plan)

    def test_index_failure_is_logged_and_startup_continues(self):
        connect = Database._connect
        calls = 0

        def fail_index_connections(database):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise sqlite3.OperationalError("simulated index lock")
            return connect(database)

        with patch.object(Database, "_connect", fail_index_connections), self.assertLogs(
            "backend.app.database", level="WARNING"
        ) as logs:
            Database(self.path)
        self.assertEqual(len(logs.output), 2)
        self.assertTrue(all("Failed to create SQLite index" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
