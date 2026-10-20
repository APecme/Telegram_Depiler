from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app import app_update, app_supervisor, update_state


class ApplicationUpdateTests(unittest.TestCase):
    def test_package_requires_built_frontend_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "app.zip"
            contents = {"VERSION": b"1.0.61", "backend/app/main.py": b"app=True", "backend/app/config.py": b"", "backend/app/static/index.html": b"<html/>", "requirements.txt": b"fastapi"}
            with zipfile.ZipFile(package, "w") as archive:
                for name, data in contents.items():
                    archive.writestr(name, data)
            app_update.extract_package(package, root / "good", "1.0.61")
            self.assertTrue((root / "good/backend/app/static/index.html").is_file())
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("../escape", "bad")
            with self.assertRaises(app_update.UpdateError):
                app_update.extract_package(package, root / "bad", "1.0.61")

    def test_missing_frontend_is_not_an_installable_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "app.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("VERSION", "1.0.61")
                archive.writestr("backend/app/main.py", "")
                archive.writestr("backend/app/config.py", "")
            with self.assertRaisesRegex(app_update.UpdateError, "文件"):
                app_update.extract_package(package, root / "extracted", "1.0.61")

    def test_beta_channel_can_be_left_without_forcing_it_back_on(self):
        with patch.object(update_state, "read", return_value={"experience_program": False}), patch.object(
            update_state, "current_version_label", return_value="bata.20261020000000",
        ):
            self.assertFalse(update_state.settings()["experience_program"])

    def test_release_discovery_uses_downloadable_package_and_checksum(self):
        release = {"tag_name": "v1.0.61", "draft": False, "prerelease": False, "assets": [
            {"name": "telegram-depiler-app.zip", "browser_download_url": "https://github.com/app.zip"},
            {"name": "telegram-depiler-app.zip.sha256", "browser_download_url": "https://github.com/app.zip.sha256"},
        ]}
        with patch.object(app_update, "_json", return_value=release):
            target = app_update.release_target("stable")
        self.assertEqual(target["target"], "1.0.61")
        self.assertEqual(target["package_url"], "https://github.com/app.zip")

    def test_update_child_starts_outside_old_app_import_directory(self):
        command, cwd, environment = app_supervisor.child_configuration(Path("/app/data/updates/apps/1.0.61"), "1.0.61", "abc")
        self.assertIn("uvicorn", command)
        self.assertEqual(Path(cwd).as_posix(), "/app/data/updates/apps/1.0.61/backend")
        self.assertEqual(Path(environment["TELEGRAM_DEPILER_STATIC_DIR"]).as_posix(), "/app/data/updates/apps/1.0.61/backend/app/static")

    def test_stage_verifies_checksum_and_keeps_current_app_on_failure(self):
        from scripts.build_app_package import build
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "frontend/dist").mkdir(parents=True)
            (root / "frontend/dist/index.html").write_text("<html>new frontend</html>")
            (root / "backend/app").mkdir(parents=True)
            for name in ("main.py", "config.py", "__init__.py"):
                (root / "backend/app" / name).write_text("")
            (root / "backend/requirements.txt").write_text("fastapi==0.111.0\n")
            package = build(root, root / "assets", "1.0.61")
            info = {"target": "1.0.61", "package_url": "https://example/app.zip", "checksum_url": "https://example/checksum"}

            def download(url, destination):
                destination.write_bytes(package.read_bytes() if url.endswith("zip") else package.with_suffix(".zip.sha256").read_bytes())

            with patch.object(app_supervisor, "APP_DIR", root / "installed"), patch.object(app_supervisor, "BASE_ROOT", root), patch.object(app_update, "_download_file", side_effect=download):
                app_update._stage(info)
                installed = root / "installed/1.0.61"
                self.assertEqual((installed / "backend/app/static/index.html").read_text(), "<html>new frontend</html>")
                package.with_suffix(".zip.sha256").write_text("0" * 64)
                with self.assertRaisesRegex(app_update.UpdateError, "SHA256"):
                    app_update._stage(dict(info, target="1.0.62"))
                self.assertTrue((installed / "backend/app/main.py").is_file())
                self.assertFalse((root / "installed/1.0.62").exists())
