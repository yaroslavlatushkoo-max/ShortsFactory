# blend3d/host/paths.py — Blender executable discovery and working directories.
import os
import shutil
from pathlib import Path

DEFAULT_WORK_ROOT = Path(r"D:\Видео_Work\minecraft")

_CANDIDATES = [
    os.environ.get("BLENDER_EXE", ""),
    r"D:\Blender\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]

def find_blender() -> Path:
    """Locate the Blender executable. Env var BLENDER_EXE wins."""
    for cand in _CANDIDATES:
        if cand and Path(cand).is_file():
            return Path(cand)
    which = shutil.which("blender")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "Blender executable not found; set BLENDER_EXE or install Blender")

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def job_dirs(work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    """Standard layout for one film job."""
    return {
        "scripts": ensure_dir(work_root / "scripts"),
        "frames": ensure_dir(work_root / "frames"),
        "audio": ensure_dir(work_root / "audio"),
        "renders": ensure_dir(work_root / "renders"),
        "checks": ensure_dir(work_root / "checks"),
    }
