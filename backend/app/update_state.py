"""Small persistent store for application update settings and jobs."""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from .config import get_settings

_LOCK = RLock()


def _folder() -> Path:
    path = get_settings().data_dir / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(name: str, default: Any) -> Any:
    with _LOCK:
        try:
            return json.loads((_folder() / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default


def write(name: str, value: Any) -> None:
    with _LOCK:
        path = _folder() / f"{name}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def settings() -> dict[str, Any]:
    saved = read("settings", {})
    if not isinstance(saved, dict):
        saved = {}
    try:
        interval = int(saved.get("check_interval_hours", 24) or 24)
    except (TypeError, ValueError):
        interval = 24
    current = {
        "check_enabled": bool(saved.get("check_enabled", True)),
        "auto_update": bool(saved.get("auto_update", False)),
        "experience_program": bool(saved.get("experience_program", False)),
        "check_interval_hours": max(1, min(168, interval)),
    }
    label = current_version_label()
    if label.startswith("bata."):
        current["experience_program"] = True
    return current


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = settings()
    for key in ("check_enabled", "auto_update", "experience_program"):
        if key in values:
            current[key] = bool(values[key])
    if "check_interval_hours" in values:
        current["check_interval_hours"] = max(1, min(168, int(values["check_interval_hours"])))
    if current["auto_update"]:
        current["check_enabled"] = True
    if current_version_label().startswith("bata."):
        current["experience_program"] = True
    write("settings", current)
    return current


def current_version_label() -> str:
    import os

    return (os.environ.get("TELEGRAM_DEPILER_RELEASE_LABEL") or get_settings().version or "dev").lstrip("vV")
