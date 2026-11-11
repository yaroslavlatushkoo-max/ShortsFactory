"""Единый проект-файл/manifest (этап E2).

Manifest описывает проект: история, настройки, артефакты, статус —
в одном JSON, который можно передавать между GUI/CLI/движком.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

DEFAULT_SETTINGS: dict[str, Any] = {
    "lang": "ru",
    "scenes": 8,
    "target_s": 600.0,
    "engine": "blocky",
    "fps": 12,
    "width": 480,
    "height": 720,
    "samples": 8,
    "closeup": False,
    "music": False,
    "subtitles": False,
}


def create_manifest(*, story_path: str | Path | None = None,
                    settings: dict[str, Any] | None = None,
                    artifacts_path: str | Path | None = None) -> dict[str, Any]:
    """Собирает манифест проекта."""
    merged = dict(DEFAULT_SETTINGS)
    if settings:
        merged.update(settings)
    manifest: dict[str, Any] = {
        "format": "shortsfactory-project",
        "version": MANIFEST_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "story": str(Path(story_path)) if story_path else None,
        "settings": merged,
        "artifacts": {
            "video": str(Path(artifacts_path)) if artifacts_path else None,
            "clips": [],
        },
        "status": "draft",
    }
    return manifest


def save_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_manifest(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("format") != "shortsfactory-project":
        raise ValueError("Не похоже на манифест ShortsFactory")
    return raw


def summary_manifest(manifest: dict[str, Any]) -> str:
    """Короткая человекочитаемая сводка."""
    s = manifest.get("settings", {})
    lines = [
        f"Проект: {manifest.get('story') or '<нет истории>'}",
        f"Статус: {manifest.get('status', 'draft')}",
        f"Язык: {s.get('lang')}; сцен: {s.get('scenes')}; "
        f"бюджет: {s.get('target_s')}с",
        f"Рендер: {s.get('width')}x{s.get('height')} @ {s.get('fps')} fps, "
        f"{s.get('samples')} samples, {s.get('engine')}",
        f"Видео: {manifest.get('artifacts', {}).get('video') or '-'}",
    ]
    return "\n".join(lines)


__all__ = [
    "MANIFEST_VERSION",
    "DEFAULT_SETTINGS",
    "create_manifest",
    "save_manifest",
    "load_manifest",
    "summary_manifest",
]
