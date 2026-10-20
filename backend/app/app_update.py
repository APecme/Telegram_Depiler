"""Download and verify code-only application packages from GitHub."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import threading
import time
import urllib.request
import json
import zipfile

from . import app_supervisor, update_state
from .config import get_settings
from .update_transport import open_url

API = "https://api.github.com/repos/APecme/Telegram_Depiler"
ACTIVE = {"scheduled", "downloading", "restarting"}
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXTRACTED = 200 * 1024 * 1024
REQUIRED = {"VERSION", "backend/app/main.py", "backend/app/config.py"}
_LOCK = threading.RLock()


class UpdateError(RuntimeError):
    pass


def _json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Telegram-Depiler-updater"})
    with open_url(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def release_target(channel: str) -> dict:
    if channel == "bata":
        releases = _json(f"{API}/releases?per_page=100")
        candidates = [item for item in releases if not item.get("draft") and re.fullmatch(r"bata\.\d{14}", item.get("tag_name", ""))]
        if not candidates:
            raise UpdateError("还没有可下载的体验版应用包")
        release = max(candidates, key=lambda item: item["tag_name"])
    else:
        release = _json(f"{API}/releases/latest")
    label = str(release.get("tag_name", "")).lstrip("vV")
    if release.get("draft") or not re.fullmatch(r"(?:bata\.\d{14}|\d+\.\d+\.\d+)", label):
        raise UpdateError("发布版本标识无效")
    assets = {item["name"]: item for item in release.get("assets", [])}
    package = assets.get("telegram-depiler-app.zip")
    checksum = assets.get("telegram-depiler-app.zip.sha256")
    if not package or not checksum:
        raise UpdateError("该版本没有完整应用包，请先通过 Docker 更新镜像")
    return {"target": label, "package_url": package["browser_download_url"], "checksum_url": checksum["browser_download_url"], "release_url": release.get("html_url", ""), "commit": ""}


def _current() -> str:
    return update_state.current_version_label()


def _channel() -> str:
    return "bata" if update_state.settings()["experience_program"] else "stable"


def _version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value.lstrip("vV"))
    return tuple(map(int, match.groups())) if match else None


def _job() -> dict:
    value = update_state.read("job", {})
    return value if isinstance(value, dict) else {}


def _capability() -> dict:
    if os.environ.get("TELEGRAM_DEPILER_APP_SUPERVISOR") == "1" and Path("/app").is_dir():
        return {"supported": True, "mode": "app"}
    if Path("/.dockerenv").exists():
        return {"supported": False, "reason": "当前镜像没有应用监护进程，请先手动更新一次 Docker 镜像。"}
    return {"supported": False, "reason": "源码部署仅支持检查更新。"}


def _download_file(url: str, destination: Path) -> None:
    if not url.startswith("https://"):
        raise UpdateError("应用包必须通过 HTTPS 下载")
    request = urllib.request.Request(url, headers={"User-Agent": "Telegram-Depiler-updater"})
    with open_url(request, timeout=30) as response, destination.open("wb") as output:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise UpdateError("应用包超过大小限制")
            output.write(chunk)


def extract_package(archive: Path, destination: Path, label: str) -> None:
    seen = set()
    total = 0
    required = REQUIRED | {"backend/app/static/index.html", "requirements.txt"}
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if item.is_dir():
                continue
            path = PurePosixPath(item.filename)
            if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or "\\" in item.filename or ":" in item.filename:
                raise UpdateError("应用包中存在不安全路径")
            name = path.as_posix()
            if not (name in {"VERSION", "requirements.txt"} or path.parts[:2] == ("backend", "app")):
                raise UpdateError("应用包包含非应用文件")
            if name in seen or stat.S_ISLNK(item.external_attr >> 16):
                raise UpdateError("应用包包含重复文件或符号链接")
            if item.file_size > MAX_EXTRACTED - total:
                raise UpdateError("应用包解压大小超过限制")
            content = package.read(item)
            total += len(content)
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            seen.add(name)
    if not required.issubset(seen):
        raise UpdateError("应用包缺少前端或后端必需文件")
    if (destination / "VERSION").read_text(encoding="utf-8").strip() != label:
        raise UpdateError("应用包版本与目标版本不一致")


def _stage(info: dict) -> None:
    label = info.get("target", "")
    if not re.fullmatch(r"(?:bata\.\d{14}|\d+\.\d+\.\d+)", label):
        raise UpdateError("目标版本无效")
    target = app_supervisor.APP_DIR / label
    if (target / ".verified.json").is_file():
        return
    app_supervisor.APP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=app_supervisor.APP_DIR) as temporary:
        root = Path(temporary)
        archive, checksum = root / "app.zip", root / "checksum"
        _download_file(info["checksum_url"], checksum)
        checksum_parts = checksum.read_text(encoding="utf-8").strip().split()
        expected = checksum_parts[0] if checksum_parts else ""
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise UpdateError("更新校验信息无效")
        _download_file(info["package_url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected.lower():
            raise UpdateError("应用包 SHA256 校验失败")
        extracted = root / "extracted"
        extract_package(archive, extracted, label)
        installed = app_supervisor.BASE_ROOT / "requirements.txt"
        if not installed.is_file():
            installed = app_supervisor.BASE_ROOT / "backend" / "requirements.txt"
        if not installed.is_file() or installed.read_text().splitlines() != (extracted / "requirements.txt").read_text().splitlines():
            raise UpdateError("此版本依赖已变化，请通过 Docker 更新镜像")
        (extracted / ".verified.json").write_text(json.dumps({"sha256": expected.lower()}))
        if target.exists():
            target.rename(target.with_name(label + f".incomplete-{int(time.time())}"))
        extracted.rename(target)


def apply(info: dict, record: dict) -> None:
    previous = app_supervisor.read_state()
    switched = False
    try:
        update_state.write("job", dict(record, status="downloading", message="正在下载并校验应用包"))
        _stage(info)
        app_supervisor.save_state({
            "current": info["target"],
            "commit": info.get("commit", ""),
            "previous": previous.get("current"),
            "previous_commit": previous.get("commit", ""),
            "pending": True,
        })
        switched = True
        update_state.write("job", dict(record, status="restarting", message="应用包已就绪，正在重启服务"))
        time.sleep(2)
        os._exit(app_supervisor.RESTART_CODE)
    except Exception as exc:
        if switched:
            app_supervisor.save_state(previous)
        update_state.write("job", dict(record, status="failed", message=f"应用更新失败：{exc}", error=str(exc)))


def check(force: bool = False) -> dict:
    selected = _channel()
    cached = update_state.read("check", {})
    if not force and cached.get("channel") == selected and cached.get("current") == _current() and time.time() - cached.get("checked_epoch", 0) < 60:
        return cached
    result = {"channel": selected, "current": _current(), "checked_at": time.time(), "checked_epoch": time.time(), "available": False, "error": ""}
    try:
        target = release_target(selected)
        if selected == "bata" and not re.fullmatch(r"bata\.\d{14}", target["target"]):
            raise UpdateError("bata 镜像版本标识无效")
        if selected == "stable" and not _version(target["target"]):
            raise UpdateError("正式版镜像版本标识无效")
        result.update(target)
        switching = result["current"].startswith("bata.") != (selected == "bata")
        if switching and selected == "stable":
            result["available"] = True
        elif switching:
            result["available"] = True
        elif selected == "bata":
            result["available"] = target["target"] > result["current"]
        else:
            current = _version(result["current"])
            result["available"] = bool(current and _version(target["target"]) and _version(target["target"]) > current)
        result["switching_channel"] = switching
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, UpdateError) else f"无法访问 GitHub 更新源：{exc}。请检查网络或代理配置。"
    update_state.write("check", result)
    return result


def status() -> dict:
    selected = _channel()
    result = update_state.read("check", {})
    if result.get("channel") != selected or result.get("current") != _current():
        result = {}
    return {"settings": update_state.settings(), "result": result, "job": _job(), "current": _current(), "channel": selected, "capability": _capability()}


def start_apply() -> dict:
    with _LOCK:
        if _job().get("status") in ACTIVE:
            raise UpdateError("已有更新正在执行")
        support = _capability()
        if not support["supported"]:
            raise UpdateError(support["reason"])
        info = check(force=True)
        if info.get("error"):
            raise UpdateError(info["error"])
        if not info.get("available"):
            return {"status": "current", **info}
        record = {"id": os.urandom(16).hex(), "mode": "app", "status": "scheduled", "message": "应用更新已排队", "target": info["target"], "channel": info["channel"], "started_at": time.time()}
        update_state.write("job", record)
        threading.Thread(target=apply, args=(info, record), name="app-updater", daemon=True).start()
        return record


def start_scheduler() -> None:
    interrupted = _job()
    if interrupted.get("status") in ACTIVE:
        installed = interrupted.get("target") == _current()
        update_state.write("job", dict(interrupted, status="updated" if installed else "failed",
            message="应用更新完成" if installed else "上次更新被中断，可重新检查并重试"))

    def worker() -> None:
        while True:
            time.sleep(60)
            try:
                settings = update_state.settings()
                cached = update_state.read("check", {})
                due = time.time() - float(cached.get("checked_epoch", 0) or 0) >= settings["check_interval_hours"] * 3600
                if settings["check_enabled"] and due and _job().get("status") not in ACTIVE:
                    result = check()
                    if settings["auto_update"] and result.get("available") and not result.get("error"):
                        start_apply()
            except Exception:
                pass
    threading.Thread(target=worker, name="update-checker", daemon=True).start()
