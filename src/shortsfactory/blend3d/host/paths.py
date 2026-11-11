"""Resolve external tools and work directories (no machine-specific paths)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def work_root() -> Path:
    env = os.environ.get("SHORTSFACTORY_WORK_ROOT")
    if env:
        return Path(env)
    return Path.cwd() / "workspace"


def blender_exe() -> str | None:
    # единое имя со всеми остальными модулями + legacy-алиас
    for var in ("BLENDER_EXECUTABLE", "BLENDER_EXE"):
        value = os.environ.get(var, "").strip()
        if value and Path(value).exists():
            return value
    found = shutil.which("blender")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        for hit in sorted(Path(base).glob("Blender Foundation/Blender*/blender.exe"),
                          reverse=True):
            return str(hit)
    return None


def find_blender() -> Path:
    """Locate the Blender executable (legacy API)."""
    exe = blender_exe()
    if exe:
        return Path(exe)
    raise FileNotFoundError(
        "Blender executable not found; set BLENDER_EXECUTABLE or install Blender")


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_dirs(work_root_path: Path | None = None) -> dict:
    """Standard layout for one film job."""
    root = work_root_path or work_root()
    return {
        "scripts": ensure_dir(root / "scripts"),
        "frames": ensure_dir(root / "frames"),
        "audio": ensure_dir(root / "audio"),
        "renders": ensure_dir(root / "renders"),
        "checks": ensure_dir(root / "checks"),
    }
