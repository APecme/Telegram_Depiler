from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config import Settings


class FrontendCacheTests(unittest.TestCase):
    """应用更新后浏览器必须重新校验 index.html，否则界面停留在旧版本。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.static = root / "static"
        (self.static / "assets").mkdir(parents=True)
        (self.static / "images").mkdir(parents=True)
        (self.static / "index.html").write_text("<html><body>app</body></html>", encoding="utf-8")
        (self.static / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
        (self.static / "images" / "logo1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        self.settings = Settings(
            data_dir=root / "data",
            download_dir=root / "downloads",
            static_dir=self.static,
        )
        self.settings.ensure_directories()
        self.main = importlib.import_module("backend.app.main")
        patcher = patch.object(self.main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(self.main.app)

    def test_index_html_is_revalidated_instead_of_cached(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response.headers["cache-control"])

    def test_spa_fallback_is_revalidated_instead_of_cached(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("app", response.text)
        self.assertIn("no-cache", response.headers["cache-control"])

    def test_hashed_assets_are_cached_immutably(self):
        response = self.client.get("/assets/index-abc123.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("immutable", response.headers["cache-control"])

    def test_other_static_files_are_revalidated(self):
        response = self.client.get("/images/logo1.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
