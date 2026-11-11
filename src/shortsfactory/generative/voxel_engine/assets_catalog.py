"""Voxel asset catalog — load/introspect/validate the curated assets.

The catalog lives in ``assets/voxel/catalog.json`` and references clip metadata
files under ``assets/voxel/animations/``.  It lets the asset pipeline produce an
``AnimationPlan`` that references known ids (character, environment, props,
clips, shots) instead of arbitrary per-run geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Project repo root (assets dir sits next to src/).
REPO_ROOT = Path(__file__).resolve().parents[4]
ASSETS_DIR = REPO_ROOT / "assets" / "voxel"
CATALOG_PATH = ASSETS_DIR / "catalog.json"


class AssetError(ValueError):
    """Raised when an asset reference is missing/invalid."""


@dataclass
class AssetCatalog:
    data: dict[str, Any] = field(default_factory=dict)
    root: Path = ASSETS_DIR

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "AssetCatalog":
        p = Path(path) if path else CATALOG_PATH
        if not p.exists():
            raise AssetError(f"Каталог ассетов не найден: {p}")
        return cls(data=json.loads(p.read_text(encoding="utf-8")), root=p.parent)

    # ------------------------------------------------------------- queries
    def ids(self, kind: str) -> list[str]:
        return sorted(self.data.get(kind, {}).keys())

    def get(self, kind: str, asset_id: str) -> dict:
        entry = self.data.get(kind, {}).get(asset_id)
        if not entry:
            raise AssetError(f"Ассет {kind}/{asset_id} отсутствует в каталоге.")
        return entry

    def clip_meta(self, clip_id: str) -> dict:
        entry = self.get("clips", clip_id)
        rel = entry.get("file")
        if not rel:
            return entry
        f = REPO_ROOT / rel
        if not f.exists():
            raise AssetError(f"Файл клипа не найден: {f}")
        meta = json.loads(f.read_text(encoding="utf-8"))
        meta["catalog"] = entry
        return meta

    # ------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        errors: list[str] = []
        for kind in ("characters", "environments", "props", "clips", "shots"):
            if kind not in self.data:
                errors.append(f"В каталоге нет секции «{kind}».")
        for clip_id in self.ids("clips"):
            try:
                self.clip_meta(clip_id)
            except AssetError as exc:
                errors.append(str(exc))
        return errors

    def validate_refs(self, production: dict) -> list[str]:
        """Validate a production spec {character, environment, props, clips, shots}."""
        errors: list[str] = []
        if not isinstance(production, dict):
            return ["Производственный spec не является словарём."]
        ch = production.get("character")
        if ch and ch not in self.ids("characters"):
            errors.append(f"Неизвестный персонаж: {ch}.")
        env = production.get("environment")
        if env and env not in self.ids("environments"):
            errors.append(f"Неизвестное окружение: {env}.")
        for pid in production.get("props", []) or []:
            if pid not in self.ids("props"):
                errors.append(f"Неизвестный предмет: {pid}.")
        for clip_id in production.get("clips", []) or []:
            if clip_id not in self.ids("clips"):
                errors.append(f"Неизвестный клип: {clip_id}.")
        for shot in production.get("shots", []) or []:
            if shot not in self.ids("shots"):
                errors.append(f"Неизвестный план камеры: {shot}.")
        return errors
