"""Build a complete application update asset after npm run build."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile


def build(root: Path, output: Path, label: str) -> Path:
    if not (root / "frontend/dist/index.html").is_file():
        raise RuntimeError("Build the frontend first")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "telegram-depiler-app.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VERSION", label + "\n")
        archive.write(root / "backend/requirements.txt", "requirements.txt")
        for file in sorted((root / "backend/app").rglob("*.py")):
            archive.write(file, file.relative_to(root).as_posix())
        for file in sorted((root / "frontend/dist").rglob("*")):
            if file.is_file():
                archive.write(file, "backend/app/static/" + file.relative_to(root / "frontend/dist").as_posix())
    target.with_suffix(".zip.sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + target.name + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", default="release-assets")
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], Path(args.output), args.label)
