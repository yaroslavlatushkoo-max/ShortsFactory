"""Declarative vector art assets for v3 characters.

A character *look* may embed a ``graphics`` block (see ``characters/*.json``).
The block describes every body part and face feature as an external SVG asset
with a size (in fractions of the character height) and an anchor point.  The
renderer composes those sprites with the kinematic rig from :mod:`rig`.

The format is deliberately small and explicit:

``parts``
    ``head``, ``hair_back``, ``hair_front``, ``torso``, ``pelvis``,
    ``upper_arm``, ``forearm``, ``hand``, ``thigh``, ``shin``, ``shoe`` and the
    optional ``skirt``.  Each entry is ``{asset, size, anchor, rest?}``.
``face``
    ``eyes``, ``brows``, ``nose``, ``mouth`` and the optional ``glasses`` /
    ``extras``.  Each entry is ``{asset, size, offset}`` where ``offset`` is
    measured from the head centre in character-height units (``+y`` down).
``expressions``
    Named combinations of SVG groups: ``{eyes, brows, mouth, blush?, extras?}``.
``layers``
    Optional override of the back-to-front body layer order.
``angles``
    Supported view angles.  v3 ships the ``front`` and ``three_quarter``
    variants; the latter flips one shoulder forward.
``outfits``
    Named overrides of part assets (for example a long-sleeve hoodie).
``actions``
    Actions the art was authored for.  Validated against the motion library.
``rig_limits``
    Declarative limits used by diagnostics (degrees).

Missing or broken assets are collected and reported with the character id and
file name instead of silently falling back to primitives.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

from .svg import SvgError, list_groups, load_svg, render_svg

ART_VERSION = 1
ART_DIR = Path(__file__).with_name("art")
PARTS_DIR = ART_DIR / "parts"

PART_KEYS = (
    "head",
    "hair_back",
    "hair_front",
    "torso",
    "pelvis",
    "upper_arm",
    "forearm",
    "hand",
    "thigh",
    "shin",
    "shoe",
)
OPTIONAL_PART_KEYS = ("skirt",)
FACE_KEYS = ("eyes", "brows", "nose", "mouth")
OPTIONAL_FACE_KEYS = ("glasses", "extras")
EXPRESSION_KEYS = (
    "neutral",
    "joy",
    "surprise",
    "confused",
    "annoyed",
    "sad",
    "laugh",
    "cry",
    "determined",
    "thinking",
)
LAYER_KEYS = (
    "far_leg",
    "far_arm",
    "pelvis",
    "skirt",
    "torso",
    "head",
    "near_leg",
    "near_arm",
)
ANGLES = ("front", "three_quarter")
_TOKEN = re.compile(r"\{\{([a-z0-9_]+)\}\}")

#: Sprite cache.  Keys include the resolved palette, so two characters that
#: share an asset but not colours never poison each other's cache.
_SPRITES: dict[tuple, Image.Image] = {}
_SPRITE_LIMIT = 640


class ArtError(ValueError):
    """Raised when a character graphics manifest is missing or malformed."""


def _fail(errors: list[str], message: str) -> None:
    errors.append(message)


def asset_path(name: str) -> Path:
    """Resolve an asset name inside the art directory (no path escapes)."""
    candidate = (PARTS_DIR / name).resolve()
    try:
        candidate.relative_to(PARTS_DIR.resolve())
    except ValueError as exc:  # pragma: no cover - defensive
        raise ArtError(f"Ассет «{name}» находится вне каталога art/parts.") from exc
    return candidate


def _validate_spec(
    spec: Any,
    label: str,
    errors: list[str],
    palette: dict[str, str],
    *,
    with_offset: bool,
    with_rest: bool,
) -> None:
    if not isinstance(spec, dict):
        _fail(errors, f"{label}: ожидается объект с полями asset/size.")
        return
    asset = spec.get("asset")
    if not isinstance(asset, str) or not asset.strip():
        _fail(errors, f"{label}: не задан файл ассета (asset).")
        return
    if not asset.lower().endswith(".svg"):
        _fail(errors, f"{label}: поддерживаются только SVG-ассеты, получено «{asset}».")
        return
    path = asset_path(asset)
    if not path.is_file():
        _fail(errors, f"{label}: файл ассета «{asset}» не найден.")
        return
    size = spec.get("size")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        _fail(errors, f"{label}: size должен быть парой чисел [ширина, высота].")
        return
    for value in size:
        if not isinstance(value, (int, float)) or not 0.005 <= float(value) <= 1.5:
            _fail(errors, f"{label}: size должен быть в диапазоне 0.005..1.5, получено {value!r}.")
            return
    anchor = spec.get("anchor")
    if anchor is None and with_offset:
        anchor = [0.5, 0.5]
    if not isinstance(anchor, (list, tuple)) or len(anchor) != 2:
        _fail(errors, f"{label}: anchor должен быть парой чисел [x, y].")
        return
    for value in anchor:
        if not isinstance(value, (int, float)) or not -0.5 <= float(value) <= 1.5:
            _fail(errors, f"{label}: anchor должен быть в диапазоне -0.5..1.5, получено {value!r}.")
            return
    if with_offset:
        offset = spec.get("offset", [0.0, 0.0])
        if not isinstance(offset, (list, tuple)) or len(offset) != 2:
            _fail(errors, f"{label}: offset должен быть парой чисел [x, y].")
        elif not all(isinstance(v, (int, float)) and -0.6 <= float(v) <= 0.6 for v in offset):
            _fail(errors, f"{label}: offset выходит за пределы -0.6..0.6.")
    if with_rest and "rest" in spec:
        rest = spec.get("rest")
        if not isinstance(rest, (int, float)) or not -6.3 <= float(rest) <= 6.3:
            _fail(errors, f"{label}: rest должен быть числом в радианах.")
    try:
        text = load_svg(path)
    except OSError as exc:
        _fail(errors, f"{label}: не удалось прочитать «{asset}» ({exc}).")
        return
    missing = sorted({token for token in _TOKEN.findall(text) if token not in palette})
    if missing:
        _fail(
            errors,
            f"{label}: в палитре нет цветов {', '.join(missing)} (ассет «{asset}»).",
        )


def validate_art(
    graphics: Any,
    palette: dict[str, str],
    source: str = "",
    *,
    allowed_actions: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Validate a graphics manifest; return a list of Russian diagnostics."""
    where = f" ({source})" if source else ""
    errors: list[str] = []
    if not isinstance(graphics, dict):
        return [f"Блок graphics{where} должен быть объектом."]
    if graphics.get("version") != ART_VERSION:
        _fail(errors, f"graphics.version{where} должен быть равен {ART_VERSION}.")
    if not isinstance(graphics.get("style"), str) or not graphics["style"].strip():
        _fail(errors, f"graphics.style{where} должен быть непустой строкой.")

    parts = graphics.get("parts")
    if not isinstance(parts, dict):
        _fail(errors, f"graphics.parts{where} должен быть объектом.")
        parts = {}
    for key in PART_KEYS:
        if key not in parts:
            _fail(errors, f"graphics.parts{where}: отсутствует обязательная часть «{key}».")
            continue
        _validate_spec(parts[key], f"graphics.parts.{key}{where}", errors, palette,
                       with_offset=False, with_rest=True)
    for key in OPTIONAL_PART_KEYS:
        if key in parts:
            _validate_spec(parts[key], f"graphics.parts.{key}{where}", errors, palette,
                           with_offset=False, with_rest=True)
    for key in parts:
        if key not in PART_KEYS and key not in OPTIONAL_PART_KEYS:
            _fail(errors, f"graphics.parts{where}: неизвестная часть «{key}».")

    face = graphics.get("face")
    if not isinstance(face, dict):
        _fail(errors, f"graphics.face{where} должен быть объектом.")
        face = {}
    for key in FACE_KEYS:
        if key not in face:
            _fail(errors, f"graphics.face{where}: отсутствует обязательный элемент «{key}».")
            continue
        _validate_spec(face[key], f"graphics.face.{key}{where}", errors, palette,
                       with_offset=True, with_rest=False)
    for key in OPTIONAL_FACE_KEYS:
        if key in face:
            _validate_spec(face[key], f"graphics.face.{key}{where}", errors, palette,
                           with_offset=True, with_rest=False)
    for key in face:
        if key not in FACE_KEYS and key not in OPTIONAL_FACE_KEYS:
            _fail(errors, f"graphics.face{where}: неизвестный элемент «{key}».")

    expressions = graphics.get("expressions")
    if not isinstance(expressions, dict) or not expressions:
        _fail(errors, f"graphics.expressions{where} должен быть непустым объектом.")
        expressions = {}
    if "neutral" not in expressions:
        _fail(errors, f"graphics.expressions{where}: обязательна эмоция «neutral».")
    eye_groups = brow_groups = mouth_groups = set()
    for key, spec in face.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("asset"), str):
            continue
        path = asset_path(spec["asset"])
        if not path.is_file():
            continue
        groups = set(list_groups(load_svg(path)))
        if key == "eyes":
            eye_groups = groups
        elif key == "brows":
            brow_groups = groups
        elif key == "mouth":
            mouth_groups = groups
    for name, spec in expressions.items():
        label = f"graphics.expressions.{name}{where}"
        if name not in EXPRESSION_KEYS:
            _fail(errors, f"{label}: неизвестная эмоция.")
            continue
        if not isinstance(spec, dict):
            _fail(errors, f"{label}: ожидается объект eyes/brows/mouth.")
            continue
        for field, allowed in (("eyes", eye_groups), ("brows", brow_groups), ("mouth", mouth_groups)):
            group = spec.get(field)
            if not isinstance(group, str) or not group:
                _fail(errors, f"{label}: не задана группа {field}.")
            elif allowed and group not in allowed:
                _fail(errors, f"{label}: в ассете нет группы «{group}» для {field}.")
        if "blush" in spec and not isinstance(spec["blush"], bool):
            _fail(errors, f"{label}: blush должен быть true/false.")
        if "extras" in spec and not isinstance(spec["extras"], list):
            _fail(errors, f"{label}: extras должен быть списком групп.")
    for required in ("joy", "surprise", "confused", "annoyed", "sad"):
        if expressions and required not in expressions:
            _fail(errors, f"graphics.expressions{where}: отсутствует обязательная эмоция «{required}».")

    layers = graphics.get("layers")
    if layers is not None:
        if not isinstance(layers, list) or not layers:
            _fail(errors, f"graphics.layers{where} должен быть непустым списком.")
        else:
            for layer in layers:
                if layer not in LAYER_KEYS:
                    _fail(errors, f"graphics.layers{where}: неизвестный слой «{layer}».")

    angles = graphics.get("angles")
    if angles is not None:
        if not isinstance(angles, list) or not angles:
            _fail(errors, f"graphics.angles{where} должен быть непустым списком.")
        else:
            for angle in angles:
                if angle not in ANGLES:
                    _fail(errors, f"graphics.angles{where}: неизвестный ракурс «{angle}».")
            if "front" not in angles:
                _fail(errors, f"graphics.angles{where}: обязателен ракурс «front».")

    outfits = graphics.get("outfits")
    if outfits is not None:
        if not isinstance(outfits, dict):
            _fail(errors, f"graphics.outfits{where} должен быть объектом.")
        else:
            for name, override in outfits.items():
                if not isinstance(override, dict):
                    _fail(errors, f"graphics.outfits.{name}{where}: ожидается объект частей.")
                    continue
                for key in override:
                    if key not in PART_KEYS and key not in OPTIONAL_PART_KEYS:
                        _fail(errors, f"graphics.outfits.{name}{where}: неизвестная часть «{key}».")

    actions = graphics.get("actions")
    if actions is not None:
        if not isinstance(actions, list) or not actions:
            _fail(errors, f"graphics.actions{where} должен быть непустым списком.")
        elif allowed_actions is not None:
            known = set(allowed_actions)
            for action in actions:
                if action not in known:
                    _fail(errors, f"graphics.actions{where}: неизвестное действие «{action}».")

    limits = graphics.get("rig_limits")
    if limits is not None:
        if not isinstance(limits, dict):
            _fail(errors, f"graphics.rig_limits{where} должен быть объектом.")
        else:
            for key, value in limits.items():
                if not isinstance(value, (int, float)):
                    _fail(errors, f"graphics.rig_limits.{key}{where}: ожидается число.")

    return errors


class CharacterArt:
    """Resolved graphics manifest for one character look."""

    def __init__(self, graphics: dict[str, Any], palette: dict[str, str], source: str = "") -> None:
        self.graphics = graphics
        self.palette = dict(palette)
        self.source = source
        self.parts: dict[str, dict[str, Any]] = dict(graphics.get("parts") or {})
        self.face: dict[str, dict[str, Any]] = dict(graphics.get("face") or {})
        self.expressions: dict[str, dict[str, Any]] = dict(graphics.get("expressions") or {})
        self.layers: tuple[str, ...] = tuple(graphics.get("layers") or LAYER_KEYS)
        self.angles: tuple[str, ...] = tuple(graphics.get("angles") or ANGLES)
        self.outfits: dict[str, dict[str, Any]] = dict(graphics.get("outfits") or {})
        self.actions: tuple[str, ...] = tuple(graphics.get("actions") or ())
        self.rig_limits: dict[str, float] = dict(graphics.get("rig_limits") or {})
        self.style = str(graphics.get("style") or "cel_v3")

    # -- specs ---------------------------------------------------------
    def part(self, key: str, outfit: str | None = None) -> dict[str, Any]:
        spec = dict(self.parts.get(key) or {})
        if outfit and outfit in self.outfits:
            override = self.outfits[outfit].get(key)
            if isinstance(override, dict):
                spec.update(override)
        if not spec:
            raise ArtError(f"У персонажа «{self.source}» нет части «{key}».")
        return spec

    def has_part(self, key: str, outfit: str | None = None) -> bool:
        if outfit and outfit in self.outfits and key in self.outfits[outfit]:
            return True
        return key in self.parts

    def face_part(self, key: str) -> dict[str, Any]:
        spec = self.face.get(key)
        if not spec:
            raise ArtError(f"У персонажа «{self.source}» нет элемента лица «{key}».")
        return dict(spec)

    def expression(self, name: str) -> dict[str, Any]:
        spec = self.expressions.get(name) or self.expressions.get("neutral") or {}
        return dict(spec)

    def describe(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "source": self.source,
            "parts": sorted(self.parts),
            "face": sorted(self.face),
            "expressions": sorted(self.expressions),
            "layers": list(self.layers),
            "angles": list(self.angles),
            "outfits": sorted(self.outfits),
            "actions": list(self.actions),
            "rig_limits": dict(self.rig_limits),
            "assets": sorted(
                {spec.get("asset") for spec in list(self.parts.values()) + list(self.face.values())
                 if isinstance(spec, dict) and spec.get("asset")}
            ),
        }

    # -- rendering -----------------------------------------------------
    def sprite(
        self,
        key: str,
        height_px: float,
        *,
        group: str | None = None,
        offsets: dict[str, tuple[float, float]] | None = None,
        face: bool = False,
        outfit: str | None = None,
    ) -> Image.Image:
        spec = self.face_part(key) if face else self.part(key, outfit)
        return _sprite(
            str(spec["asset"]),
            float(spec["size"][0]) * height_px,
            float(spec["size"][1]) * height_px,
            self.palette,
            group,
            offsets,
        )


def load_art(
    graphics: Any,
    palette: dict[str, str],
    source: str = "",
    *,
    allowed_actions: tuple[str, ...] | list[str] | None = None,
) -> CharacterArt:
    errors = validate_art(graphics, palette, source, allowed_actions=allowed_actions)
    if errors:
        raise ArtError("\n".join(errors))
    return CharacterArt(graphics, palette, source)


def _sprite(
    asset: str,
    width: float,
    height: float,
    palette: dict[str, str],
    group: str | None,
    offsets: dict[str, tuple[float, float]] | None,
) -> Image.Image:
    w = max(2, int(round(width)))
    h = max(2, int(round(height)))
    palette_key = tuple(sorted(palette.items()))
    offsets_key = tuple(sorted((offsets or {}).items()))
    key = (asset, w, h, palette_key, group, offsets_key)
    cached = _SPRITES.get(key)
    if cached is not None:
        return cached
    path = asset_path(asset)
    text = load_svg(path)
    try:
        image = render_svg(
            text,
            (w, h),
            palette=palette,
            group=group,
            offsets=dict(offsets_key),
            supersample=3,
        )
    except SvgError as exc:
        raise ArtError(f"Не удалось отрисовать ассет «{asset}»: {exc}") from exc
    if len(_SPRITES) >= _SPRITE_LIMIT:
        _SPRITES.clear()
    _SPRITES[key] = image
    return image


def clear_cache() -> None:
    _SPRITES.clear()


def describe_art() -> dict[str, Any]:
    return {
        "format": "svg-subset",
        "version": ART_VERSION,
        "parts_dir": str(PARTS_DIR),
        "part_keys": list(PART_KEYS),
        "optional_parts": list(OPTIONAL_PART_KEYS),
        "face_keys": list(FACE_KEYS),
        "expressions": list(EXPRESSION_KEYS),
        "angles": list(ANGLES),
    }
