"""Shared contracts, component locations and worker process helpers.

The integration layer intentionally talks to Story Studio and Animation
Intelligence Lab over JSON files/stdio.  Neither component is imported into the
ShortsFactory virtual environment, so their dependency sets stay untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


class IntegrationError(RuntimeError):
    """A user-facing integration failure."""


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _python_has_pydantic(command: Sequence[str]) -> bool:
    try:
        proc = subprocess.run(
            [*command, "-c", "import pydantic"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _system_python() -> str:
    """Find a Python interpreter that can run Story Studio (needs pydantic)."""
    candidates: list[list[str]] = []
    if os.environ.get("SHORTSFACTORY_STORY_PYTHON"):
        candidates.append([os.environ["SHORTSFACTORY_STORY_PYTHON"]])
    launcher = shutil.which("py")
    if launcher:
        candidates.append([launcher, "-3.12"])
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append([str(Path(local) / "Programs" / "Python" / "Python312" / "python.exe")])
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base:
            candidates.append([str(Path(base) / "Python312" / "python.exe")])
    candidates.append([sys.executable])
    for command in candidates:
        if Path(command[0]).exists() or shutil.which(command[0]):
            if _python_has_pydantic(command):
                return command[0] if len(command) == 1 else " ".join(command)
    raise IntegrationError(
        "Не найден Python с pydantic для Story Studio. "
        "Задайте SHORTSFACTORY_STORY_PYTHON."
    )


@dataclass(frozen=True)
class IntegrationPaths:
    """Filesystem locations of the three independent components."""

    factory_root: Path
    story_studio_root: Path
    animation_lab_root: Path
    story_python: str
    lab_python: str

    @classmethod
    def discover(cls, factory_root: Path | str | None = None) -> "IntegrationPaths":
        factory = Path(factory_root or Path(__file__).resolve().parents[3]).resolve()
        story = _env_path(
            "SHORTSFACTORY_STORY_STUDIO",
            factory.parent / "ShortsFactory_StoryStudio" / "ShortsFactory_StoryStudio",
        )
        lab = _env_path(
            "SHORTSFACTORY_ANIMATION_LAB",
            factory / "Animation_Intelligence_Lab",
        )
        lab_python = os.environ.get("SHORTSFACTORY_LAB_PYTHON") or str(
            lab / ".venv" / "Scripts" / "python.exe"
        )
        if not Path(lab_python).exists():
            lab_python = os.environ.get("SHORTSFACTORY_LAB_PYTHON") or "python"
        return cls(
            factory_root=factory,
            story_studio_root=story,
            animation_lab_root=lab,
            story_python=_system_python(),
            lab_python=lab_python,
        )

    def check(self) -> None:
        for label, path in (
            ("Story Studio", self.story_studio_root),
            ("Animation Intelligence Lab", self.animation_lab_root),
        ):
            if not path.is_dir():
                raise IntegrationError(f"{label} не найден: {path}")
        if not (self.story_studio_root / "src" / "story_studio").is_dir():
            raise IntegrationError(
                f"Story Studio не содержит src/story_studio: {self.story_studio_root}"
            )
        if not (self.animation_lab_root / "src" / "animation_lab").is_dir():
            raise IntegrationError(
                f"Animation Lab не содержит src/animation_lab: {self.animation_lab_root}"
            )
        if not Path(self.lab_python).exists() and shutil.which(self.lab_python) is None:
            raise IntegrationError(
                f"Python Animation Lab не найден: {self.lab_python}. "
                "Задайте SHORTSFACTORY_LAB_PYTHON."
            )


def run_json_worker(
    python: str,
    script: Path,
    payload: dict[str, Any],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run a standalone worker and return its JSON stdout.

    The worker receives the payload on stdin and must write exactly one JSON
    object to stdout.  Diagnostics go to stderr.
    """
    if not script.is_file():
        raise IntegrationError(f"Worker не найден: {script}")
    if " " not in python or Path(python).exists():
        command = [python, str(script)]
    else:
        command = [*python.split(), str(script)]
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        child_env.update(env)
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            env=child_env,
            timeout=timeout,
        )
    except OSError as exc:
        raise IntegrationError(f"Не удалось запустить worker {script.name}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(f"Worker {script.name} превысил таймаут {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise IntegrationError(
            f"Worker {script.name} завершился с кодом {proc.returncode}: {detail[-4000:]}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            f"Worker {script.name} вернул не JSON: {proc.stdout[-2000:]}"
        ) from exc
    if not isinstance(data, dict):
        raise IntegrationError(f"Worker {script.name} вернул не объект JSON")
    return data


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tail(items: Iterable[str], count: int = 20) -> list[str]:
    values = list(items)
    return values[-count:]
