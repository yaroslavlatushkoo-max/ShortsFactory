"""E14: регрессия эталонных blocky-сцен.

Эталон хранится в JSON рядом со сценами (``scenes/blocky/regression.json``) и
фиксирует только те числа, которые обязаны быть стабильными: длительность,
состав актёров, число реплик, кадры склеек, количество пропсов.  Любое
непреднамеренное изменение сцены или режиссёра камеры ломает сравнение — это и
есть сигнал перед релизом.

Запуск::

    python -m shortsfactory blocky-regress            # сравнить с эталоном
    python -m shortsfactory blocky-regress --update   # перезаписать эталон
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "GOLDEN_NAME",
    "build_golden",
    "compare_scene",
    "load_golden",
    "run_regression",
    "scene_metrics",
]

GOLDEN_NAME = "regression.json"

#: Поля, которые сравниваются как есть.  Список намеренно короткий: регрессия
#: должна ловить смысловые сдвиги, а не форматирование JSON.
TRACKED = (
    "duration",
    "fps",
    "actors",
    "dialogue",
    "props",
    "shots",
    "cut_frames",
    "auto_act",
    "auto_camera",
)


def scene_metrics(path: str | Path) -> dict[str, Any]:
    """Stable metrics of one scene document (validation included)."""
    from .blocky_scene import BlockyScene

    scene = BlockyScene.load(path)
    scene.validate()
    cuts = sorted(int(item["frame"]) for item in scene.camera_cuts)
    return {
        "duration": round(float(scene.duration), 3),
        "fps": int(scene.fps),
        "actors": sorted(str(actor.get("character")) for actor in scene.actors),
        "dialogue": len(scene.dialogue),
        "props": len(scene.props),
        "shots": len(cuts),
        "cut_frames": cuts,
        "auto_act": bool(scene.auto_act),
        "auto_camera": bool(scene.auto_camera),
    }


def build_golden(root: str | Path, scene_paths: list[str | Path] | None = None) -> dict[str, Any]:
    root = Path(root)
    paths = [Path(item) for item in scene_paths] if scene_paths else sorted(
        path for path in (root / "scenes" / "blocky").glob("*.json")
        if path.name != GOLDEN_NAME
    )
    scenes: dict[str, Any] = {}
    errors: list[str] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if isinstance(raw.get("scenes"), list):
            continue  # это раскадровка (вход blocky-create), а не сцена
        try:
            scenes[path.stem] = scene_metrics(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return {"version": 1, "scenes": scenes, "errors": errors}


def load_golden(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("scenes"), dict):
        raise ValueError("Эталон регрессии без секции scenes")
    return data


def compare_scene(
    name: str, expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    issues: list[str] = []
    for key in TRACKED:
        if key not in expected or key not in actual:
            continue
        if expected[key] != actual[key]:
            issues.append(f"{name}: {key} {expected[key]!r} → {actual[key]!r}")
    return issues


def run_regression(
    root: str | Path,
    *,
    golden: str | Path | None = None,
    update: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    golden_path = Path(golden) if golden else root / "scenes" / "blocky" / GOLDEN_NAME
    built = build_golden(root)
    if update:
        golden_path.write_text(
            json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "status": "UPDATED" if not built["errors"] else "FAILED",
            "golden": str(golden_path),
            "scenes": sorted(built["scenes"]),
            "issues": list(built["errors"]),
        }

    try:
        expected = load_golden(golden_path)
    except (OSError, ValueError) as exc:
        return {
            "status": "FAILED",
            "golden": str(golden_path),
            "issues": [f"нет эталона: {exc}"],
        }

    issues: list[str] = list(built["errors"])
    missing = sorted(set(expected["scenes"]) - set(built["scenes"]))
    added = sorted(set(built["scenes"]) - set(expected["scenes"]))
    issues.extend(f"пропала сцена: {name}" for name in missing)
    issues.extend(f"новая сцена без эталона: {name}" for name in added)
    for name in sorted(set(expected["scenes"]) & set(built["scenes"])):
        issues.extend(compare_scene(name, expected["scenes"][name], built["scenes"][name]))

    return {
        "status": "PASS" if not issues else "FAILED",
        "golden": str(golden_path),
        "checked": len(set(expected["scenes"]) & set(built["scenes"])),
        "scenes": sorted(built["scenes"]),
        "issues": issues,
    }
