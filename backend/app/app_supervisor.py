"""Run the API as a child process so verified code updates can restart it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

DATA_DIR = Path(os.environ.get("TELEGRAM_DEPILER_DATA_DIR", "/app/data"))
BASE_ROOT = Path("/app")
APP_DIR = DATA_DIR / "updates" / "apps"
STATE_FILE = DATA_DIR / "updates" / "app-current.json"
JOB_FILE = DATA_DIR / "updates" / "job.json"
RESTART_CODE = 75
_child: subprocess.Popen | None = None


def _read(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_state() -> dict:
    return _read(STATE_FILE, {})


def save_state(value: dict) -> None:
    _write(STATE_FILE, value)


def root_for(label: str | None) -> Path:
    if not label:
        return BASE_ROOT
    if not (APP_DIR / label / "backend" / "app" / "main.py").is_file():
        raise ValueError("更新目录不完整")
    return APP_DIR / label


def set_job(status: str, message: str) -> None:
    job = _read(JOB_FILE, {})
    if job.get("mode") == "app":
        job.update(status=status, message=message)
        _write(JOB_FILE, job)


def _health_ready(child: subprocess.Popen, expected: str) -> bool:
    url = "http://127.0.0.1:8000/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(45):
        if child.poll() is not None:
            return False
        try:
            with opener.open(url, timeout=2) as response:
                payload = json.load(response)
                if response.status == 200 and str(payload.get("version", "")).lstrip("vV") == expected:
                    time.sleep(2)
                    return child.poll() is None
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def _terminate(signum, frame) -> None:
    if _child is not None and _child.poll() is None:
        _child.terminate()
        try:
            _child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _child.kill()
    raise SystemExit(0)


def run() -> int:
    global _child
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    while True:
        state = read_state()
        label = state.get("current")
        try:
            root = root_for(label)
        except (ValueError, OSError) as exc:
            state.update(current=None, previous=None, pending=False)
            save_state(state)
            set_job("rolled_back", f"应用包不可用，已恢复镜像内版本：{exc}")
            label, root = None, BASE_ROOT

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "backend") + os.pathsep + environment.get("PYTHONPATH", "")
        environment["TELEGRAM_DEPILER_APP_SUPERVISOR"] = "1"
        expected = (label or environment.get("TELEGRAM_DEPILER_RELEASE_LABEL", "")).lstrip("vV")
        if not expected:
            try:
                expected = (BASE_ROOT / "VERSION").read_text(encoding="utf-8").strip().lstrip("vV")
            except OSError:
                expected = ""
        if label:
            environment["TELEGRAM_DEPILER_RELEASE_LABEL"] = label
            environment["TELEGRAM_DEPILER_RELEASE_COMMIT"] = str(state.get("commit", ""))
        _child = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=str(BASE_ROOT),
            env=environment,
        )
        if not _health_ready(_child, expected):
            if _child.poll() is None:
                _child.terminate()
                try:
                    _child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    _child.kill()
                    _child.wait()
            if label:
                state.update(
                    current=state.get("previous"),
                    commit=state.get("previous_commit", ""),
                    previous=None,
                    pending=False,
                )
                save_state(state)
                set_job("rolled_back", "新版本未能启动，已恢复旧版本")
                continue
            return 1
        if state.get("pending"):
            state["pending"] = False
            save_state(state)
            set_job("updated", "应用更新完成")
        if _child.wait() != RESTART_CODE:
            return 0


if __name__ == "__main__":
    raise SystemExit(run())
