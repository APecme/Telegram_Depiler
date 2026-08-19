from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def cover_cache_path(data_dir: Path | str, download_id: int) -> Path:
    return Path(data_dir) / ".telegram_depiler_tmp" / "covers" / f"{int(download_id)}.jpg"


async def generate_cover_cache(data_dir: Path | str, download_id: int, source_path: Path | str) -> str | None:
    """Create a small JPEG cover once, so the message preview never has to decode the source again."""
    source = Path(source_path)
    if not source.is_file():
        return None

    target = cover_cache_path(data_dir, download_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return str(target)

    temporary = target.with_name(f"{target.stem}.tmp.jpg")
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        "thumbnail,scale=480:480:force_original_aspect_ratio=decrease",
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(temporary),
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("ffmpeg is unavailable; cover cache was not generated for download %s", download_id)
        return None
    except OSError as exc:
        logger.warning("Unable to start cover generation for download %s: %s", download_id, exc)
        return None

    if result.returncode != 0 or not temporary.is_file():
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
        logger.warning("Cover generation failed for download %s: %s", download_id, detail)
        temporary.unlink(missing_ok=True)
        return None

    try:
        os.replace(temporary, target)
    except OSError as exc:
        logger.warning("Unable to save cover cache for download %s: %s", download_id, exc)
        temporary.unlink(missing_ok=True)
        return None
    return str(target)


def remove_cover_cache(data_dir: Path | str, download_id: int, stored_path: str | None = None) -> None:
    candidates = [cover_cache_path(data_dir, download_id)]
    if stored_path:
        candidates.append(Path(stored_path))
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Unable to remove cover cache %s: %s", path, exc)
