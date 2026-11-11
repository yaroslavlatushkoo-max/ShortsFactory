"""Versioned appearance assets for cartoon characters.

A *look* is a small JSON document describing silhouette, palette, hairstyle,
face and outfit. The rig knows how to draw these styles; the scene only refers
to a look by name (``"look": "milo"``), so an artist can restyle a character
without touching Python code.

Look files live in ``shortsfactory/animation/characters/*.json``. They are
plain, diffable assets: the version and the whole document are copied into the
render report, so an old video can always be traced back to the exact look that
produced it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .art import ArtError, CharacterArt, load_art, validate_art
from .motion import ACTIONS

LOOKS_DIR = Path(__file__).with_name("characters")

#: v1 looks are drawn by the legacy primitive renderer, v2 looks carry external
#: SVG graphics.  Both stay loadable so old scenes keep rendering.
SUPPORTED_LOOK_VERSION = 2
LEGACY_LOOK_VERSION = 1
SUPPORTED_LOOK_VERSIONS = (LEGACY_LOOK_VERSION, SUPPORTED_LOOK_VERSION)

HAIR_STYLES = ("spiky", "bob", "bun", "flat", "curly", "none")
EYE_STYLES = ("round", "almond", "wide")
BROW_STYLES = ("thick", "thin", "soft")
OUTFIT_STYLES = ("tee", "hoodie", "dress", "overalls")
SLEEVES = ("short", "long")

PALETTE_KEYS = (
    "skin", "skin_shadow", "shirt", "shirt_dark", "pants", "shoe",
    "hair", "outline", "eye_white", "pupil", "mouth", "accent",
)

DEFAULT_LOOK: dict[str, Any] = {
    "version": SUPPORTED_LOOK_VERSION,
    "id": "default",
    "name": "Default",
    "rig": "humanoid_v1",
    "proportions": {"build": 1.0, "head": 1.0, "legs": 1.0},
    "palette": {
        "skin": "#f4c9a3",
        "skin_shadow": "#dba97f",
        "shirt": "#2fb6c4",
        "shirt_dark": "#218b96",
        "pants": "#33405c",
        "shoe": "#1f2430",
        "hair": "#5a3b26",
        "outline": "#1b2230",
        "eye_white": "#ffffff",
        "pupil": "#22303f",
        "mouth": "#8a3a3a",
        "accent": "#f2d06b",
    },
    "hair": {"style": "flat", "color": ""},
    "face": {"eye": "round", "brow": "thick", "nose": "button", "mouth": "normal", "freckles": False},
    "outfit": {"style": "tee", "sleeve": "short", "collar": True},
    "accessories": [],
}


class LookError(ValueError):
    """Raised when a character look asset is missing or malformed."""


def _look_path(name: str) -> Path:
    candidate = Path(name)
    if candidate.suffix.lower() == ".json":
        return candidate if candidate.is_absolute() else LOOKS_DIR / candidate
    return LOOKS_DIR / f"{name}.json"


def list_looks() -> list[str]:
    """Names of all look assets shipped with the engine."""
    return sorted(path.stem for path in LOOKS_DIR.glob("*.json"))


def validate_look(document: dict[str, Any], source: str = "") -> list[str]:
    """Structural validation; returns human-readable Russian problems."""
    errors: list[str] = []
    where = f" ({source})" if source else ""
    if not isinstance(document, dict):
        return [f"Описание внешности должно быть объектом JSON{where}."]
    if int(document.get("version", 0) or 0) not in SUPPORTED_LOOK_VERSIONS:
        errors.append(f"Неподдерживаемая версия внешности{where}: {document.get('version')}.")
    version = int(document.get("version", 0) or 0)
    if not str(document.get("id", "")).strip():
        errors.append(f"У внешности{where} не задан id.")
    if str(document.get("rig", "humanoid_v1")) != "humanoid_v1":
        errors.append(f"Внешность{where} ссылается на неизвестный риг «{document.get('rig')}».")

    hair = document.get("hair") or {}
    if not isinstance(hair, dict):
        errors.append(f"Блок hair{where} должен быть объектом.")
    elif str(hair.get("style", "flat")) not in HAIR_STYLES:
        errors.append(f"Неизвестная причёска{where}: «{hair.get('style')}».")

    face = document.get("face") or {}
    if not isinstance(face, dict):
        errors.append(f"Блок face{where} должен быть объектом.")
    else:
        if str(face.get("eye", "round")) not in EYE_STYLES:
            errors.append(f"Неизвестная форма глаз{where}: «{face.get('eye')}».")
        if str(face.get("brow", "thick")) not in BROW_STYLES:
            errors.append(f"Неизвестная форма бровей{where}: «{face.get('brow')}».")

    outfit = document.get("outfit") or {}
    if not isinstance(outfit, dict):
        errors.append(f"Блок outfit{where} должен быть объектом.")
    else:
        if str(outfit.get("style", "tee")) not in OUTFIT_STYLES:
            errors.append(f"Неизвестный стиль одежды{where}: «{outfit.get('style')}».")
        if str(outfit.get("sleeve", "short")) not in SLEEVES:
            errors.append(f"Неизвестная длина рукава{where}: «{outfit.get('sleeve')}».")

    palette = document.get("palette") or {}
    if not isinstance(palette, dict):
        errors.append(f"Палитра{where} должна быть объектом.")
        palette = {}
    else:
        for key, value in palette.items():
            text = str(value)
            if not (len(text) == 7 and text.startswith("#")):
                errors.append(f"Цвет «{key}»{where} должен быть в формате #rrggbb: {value}.")

    proportions = document.get("proportions") or {}
    if not isinstance(proportions, dict):
        errors.append(f"Блок proportions{where} должен быть объектом.")
    else:
        for key in ("build", "head", "legs"):
            if key in proportions and not 0.5 <= float(proportions[key]) <= 2.0:
                errors.append(f"Некорректная пропорция «{key}»{where}: {proportions[key]}.")

    graphics = document.get("graphics")
    if version >= SUPPORTED_LOOK_VERSION and graphics is None:
        errors.append(f"Внешность версии {version}{where} должна содержать блок graphics с ассетами.")
    if graphics is not None:
        resolved = dict(DEFAULT_LOOK["palette"])
        resolved.update({str(k): str(v) for k, v in palette.items()})
        try:
            errors.extend(validate_art(graphics, resolved, source or str(document.get("id", "")),
                                       allowed_actions=tuple(ACTIONS)))
        except ArtError as exc:
            errors.append(str(exc))
    return errors


def load_look(name: str) -> dict[str, Any]:
    """Load a look asset, merge it over the defaults and validate it."""
    path = _look_path(name)
    if not path.exists():
        raise LookError(f"Файл внешности не найден: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookError(f"Не удалось прочитать внешность {path}: {exc}") from exc
    errors = validate_look(data, path.name)
    if errors:
        raise LookError("Внешность некорректна:\n- " + "\n- ".join(errors))

    look = json.loads(json.dumps(DEFAULT_LOOK))
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(look.get(key), dict):
            look[key].update(value)
        else:
            look[key] = value
    look["source"] = path.as_posix()
    return look


def resolve_look(character: Any) -> dict[str, Any]:
    """Look for a scene character: named asset, or a palette-only fallback."""
    name = str(getattr(character, "look", "") or "")
    if name:
        look = load_look(name)
    else:
        look = json.loads(json.dumps(DEFAULT_LOOK))
        look["id"] = str(getattr(character, "id", "character"))
        look["name"] = str(getattr(character, "name", "") or look["id"])
    palette = dict(look.get("palette") or {})
    palette.update(getattr(character, "palette", {}) or {})
    look["palette"] = palette
    return look


def look_exists(name: str) -> bool:
    return _look_path(name).exists()


_ART_CACHE: dict[tuple, CharacterArt] = {}


def look_art(look: dict[str, Any]) -> CharacterArt | None:
    """Compiled graphics for a look, or ``None`` for legacy v1 looks."""
    graphics = look.get("graphics")
    if not graphics:
        return None
    palette = dict(look.get("palette") or {})
    key = (
        str(look.get("source", "")),
        tuple(sorted(palette.items())),
        json.dumps(graphics, sort_keys=True, ensure_ascii=False),
    )
    art = _ART_CACHE.get(key)
    if art is None:
        art = load_art(graphics, palette, str(look.get("id", "")), allowed_actions=tuple(ACTIONS))
        _ART_CACHE[key] = art
    return art

