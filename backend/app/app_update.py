"""Download and verify code-only application packages from GitHub."""
from __future__ import annotations

import hashlib
import os
import platform
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
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _registry_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _registry_target(tag: str) -> dict:
    repository = "apecme/telegram-depiler"
    token = _registry_json("https://auth.docker.io/token?service=registry.docker.io&scope=repository:" + repository + ":pull")["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": ", ".join([
        "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json",
    ])}
    response = urllib.request.urlopen(urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}", headers=headers), timeout=20)
    digest = response.headers.get("Docker-Content-Digest", "")
    manifest = json.loads(response.read().decode("utf-8"))
    if "manifests" in manifest:
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"aarch64", "arm64"} else "amd64"
        matches = [item for item in manifest["manifests"] if item.get("platform", {}).get("os") == "linux" and item.get("platform", {}).get("architecture") == arch]
        if not matches:
            raise UpdateError("该版本没有适合当前 CPU 架构的 Docker 镜像")
        digest = matches[0]["digest"]
        manifest = _registry_json(f"https://registry-1.docker.io/v2/{repository}/manifests/{digest}", headers=headers)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise UpdateError("更新源未返回有效镜像摘要")
    config_id = manifest["config"]["digest"]
    config = _registry_json(f"https://registry-1.docker.io/v2/{repository}/blobs/{config_id}", headers=headers)
    env = dict(value.split("=", 1) for value in config.get("config", {}).get("Env", []) if "=" in value)
    target = (env.get("TELEGRAM_DEPILER_RELEASE_LABEL") or "").lstrip("vV")
    if not target:
        raise UpdateError("更新镜像缺少版本标识")
    return {"target": target, "image": repository + "@" + digest, "tag": tag, "commit": env.get("TELEGRAM_DEPILER_RELEASE_COMMIT", "")}


def _current() -> str:
    return update_state.current_version_label()


def _channel() -> str:
    return "bata" if update_state.settings()["experience_program"] else "stable"


def _version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value.lstrip("vV"))
    return tuple(map(int, match.groups())) if match else None


def _job() -> dict:
    return update_state.read("job", {})


def _capability() -> dict:
    if os.environ.get("TELEGRAM_DEPILER_APP_SUPERVISOR") == "1" and Path("/app").is_dir():
        return {"supported": True, "mode": "app"}
    if Path("/.dockerenv").exists():
        return {"supported": False, "reason": "当前镜像没有应用监护进程，请先手动更新一次 Docker 镜像。"}
    return {"supported": False, "reason": "源码部署仅支持检查更新。"}


def _download_archive(commit: str, destination: Path) -> None:
    request = urllib.request.Request(f"{API}/zipball/{commit}", headers={"User-Agent": "Telegram-Depiler-updater"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise UpdateError("应用包超过大小限制")
            output.write(chunk)


def _allowed(path: PurePosixPath) -> bool:
    return path.as_posix() == "VERSION" or path.parts[:1] == ("backend",) and path.parts[:2] == ("backend", "app")


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _extract_verified(archive: Path, destination: Path, expected: dict[str, str]) -> None:
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            parts = PurePosixPath(item.filename).parts
            if item.is_dir():
                continue
            if len(parts) < 2 or any(part in ("", ".", "..") for part in parts) or "\\" in item.filename or item.filename.startswith("/"):
                raise UpdateError("应用包中存在不安全路径")
            path = PurePosixPath(*parts[1:])
            name = path.as_posix()
            if not _allowed(path):
                continue
            if name in seen or name not in expected or stat.S_ISLNK(item.external_attr >> 16):
                raise UpdateError("应用包文件与 Git 提交不符")
            if item.file_size > MAX_EXTRACTED - total:
                raise UpdateError("应用包解压大小超过限制")
            content = package.read(item)
            total += len(content)
            if _blob_sha(content) != expected[name]:
                raise UpdateError(f"应用包校验失败：{name}")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            seen.add(name)
    if not REQUIRED.issubset(seen):
        raise UpdateError("应用包缺少必需文件")


def _stage(info: dict) -> None:
    label, commit = info.get("target", ""), info.get("commit", "")
    if not re.fullmatch(r"(?:bata\.\d{14}|\d+\.\d+\.\d+)", label) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise UpdateError("目标镜像未提供有效应用版本和提交标识")
    target = app_supervisor.APP_DIR / label
    if target.is_dir():
        return
    app_supervisor.APP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=app_supervisor.APP_DIR) as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "source.zip"
        extracted = temporary_path / "app"
        extracted.mkdir()
        commit_info = _json(f"{API}/git/commits/{commit}")
        if commit_info.get("sha") != commit:
            raise UpdateError("Git 提交标识不匹配")
        tree = _json(f"{API}/git/trees/{commit_info['tree']['sha']}?recursive=1")
        if tree.get("truncated"):
            raise UpdateError("Git 文件清单不完整")
        expected = {
            item["path"]: item["sha"]
            for item in tree.get("tree", [])
            if item.get("type") == "blob" and _allowed(PurePosixPath(item["path"]))
        }
        _download_archive(commit, archive)
        _extract_verified(archive, extracted, expected)
        extracted.rename(target)


def apply(info: dict, record: dict) -> None:
    previous = app_supervisor.read_state()
    switched = False
    try:
        update_state.write("job", dict(record, status="downloading", message="正在下载并校验应用包"))
        _stage(info)
        app_supervisor.save_state({
            "current": info["target"],
            "commit": info["commit"],
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
        target = _registry_target("bata" if selected == "bata" else "latest")
        if selected == "bata" and not re.fullmatch(r"bata\.\d{14}", target["target"]):
            raise UpdateError("bata 镜像版本标识无效")
        if selected == "stable" and not _version(target["target"]):
            raise UpdateError("正式版镜像版本标识无效")
        result.update(target)
        switching = result["current"].startswith("bata.") != (selected == "bata")
        if switching and selected == "stable":
            base = _version(get_settings().version)
            result["available"] = bool(base and _version(target["target"]) and _version(target["target"]) > base)
        elif switching:
            result["available"] = True
        elif selected == "bata":
            result["available"] = target["target"] > result["current"]
        else:
            current = _version(result["current"])
            result["available"] = bool(current and _version(target["target"]) and _version(target["target"]) > current)
        result["switching_channel"] = switching
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, UpdateError) else "无法连接更新源，请检查服务器网络后重试"
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
