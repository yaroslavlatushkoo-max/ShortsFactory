"""Story Film — вариант 2: «отрисовывание истории».

Модуль превращает сториборд в видео: для каждой сцены рисуется
статичная 2D-иллюстрация (PIL, пиксельная книга), поверх неё делается
Ken Burns-эффект (медленный зум/пан через ffmpeg zoompan), накладывается
озвучка TTS (Piper) + мягкий эмбиент + субтитры. Всё склеивается в MP4.

Не использует Blender и не трогает первый вариант (анимация).
"""

from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..blend3d.host.skins import SKIN64_REGIONS, region_px

# ---------------------------------------------------------------------------
# константы
# ---------------------------------------------------------------------------

DEFAULT_FPS = 30
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_SAMPLE_RATE = 22050
DEFAULT_VOICE_RU = "ru_RU-dmitri-medium.onnx"
DEFAULT_VOICE_RU_F = "ru_RU-irina-medium.onnx"
DEFAULT_VOICE_EN = "en_US-ryan-high.onnx"
DEFAULT_VOICE_EN_F = "en_US-libritts_r-medium.onnx"
DEFAULT_PIPER_DIR = Path(__file__).resolve().parents[3] / "models" / "piper"

# палитры фонов по локациям (небо, земля, декор1, декор2, акцент)
LOCATION_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "forest": ((118, 168, 224), (76, 138, 92), (58, 104, 70), (110, 86, 58), (255, 240, 190)),
    "meadow": ((140, 190, 236), (126, 180, 96), (92, 150, 70), (130, 110, 70), (255, 238, 170)),
    "cave": ((46, 44, 58), (64, 60, 74), (88, 82, 96), (52, 48, 62), (120, 140, 190)),
    "village": ((132, 178, 224), (110, 152, 96), (140, 110, 78), (120, 96, 66), (255, 232, 170)),
    "desert": ((220, 190, 130), (204, 168, 104), (180, 142, 86), (150, 112, 66), (255, 236, 180)),
    "snow": ((186, 208, 232), (214, 224, 236), (180, 198, 216), (150, 172, 196), (235, 244, 252)),
    "night": ((36, 42, 68), (58, 66, 92), (78, 84, 110), (44, 50, 76), (255, 224, 150)),
    "default": ((130, 176, 220), (110, 150, 100), (96, 130, 86), (120, 100, 72), (255, 236, 180)),
}

SKIN_SOURCE = {
    "head": "front",
    "body": "front",
    "arm_r": "front",
    "arm_l": "front",
    "leg_r": "front",
    "leg_l": "front",
}

# ---------------------------------------------------------------------------
# ошибки
# ---------------------------------------------------------------------------


class StoryFilmError(RuntimeError):
    """Ошибка построения story-фильма."""


# ---------------------------------------------------------------------------
# вспомогательное: пиксельные спрайты персонажей
# ---------------------------------------------------------------------------


def _load_skin(char: str, skins_dir: Optional[Path] = None) -> Image.Image:
    skins_dir = skins_dir or (
        Path(__file__).resolve().parents[3]
        / "src" / "shortsfactory" / "blend3d" / "presets" / "textures" / "skins"
    )
    candidates = [
        skins_dir / f"{char}_mouth_closed.png",
        skins_dir / f"{char}_64.png",
    ]
    for c in candidates:
        if c.exists():
            return Image.open(c).convert("RGBA")
    raise StoryFilmError(f"Скин не найден для '{char}' (искал в {skins_dir})")


def _tile(tex: Image.Image, part: str, face: str) -> Image.Image:
    """Вырезает грань части скина и масштабирует до блока 16x16."""
    rect = region_px(part, face)
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    tile = tex.crop(rect).resize((16, 16), Image.NEAREST) if (w, h) != (16, 16) else tex.crop(rect)
    return tile


def _flip_h(tile: Image.Image) -> Image.Image:
    return ImageOps.mirror(tile)


def render_character_sprite(
    char: str,
    *,
    scale: int = 4,
    skins_dir: Optional[Path] = None,
    facing: str = "front",
    leg_phase: float = 0.0,
) -> Image.Image:
    """Собирает спрайт персонажа из скина (вид анфас/профиль).

    Сборка по «кубику»: голова 16x16 (2 блока), тело 16x16,
    руки 8x16 слева/справа, ноги 8x16 снизу. scale — пикселей на блок.
    leg_phase — фаза шага (-1..1): при |leg_phase|>0 ноги переступают
    (одна поднята, другая опорная). Для неизвестных персонажей рисуется
    универсальный человечек (не обязательно Minecraft).
    """
    if char in ("steve", "alex"):
        return _sprite_from_skin(char, scale=scale, skins_dir=skins_dir, facing=facing, leg_phase=leg_phase)
    return _sprite_generic(char, scale=scale, facing=facing, leg_phase=leg_phase)


GENERIC_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    # (кожа, рубашка, штаны, волосы, сапоги)
    "boy": ((232, 196, 160), (64, 140, 220), (70, 74, 92), (120, 84, 52), (60, 56, 60)),
    "girl": ((244, 208, 172), (210, 100, 130), (110, 70, 120), (200, 120, 60), (80, 60, 70)),
    "old": ((226, 192, 156), (150, 150, 150), (90, 90, 100), (200, 200, 205), (70, 70, 75)),
    "guard": ((220, 184, 150), (168, 140, 90), (100, 90, 70), (130, 120, 100), (60, 52, 44)),
    "golem": ((190, 190, 195), (120, 120, 128), (90, 90, 98), (150, 150, 155), (70, 70, 76)),
    "default": ((232, 196, 160), (64, 140, 220), (70, 74, 92), (120, 84, 52), (60, 56, 60)),
}


def _sprite_generic(char: str, *, scale: int, facing: str, leg_phase: float = 0.0) -> Image.Image:
    pal = GENERIC_PALETTES.get(char, GENERIC_PALETTES["default"])
    skin, shirt, pants, hair, shoes = pal
    b = scale
    w, h = b * 24, b * 52
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    dark = (34, 28, 34)
    shirt_dark = tuple(max(0, c - 34) for c in shirt)
    pants_dark = tuple(max(0, c - 30) for c in pants)
    hair_dark = tuple(max(0, c - 28) for c in hair)
    skin_dark = tuple(max(0, c - 26) for c in skin)
    shoes_dark = tuple(max(0, c - 22) for c in shoes)

    # ---------- тень под персонажем ----------
    d.ellipse([b * 3, b * 46, b * 21, b * 52], fill=(0, 0, 0, 70))

    # ---------- шея ----------
    d.rectangle([b * 9, b * 14, b * 15, b * 18], fill=skin_dark)

    # ---------- голова (16 x 16 блоков) ----------
    hx, hy = b * 4, b * 0
    # контур головы
    d.rectangle([hx - b // 4, hy - b // 4, hx + b * 16 + b // 4, hy + b * 16 + b // 4], fill=dark)
    d.rectangle([hx, hy, hx + b * 16, hy + b * 16], fill=skin)

    # уши
    d.rectangle([hx - b, hy + b * 5, hx, hy + b * 11], fill=skin)
    d.rectangle([hx + b * 16, hy + b * 5, hx + b * 17, hy + b * 11], fill=skin)

    # волосы: верх, затылок и чёлка
    d.rectangle([hx, hy, hx + b * 16, hy + b * 5], fill=hair)
    d.rectangle([hx, hy, hx + b * 2, hy + b * 16], fill=hair)  # левая боковая
    d.rectangle([hx + b * 14, hy, hx + b * 16, hy + b * 16], fill=hair)  # правая боковая
    # чёлка зубчиками
    for i in range(4):
        x0 = hx + b * (1 + i * 3.5)
        d.polygon([(x0, hy + b * 5), (x0 + b * 3, hy + b * 5), (x0 + b * 1.5, hy + b * 9)], fill=hair)

    # лицо
    eye_c = (28, 26, 38)
    # глаза (белки + зрачок + блик)
    if facing == "right":
        ex = hx + b * 9
        d.rectangle([ex, hy + b * 7, ex + b * 5, hy + b * 10], fill=(250, 250, 252))
        d.rectangle([ex + b, hy + b * 7.5, ex + b * 3, hy + b * 9.5], fill=eye_c)
        d.rectangle([ex + b * 2, hy + b * 8, ex + b * 3, hy + b * 9], fill=(255, 255, 255))
    elif facing == "left":
        ex = hx + b * 2
        d.rectangle([ex, hy + b * 7, ex + b * 5, hy + b * 10], fill=(250, 250, 252))
        d.rectangle([ex + b * 2, hy + b * 7.5, ex + b * 4, hy + b * 9.5], fill=eye_c)
        d.rectangle([ex + b * 2, hy + b * 8, ex + b * 3, hy + b * 9], fill=(255, 255, 255))
    else:
        for ex in (hx + b * 2, hx + b * 9):
            d.rectangle([ex, hy + b * 7, ex + b * 5, hy + b * 10], fill=(250, 250, 252))
            d.rectangle([ex + b, hy + b * 7.5, ex + b * 3, hy + b * 9.5], fill=eye_c)
            d.rectangle([ex + b * 2, hy + b * 8, ex + b * 3, hy + b * 9], fill=(255, 255, 255))
    # брови
    for ex in (hx + b * 1, hx + b * 8):
        d.rectangle([ex, hy + b * 6, ex + b * 6, hy + b * 7], fill=hair_dark)
    # рот — улыбка
    d.arc([hx + b * 5, hy + b * 11, hx + b * 11, hy + b * 14], start=0, end=180, fill=(120, 60, 55), width=max(1, b // 2))
    # румянец
    d.ellipse([hx + b * 1, hy + b * 10, hx + b * 4, hy + b * 12], fill=(235, 150, 140))
    d.ellipse([hx + b * 12, hy + b * 10, hx + b * 15, hy + b * 12], fill=(235, 150, 140))

    # ---------- тело (16 x 16) ----------
    bx, by = b * 4, b * 16
    d.rectangle([bx - b // 4, by - b // 4, bx + b * 16 + b // 4, by + b * 16 + b // 4], fill=dark)
    d.rectangle([bx, by, bx + b * 16, by + b * 16], fill=shirt)
    # воротник
    d.rectangle([bx + b * 5, by, bx + b * 11, by + b * 3], fill=shirt_dark)
    # пуговицы
    for i in range(3):
        d.ellipse([bx + b * 7, by + b * (5 + i * 3), bx + b * 9, by + b * (7 + i * 3)], fill=shirt_dark)
    # пояс
    d.rectangle([bx, by + b * 12, bx + b * 16, by + b * 14], fill=shirt_dark)

    # ---------- руки (8 x 16 каждая) с локтём и кистью ----------
    arm_top = by + b
    # левая рука: плечо (рукав) → предплечье (кожа)
    d.rectangle([b * 0, arm_top, b * 4, arm_top + b * 15], fill=dark)
    d.rectangle([b * 0, arm_top, b * 4, arm_top + b * 8], fill=shirt)
    d.rectangle([b * 0, arm_top + b * 8, b * 4, arm_top + b * 13], fill=skin)
    d.ellipse([b * 0, arm_top + b * 11, b * 4, arm_top + b * 15], fill=skin)
    # правая рука
    d.rectangle([b * 20, arm_top, b * 24, arm_top + b * 15], fill=dark)
    d.rectangle([b * 20, arm_top, b * 24, arm_top + b * 8], fill=shirt)
    d.rectangle([b * 20, arm_top + b * 8, b * 24, arm_top + b * 13], fill=skin)
    d.ellipse([b * 20, arm_top + b * 11, b * 24, arm_top + b * 15], fill=skin)

    # ---------- ноги (8 x 16 каждая) с фазами шага ----------
    leg_y = b * 32
    lift = int(abs(leg_phase) * b * 3.5)
    forward = int(leg_phase * b * 1.5)
    # левая нога (при leg_phase>0 — опорная, иначе поднята)
    left_up = max(0, -forward)
    right_up = max(0, forward)
    left_lift = lift if left_up else 0
    right_lift = lift if right_up else 0
    # левая
    d.rectangle([b * 4 - b // 4, leg_y, b * 12 + b // 4, leg_y + b * 16 + b // 4], fill=dark)
    d.rectangle([b * 4, leg_y, b * 12, leg_y + b * 16 - left_lift], fill=pants)
    d.rounded_rectangle([b * 4, leg_y + b * 11 - left_lift, b * 12, leg_y + b * 16 - left_lift], radius=b, fill=shoes)
    d.rectangle([b * 4, leg_y + b * 11 - left_lift, b * 12, leg_y + b * 12 - left_lift], fill=shoes_dark)
    # правая
    d.rectangle([b * 12 - b // 4, leg_y, b * 20 + b // 4, leg_y + b * 16 + b // 4], fill=dark)
    d.rectangle([b * 12, leg_y, b * 20, leg_y + b * 16 - right_lift], fill=pants)
    d.rounded_rectangle([b * 12, leg_y + b * 11 - right_lift, b * 20, leg_y + b * 16 - right_lift], radius=b, fill=shoes)
    d.rectangle([b * 12, leg_y + b * 11 - right_lift, b * 20, leg_y + b * 12 - right_lift], fill=shoes_dark)

    # юбка для девочки (поверх штанов)
    if char == "girl":
        d.polygon(
            [(b * 2, leg_y + b * 2), (b * 22, leg_y + b * 2),
             (b * 20, leg_y + b * 12), (b * 4, leg_y + b * 12)],
            fill=shirt_dark,
        )
        d.rectangle([b * 2, leg_y + b * 2, b * 22, leg_y + b * 4], fill=shirt)
        # длинные волосы по бокам
        d.rectangle([hx - b, hy + b * 4, hx + b * 2, hy + b * 20], fill=hair)
        d.rectangle([hx + b * 14, hy + b * 4, hx + b * 17, hy + b * 20], fill=hair)
        # бант
        d.ellipse([hx + b * 6, hy + b * 2, hx + b * 10, hy + b * 4], fill=(230, 90, 110))
    return canvas


def _sprite_from_skin(char: str, *, scale: int, skins_dir: Optional[Path], facing: str, leg_phase: float = 0.0) -> Image.Image:
    tex = _load_skin(char, skins_dir)
    b = scale  # блок = scale px
    w, h = b * 24, b * 52
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # тень под персонажем
    draw.ellipse([b * 3, b * 46, b * 21, b * 52], fill=(0, 0, 0, 70))

    head = _tile(tex, "head", "front")
    if facing == "left":
        head = _flip_h(head)
    elif facing == "right":
        head = head
    # лёгкая тень по контуру
    canvas.paste(head.resize((b * 16, b * 16), Image.NEAREST), (b * 4, b * 0))

    body = _tile(tex, "body", "front")
    if facing == "left":
        arm_r = _tile(tex, "arm_l", "front")
        arm_l = _tile(tex, "arm_r", "front")
    else:
        arm_l = _tile(tex, "arm_l", "front")
        arm_r = _tile(tex, "arm_r", "front")
    canvas.paste(body.resize((b * 16, b * 16), Image.NEAREST), (b * 4, b * 16))

    leg_l = _tile(tex, "leg_l", "front")
    leg_r = _tile(tex, "leg_r", "front")
    # фазы шага: одна нога приподнята и укорочена, вторая опорная
    lift = int(abs(leg_phase) * b * 3)
    forward = int(leg_phase * b * 1.5)
    left_up = max(0, -forward)
    right_up = max(0, forward)
    leg_l_up = leg_l.crop((0, 0, b * 8, b * 16 - left_up)) if left_up else leg_l
    leg_r_up = leg_r.crop((0, 0, b * 8, b * 16 - right_up)) if right_up else leg_r
    canvas.paste(leg_l_up.resize((b * 8, b * 16 - left_up), Image.NEAREST), (b * 4, b * 32 + left_up))
    canvas.paste(leg_r_up.resize((b * 8, b * 16 - right_up), Image.NEAREST), (b * 12, b * 32 + right_up))

    canvas.paste(arm_l.resize((b * 8, b * 16), Image.NEAREST), (b * 0, b * 16))
    canvas.paste(arm_r.resize((b * 8, b * 16), Image.NEAREST), (b * 16, b * 16))
    return canvas


# ---------------------------------------------------------------------------
# фон сцены
# ---------------------------------------------------------------------------


def _palette(location: Optional[str]) -> tuple[tuple[int, int, int], ...]:
    if not location:
        return LOCATION_PALETTES["default"]
    for key in LOCATION_PALETTES:
        if key in location.lower():
            return LOCATION_PALETTES[key]
    return LOCATION_PALETTES["default"]


_BACKGROUND_CACHE: dict[tuple, Image.Image] = {}


def _brighten(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(min(255, int(v * k)) for v in c)


def _darken(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(max(0, int(v * k)) for v in c)


def render_background(location: Optional[str], width: int, height: int, seed: int = 7) -> Image.Image:
    """«Книжный» иллюстративный фон: небо, солнце, облака, холмы,
    деревья, земля с травой и цветами, спецэффекты локаций и виньетка.
    Результат кэшируется по (location, size, seed)."""
    key = (location, width, height, seed)
    cached = _BACKGROUND_CACHE.get(key)
    if cached is not None:
        return cached

    sky, ground, g2, deco, accent = _palette(location)
    W, H = width, height
    rng = random.Random(seed)
    loc = (location or "").lower()

    is_cave = "cave" in loc or "пещер" in loc
    is_night = "night" in loc or "ноч" in loc
    is_snow = "snow" in loc or "снег" in loc or "зим" in loc
    is_desert = "desert" in loc or "пуст" in loc
    has_water = "озер" in loc or "оз" in loc or "lake" in loc or "рек" in loc or "river" in loc

    # ---------- небо (градиент, numpy) ----------
    if is_cave:
        top, bottom = _darken(sky, 0.9), sky
    elif is_night:
        top, bottom = _darken(sky, 0.7), sky
    else:
        top, bottom = _brighten(sky, 1.25), sky
    t = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    top_a = np.array(top, dtype=np.float32)[None, None, :]
    bot_a = np.array(bottom, dtype=np.float32)[None, None, :]
    arr = (top_a * (1.0 - t) + bot_a * t)
    arr = np.broadcast_to(arr, (H, W, 3)).copy().astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(img)

    horizon = int(H * 0.60)
    rng = random.Random(seed)

    # ---------- солнце (не для cave/night) ----------
    if not is_cave and not is_night:
        sun_x = rng.randint(int(W * 0.68), int(W * 0.88))
        sun_y = rng.randint(int(H * 0.10), int(H * 0.22))
        r_sun = int(H * 0.045)
        # свечение
        for i in range(6, 0, -1):
            rr = r_sun + i * 7
            col = (255, 240, 190) if i > 3 else (244, 224, 160)
            d.ellipse([sun_x - rr, sun_y - rr, sun_x + rr, sun_y + rr], fill=col)
        d.ellipse([sun_x - r_sun, sun_y - r_sun, sun_x + r_sun, sun_y + r_sun], fill=(255, 244, 205))

    # ---------- облака ----------
    if not is_cave and not is_night:
        n_clouds = rng.randint(3, 5)
        for _ in range(n_clouds):
            cx = rng.randint(0, W)
            cy = rng.randint(int(H * 0.04), int(H * 0.32))
            r = rng.randint(int(H * 0.028), int(H * 0.055))
            col_top = (252, 252, 255)
            col_bot = (216, 226, 240)
            for dx, dy, rr in (
                (0, 0, r), (int(r * 0.9), int(r * 0.15), int(r * 0.8)),
                (-int(r * 0.9), int(r * 0.15), int(r * 0.8)),
                (int(r * 0.45), -int(r * 0.3), int(r * 0.6)),
            ):
                d.ellipse([cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr], fill=col_top)
                d.ellipse([cx + dx - rr, cy + dy + int(r * 0.25) - rr, cx + dx + rr, cy + dy + int(r * 0.25) + rr], fill=col_bot)

    # ---------- горы/холмы (2 слоя) ----------
    hill1 = _darken(g2, 0.85)
    hill2 = _darken(g2, 1.0)
    for layer, base_col, amp_k in ((0, hill1, 0.05), (1, hill2, 0.075)):
        base_y = horizon + 14 + layer * int(H * 0.05)
        amp = int(H * amp_k)
        pts: list[tuple[int, int]] = []
        step = max(24, W // 40)
        for x in range(-step, W + step, step):
            y = base_y - int(amp * (0.55 + 0.45 * math.sin(x / 140.0 + layer * 2.3 + seed)))
            pts.append((x, y))
        pts.append((W, H))
        pts.append((0, H))
        d.polygon(pts, fill=base_col)

    # ---------- вода (если есть озеро/река) ----------
    if has_water and not is_cave:
        water_y = horizon + int(H * 0.06)
        water_col = (94, 150, 196) if not is_night else (48, 70, 110)
        d.polygon([(0, water_y), (W, water_y), (W, H), (0, H)], fill=water_col)
        for k in range(14):
            wx = rng.randint(0, W)
            wy = rng.randint(water_y + 8, H - 6)
            wl = rng.randint(20, 90)
            d.line([(wx, wy), (wx + wl, wy)], fill=(255, 255, 255), width=2)
        ground_for_grass = water_col
        ground_start = water_y
    else:
        ground_for_grass = ground
        ground_start = horizon

    # ---------- земля ----------
    d.rectangle([0, ground_start, W, H], fill=ground_for_grass)
    # пятна
    for _ in range(random.Random(seed + 1).randint(20, 34)):
        x = rng.randint(0, W)
        y = rng.randint(ground_start + 12, H - 6)
        r = rng.randint(6, 30)
        col = tuple(max(0, min(255, ground_for_grass[i] + rng.randint(-34, 26))) for i in range(3))
        d.ellipse([x - r, y - r // 2, x + r, y + r // 2], fill=col)
    # трава (штрихи)
    grass_col = _brighten(g2, 1.15)
    for _ in range(random.Random(seed + 2).randint(90, 150)):
        x = rng.randint(0, W)
        y = rng.randint(ground_start + 6, H - 4)
        ln = rng.randint(5, 14)
        d.line([(x, y), (x + rng.randint(-3, 3), y - ln)], fill=grass_col, width=2)
    # цветы
    for _ in range(random.Random(seed + 3).randint(14, 26)):
        x = rng.randint(0, W)
        y = rng.randint(ground_start + 10, H - 8)
        r = rng.randint(3, 6)
        col = accent if rng.random() < 0.6 else (255, 210, 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        d.ellipse([x - r // 2, y - r - 3, x + r // 2, y - r + 3], fill=(80, 140, 70))

    # ---------- деревья (для открытых локаций) ----------
    if not is_cave and not is_desert:
        n_trees = rng.randint(3, 5)
        for _ in range(n_trees):
            tx = rng.randint(int(W * 0.02), int(W * 0.98))
            ty = rng.randint(ground_start + int(H * 0.02), ground_start + int(H * 0.12))
            th = rng.randint(int(H * 0.10), int(H * 0.17))
            tw = int(th * 0.5)
            trunk_col = (104, 74, 50) if not is_snow else (110, 96, 82)
            leaf_col = tuple(
                max(0, min(255, g2[i] + rng.randint(-22, 30))) for i in range(3)
            )
            if is_snow:
                leaf_col = _brighten(leaf_col, 1.25)
            d.rectangle([tx - tw // 6, ty - th, tx + tw // 6, ty + int(th * 0.4)], fill=trunk_col)
            crowny = ty - th
            for dx, dy, rr in (
                (0, 0, int(th * 0.5)),
                (-int(th * 0.22), -int(th * 0.12), int(th * 0.34)),
                (int(th * 0.22), -int(th * 0.12), int(th * 0.34)),
                (0, -int(th * 0.20), int(th * 0.36)),
            ):
                d.ellipse([tx + dx - rr, crowny + dy - rr, tx + dx + rr, crowny + dy + rr], fill=leaf_col)

    # ---------- пещера: сталактиты и кристаллы ----------
    if is_cave:
        stal_col = _darken(deco, 0.8)
        for k in range(9):
            sx = rng.randint(0, W)
            base_y = rng.randint(0, int(H * 0.04))
            sw = rng.randint(int(H * 0.02), int(H * 0.05))
            sh = rng.randint(int(H * 0.08), int(H * 0.22))
            d.polygon([(sx - sw, base_y), (sx + sw, base_y), (sx, base_y + sh)], fill=stal_col)
        for k in range(7):
            cx = rng.randint(0, W)
            cy = rng.randint(int(H * 0.45), int(H * 0.78))
            cs = rng.randint(6, 14)
            glow = (140, 170, 230)
            for i in range(4, 0, -1):
                rr = cs + i * 5
                d.polygon([(cx - rr, cy), (cx, cy - rr), (cx + rr, cy), (cx, cy + rr)], fill=glow)
            d.polygon([(cx - cs, cy), (cx, cy - cs), (cx + cs, cy), (cx, cy + cs)], fill=(190, 215, 250))

    # ---------- ночь: звёзды и луна ----------
    if is_night:
        for _ in range(80):
            sx = rng.randint(0, W)
            sy = rng.randint(0, horizon)
            if rng.random() < 0.7:
                d.point((sx, sy), fill=(255, 255, 255))
            else:
                d.ellipse([sx - 1, sy - 1, sx + 1, sy + 1], fill=(255, 255, 220))
        mx = int(W * 0.80)
        my = int(H * 0.14)
        mr = int(H * 0.035)
        for i in range(5, 0, -1):
            rr = mr + i * 6
            d.ellipse([mx - rr, my - rr, mx + rr, my + rr], fill=(232, 236, 250))
        d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(248, 248, 255))
        d.ellipse([mx - mr // 2, my - mr // 2, mx, my], fill=(226, 230, 246))
    # снег: снежинки
    if is_snow:
        for _ in range(60):
            sx = rng.randint(0, W)
            sy = rng.randint(0, H)
            d.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=(250, 252, 255))
    # пустыня: дюны и кактусы
    if is_desert:
        for k in range(4):
            dx0 = rng.randint(0, W)
            dy0 = rng.randint(horizon + int(H * 0.05), H - int(H * 0.08))
            dr = rng.randint(int(H * 0.08), int(H * 0.18))
            d.pieslice([dx0 - dr, dy0 - dr, dx0 + dr, dy0 + dr], 180, 360, fill=_brighten(deco, 1.15))
        for k in range(3):
            cx = rng.randint(0, W)
            cy = rng.randint(ground_start + int(H * 0.04), H - int(H * 0.06))
            ch = rng.randint(int(H * 0.06), int(H * 0.12))
            d.rectangle([cx - 4, cy - ch, cx + 4, cy], fill=(70, 110, 60))
            d.rectangle([cx - 2, cy - ch - 8, cx + 10, cy - ch + 4], fill=(70, 110, 60))
            d.rectangle([cx - 10, cy - ch + 10, cx + 2, cy - ch + 22], fill=(70, 110, 60))

    # ---------- передний план (силуэты) ----------
    front_col = _darken(ground_for_grass, 0.72)
    for k in range(7):
        fx = rng.randint(0, W)
        fy = H - rng.randint(0, int(H * 0.01))
        fr = rng.randint(int(H * 0.04), int(H * 0.09))
        d.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=front_col)
        d.ellipse([fx - fr + int(fr * 0.6), fy - int(fr * 0.4), fx + fr + int(fr * 0.6), fy + int(fr * 0.4)], fill=front_col)

    # ---------- виньетка ----------
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = W / 2.0, H * 0.45
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(W, H)
    vig = np.clip(1.0 - 0.30 * np.clip((dist - 0.35) / 0.5, 0.0, 1.0) ** 2, 0.55, 1.0)
    arr2 = np.asarray(img, dtype=np.float32) * vig[:, :, None]
    out = Image.fromarray(arr2.astype(np.uint8), "RGB")

    _BACKGROUND_CACHE[key] = out
    return out


# ---------------------------------------------------------------------------
# сборка кадра сцены
# ---------------------------------------------------------------------------


def _sprite_with_gesture(spr: Image.Image, gesture: str, t: float) -> Image.Image:
    """Добавляет простой жест к спрайту: wave (помахать), point_right,
    point_left (указать рукой). t — локальное время 0..1."""
    if not gesture:
        return spr
    out = spr.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    b = w // 24
    skin = (232, 196, 160)
    shirt = (64, 140, 220)
    dark = (34, 28, 34)

    if gesture == "wave":
        # рука вверх у правого плеча, покачивается
        sway = int(math.sin(t * math.tau * 2) * b * 1.6)
        x0 = w - b * 8 + sway
        d.rectangle([x0, b * 8, x0 + b * 4, b * 20], fill=dark)
        d.rectangle([x0, b * 8, x0 + b * 4, b * 17], fill=shirt)
        d.rectangle([x0, b * 4, x0 + b * 4, b * 9], fill=skin)
    elif gesture == "point_right":
        x0 = w - b * 8
        d.rectangle([x0, b * 22, x0 + b * 12, b * 26], fill=dark)
        d.rectangle([x0, b * 22, x0 + b * 8, b * 26], fill=shirt)
        d.rectangle([x0 + b * 8, b * 22, x0 + b * 12, b * 26], fill=skin)
    elif gesture == "point_left":
        x0 = b * 4
        d.rectangle([x0 - b * 12, b * 22, x0, b * 26], fill=dark)
        d.rectangle([x0 - b * 8, b * 22, x0, b * 26], fill=shirt)
        d.rectangle([x0 - b * 12, b * 22, x0 - b * 8, b * 26], fill=skin)
    return out


def _char_motion(char: str, moves: Optional[list[dict[str, Any]]], t: float) -> Optional[dict[str, Any]]:
    if not moves:
        return None
    for m in moves:
        if m.get("char") == char:
            t0 = m.get("t0", 0.0)
            t1 = m.get("t1", 1.0)
            if t0 <= t <= t1:
                return m
    return None


def compose_scene_frame(
    *,
    title: str,
    location: Optional[str],
    characters: list[tuple[str, str]],  # (char, side: 'left'|'right')
    width: int,
    height: int,
    seed: int = 7,
    voice_line: str = "",
    t: float = 0.0,
    moves: Optional[list[dict[str, Any]]] = None,
) -> Image.Image:
    """Иллюстрация сцены: фон + персонажи (+ движения) + заголовок/реплика."""
    img = render_background(location, width, height, seed=seed)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(img)

    n = len(characters)
    for i, (char, side) in enumerate(characters):
        motion = _char_motion(char, moves, t)
        facing: str = "right" if side == "right" else "left"

        spr = render_character_sprite(char, scale=10, facing=facing)
        sw, sh = spr.size

        if motion is not None:
            # позиция интерполируется в пределах маршрута
            t0, t1 = motion.get("t0", 0.0), motion.get("t1", 1.0)
            local = (t - t0) / max(1e-6, t1 - t0)
            sx0, sy0 = motion.get("start", (0.15, 0.82))
            sx1, sy1 = motion.get("end", (0.85, 0.82))
            x = sx0 + (sx1 - sx0) * local
            y = sy0 + (sy1 - sy0) * local
            facing = "right" if sx1 >= sx0 else "left"
            # фаза шага: ноги переступают при ходьбе
            leg_phase = math.sin(local * math.tau * 2.0)
            spr = render_character_sprite(char, scale=10, facing=facing, leg_phase=leg_phase)
            sw, sh = spr.size
            gesture = motion.get("gesture")
            if gesture:
                spr = _sprite_with_gesture(spr, gesture, local)
                sw, sh = spr.size
            # покачивание при ходьбе
            bounce = abs(math.sin(local * math.tau * 2.0)) * 5.0
            anchor_x = int(x * width - sw / 2)
            anchor_y = int(y * height - sh) - int(bounce)
            # плавное появление/уход
            alpha = 1.0
            fade = 0.08
            if local < fade:
                alpha = local / fade
            elif local > 1.0 - fade:
                alpha = (1.0 - local) / fade
            if alpha < 1.0:
                spr = spr.copy()
                spr.putalpha(spr.getchannel("A").point(lambda v: int(v * alpha)))
        else:
            anchor_x = int(width * (0.22 if side == "left" else 0.72))
            if n == 1:
                anchor_x = width // 2 - sw // 2
            anchor_y = height - sh - int(height * 0.10)

        canvas.alpha_composite(spr, (anchor_x, anchor_y))

    # затемнение низа для читаемости текста
    dark = Image.new("L", (width, height), 0)
    d = ImageDraw.Draw(dark)
    d.rectangle([0, int(height * 0.78), width, height], fill=90)
    black = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    black.putalpha(dark.point(lambda v: v))
    canvas = Image.alpha_composite(canvas, black)

    out = canvas.convert("RGB")
    draw = ImageDraw.Draw(out)
    # заголовок
    try:
        draw.text((40, 40), title, fill=(255, 255, 255), stroke_width=3, stroke_fill=(10, 10, 10))
    except Exception:
        pass
    if voice_line:
        # перенос строк
        words = voice_line.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > 36:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)
        y0 = int(height * 0.82)
        for li, ln in enumerate(lines[:4]):
            draw.text((40, y0 + li * 56), ln, fill=(255, 255, 255), stroke_width=3, stroke_fill=(10, 10, 10))
    return out


# ---------------------------------------------------------------------------
# аудио: TTS + эмбиент
# ---------------------------------------------------------------------------


def _load_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
        return data, sr


def _write_wav_mono(path: Path, samples: np.ndarray, sr: int) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _soft_pad(duration: float, sr: int = DEFAULT_SAMPLE_RATE, amp: float = 0.05) -> np.ndarray:
    """Мягкий эмбиент: низкие аккорды с плавным нарастанием/затуханием."""
    n = int(duration * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    freqs = (110.0, 130.81, 164.81, 196.0, 220.0, 261.63, 329.63)
    rng = random.Random(42)
    for i, f in enumerate(freqs):
        phase = rng.random() * math.tau
        amp_i = amp * (0.7 if i % 2 else 1.0)
        out += amp_i * np.sin(math.tau * f * t + phase)
    # fade in/out
    fade = int(0.8 * sr)
    if n > fade * 2:
        out[:fade] *= np.linspace(0, 1, fade)
        out[-fade:] *= np.linspace(1, 0, fade)
    return out


def synthesize_scene_audio(
    text: str,
    *,
    lang: str,
    voice_override: Optional[str] = None,
    sr: int = DEFAULT_SAMPLE_RATE,
    ambient: bool = True,
    gape: float = 0.35,
) -> tuple[np.ndarray, float]:
    """Озвучка сцены: TTS + тишина сзади + мягкий эмбиент."""
    from ..tts import TTSService
    from ..config import load_config

    service = TTSService(load_config(Path(".")))
    wav_path, dur = service.synthesize_line(text, lang, voice_override=voice_override)
    speech, sp_sr = _load_wav_mono(Path(wav_path))
    if sp_sr != sr:
        # простое передискретизирование
        ratio = sr / sp_sr
        idx = (np.arange(int(len(speech) * ratio)) / ratio).astype(int)
        idx = np.clip(idx, 0, len(speech) - 1)
        speech = speech[idx]

    total = dur + gape * 2
    n = int(total * sr)
    out = np.zeros(n, dtype=np.float32)
    start = int(gape * sr)
    out[start:start + len(speech)] = speech
    if ambient:
        out += _soft_pad(total, sr=sr, amp=0.03)
    out = np.clip(out, -1.0, 1.0)
    return out, total


# ---------------------------------------------------------------------------
# Ken Burns (ffmpeg zoompan)
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    return "ffmpeg"


def ken_burns_clip(
    frame: Image.Image,
    out_video: Path,
    *,
    duration: float,
    fps: int = DEFAULT_FPS,
    zoom_in: bool = True,
) -> Path:
    """Одна иллюстрация → видео-клип с медленным зумом (zoompan)."""
    tmp_png = out_video.parent / (out_video.stem + "_frame.png")
    frame.save(tmp_png)
    w, h = frame.size
    if zoom_in:
        zexpr = "min(zoom+0.0006,1.35)"
    else:
        zexpr = "max(zoom-0.0006,1.0)"
    vf = (
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={int(duration * fps)}:s={w}x{h}:fps={fps}"
    )
    cmd = [
        _ffmpeg(), "-y", "-loop", "1", "-i", str(tmp_png),
        "-vf", vf, "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-crf", "20", str(out_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StoryFilmError(f"ffmpeg zoompan failed: {proc.stderr[-800:]}")
    tmp_png.unlink(missing_ok=True)
    return out_video


# ---------------------------------------------------------------------------
# субтитры
# ---------------------------------------------------------------------------


def write_srt(lines: list[tuple[float, float, str]], path: Path) -> Path:
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    parts: list[str] = []
    for i, (t0, t1, text) in enumerate(lines, 1):
        parts.append(f"{i}\n{ts(t0)} --> {ts(t1)}\n{text}\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# главная функция
# ---------------------------------------------------------------------------

@dataclass
class StorySceneResult:
    index: int
    title: str
    video: Path
    audio_start: float
    duration: float


def _render_scene_sequence(
    *,
    title: str,
    location: Optional[str],
    characters: list[tuple[str, str]],
    width: int,
    height: int,
    seed: int,
    voice_line: str,
    moves: Optional[list[dict[str, Any]]],
    clip_dur: float,
    fps: int,
    out_clip: Path,
    work: Path,
    index: int,
) -> Path:
    """Рендерит сцену как последовательность кадров (с движениями) в MP4."""
    n_frames = max(1, round(clip_dur * fps))
    seq_dir = work / f"seq_{index:03d}"
    seq_dir.mkdir(parents=True, exist_ok=True)
    for k in range(n_frames):
        t = k / n_frames
        frame = compose_scene_frame(
            title=title, location=location, characters=characters,
            width=width, height=height, seed=seed, voice_line=voice_line,
            t=t, moves=moves,
        )
        frame.save(seq_dir / f"{k + 1:04d}.jpg", quality=88)
    cmd = [
        _ffmpeg(), "-y", "-framerate", str(fps),
        "-i", str(seq_dir / "%04d.jpg"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "20",
        str(out_clip),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(seq_dir, ignore_errors=True)
    if proc.returncode != 0:
        raise StoryFilmError(f"ffmpeg sequence failed: {proc.stderr[-800:]}")
    return out_clip


def build_story_film(
    storyboard: dict[str, Any],
    out_dir: Path | str,
    *,
    lang: str = "ru",
    target_s: Optional[float] = None,
    fps: int = DEFAULT_FPS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    voice_map: Optional[dict[str, str]] = None,
    ambient: bool = True,
    subtitles: bool = True,
    seed: int = 7,
) -> Path:
    """Строит story-фильм из сториборда.

    storyboard: результат generate_storyboard (ключи title/scenes[]).
    Возвращает путь к финальному MP4.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)

    scenes = storyboard.get("scenes") or []
    if not scenes:
        raise StoryFilmError("Сториборд не содержит сцен")

    title0 = storyboard.get("title") or "История"
    voice_map = voice_map or {}
    voices = {
        "ru": str(out_dir.parents[0] if False else DEFAULT_PIPER_DIR / voice_map.get("ru", DEFAULT_VOICE_RU)),
    }
    # голоса по умолчанию
    def pick_voice(char: Optional[str]) -> str:
        if char and voice_map.get(char):
            return str(DEFAULT_PIPER_DIR / voice_map[char])
        if lang.startswith("en"):
            return str(DEFAULT_PIPER_DIR / DEFAULT_VOICE_EN)
        return str(DEFAULT_PIPER_DIR / DEFAULT_VOICE_RU)

    # длительности: пропорционально длине текста
    target_s = target_s or float(storyboard.get("target_duration_s") or 60.0)
    total_chars = max(1, sum(len(sc.get("detail") or sc.get("summary") or "") for sc in scenes))
    durations: list[float] = []
    for sc in scenes:
        text = sc.get("detail") or sc.get("summary") or ""
        durations.append(max(4.0, target_s * len(text) / total_chars))

    # --- аудио и клипы ---
    clip_paths: list[Path] = []
    audio_chunks: list[np.ndarray] = []
    srt_lines: list[tuple[float, float, str]] = []
    cursor = 0.0
    results: list[StorySceneResult] = []
    sr = DEFAULT_SAMPLE_RATE

    for i, sc in enumerate(scenes):
        text = (sc.get("detail") or sc.get("summary") or "").strip()
        title = sc.get("title") or f"Сцена {i + 1}"
        location = sc.get("location") or ""
        duration = durations[i]

        # кто в сцене
        chars_meta = sc.get("characters") or []
        chars: list[tuple[str, str]] = []
        default_chars = [("steve", "left"), ("alex", "right")]
        if isinstance(chars_meta, list) and chars_meta:
            for j, c in enumerate(chars_meta[:2]):
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    name = str(c[0])
                    side = str(c[1]) if str(c[1]) in ("left", "right") else ("left" if j == 0 else "right")
                elif isinstance(c, str):
                    name = c
                    side = "left" if j == 0 else "right"
                else:
                    name = c.get("name") or "steve"
                    side = c.get("side") or ("left" if j == 0 else "right")
                if name not in ("steve", "alex"):
                    name = name  # универсальные персонажи разрешены
                chars.append((name, side))
        else:
            chars = default_chars

        # TTS (персонаж → голос)
        voice = pick_voice(chars[0][0] if chars else None)
        speech, sp_dur = synthesize_scene_audio(
            text, lang=lang, voice_override=voice, sr=sr, ambient=ambient,
        )
        clip_dur = max(duration, sp_dur + 0.6)
        clip = work / f"clip_{i:03d}.mp4"
        _render_scene_sequence(
            title=title, location=location, characters=chars,
            width=width, height=height, seed=seed + i, voice_line=text,
            moves=sc.get("moves"), clip_dur=clip_dur, fps=fps,
            out_clip=clip, work=work, index=i,
        )
        clip_paths.append(clip)

        # аудио-блок со вставкой
        pad = np.zeros(int((clip_dur - sp_dur) * sr), dtype=np.float32)
        audio_chunks.append(np.concatenate([speech, pad]))

        srt_lines.append((cursor, cursor + clip_dur, text))
        results.append(StorySceneResult(i, title, clip, cursor, clip_dur))
        cursor += clip_dur

    # --- склейка клипов ---
    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{c.as_posix()}'\n" for c in clip_paths), encoding="utf-8")
    silent_video = work / "video_silent.mp4"
    cmd = [
        _ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-crf", "20", str(silent_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StoryFilmError(f"concat failed: {proc.stderr[-800:]}")

    # --- мастер-аудио ---
    master = np.concatenate(audio_chunks)
    _normalize(master)
    audio_wav = work / "audio_master.wav"
    _write_wav_mono(audio_wav, master, sr)

    # --- mux + субтитры ---
    final = out_dir / f"{_slugify(title0)}.mp4"
    cmd = [
        _ffmpeg(), "-y", "-i", str(silent_video), "-i", str(audio_wav),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(final),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StoryFilmError(f"mux failed: {proc.stderr[-800:]}")

    if subtitles:
        srt_path = out_dir / f"{_slugify(title0)}.srt"
        write_srt(srt_lines, srt_path)
        # burn-in
        burned = out_dir / f"{_slugify(title0)}_subs.mp4"
        sub_path = srt_path.as_posix().replace(":", "\\:")
        cmd = [
            _ffmpeg(), "-y", "-i", str(final),
            "-vf", f"subtitles={sub_path}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", str(burned),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and burned.exists():
            burned.replace(final)

    # отчёт
    report = {
        "title": title0,
        "lang": lang,
        "target_s": target_s,
        "total_s": round(cursor, 2),
        "fps": fps,
        "resolution": [width, height],
        "scenes": [
            {"index": r.index, "title": r.title, "duration_s": round(r.duration, 2)}
            for r in results
        ],
        "video": str(final),
    }
    (out_dir / "story_film_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return final


def _normalize(samples: np.ndarray, peak: float = 0.85) -> None:
    m = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if m > 1e-6:
        samples *= peak / m


def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " _-":
            out.append("_")
    s = "".join(out).strip("_")
    return s or "story"


# ---------------------------------------------------------------------------
# CLI: python -m shortsfactory.generative.story_film --idea "..."
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys

    from .blocky_story import generate_storyboard

    ap = argparse.ArgumentParser(prog="story_film")
    ap.add_argument("--idea", required=True)
    ap.add_argument("--scenes", type=int, default=4)
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--target", type=float, default=60.0)
    ap.add_argument("--out", default="output/story_film")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument("--no-subtitles", action="store_true")
    args = ap.parse_args(argv)

    try:
        sb = generate_storyboard(args.idea, scenes=args.scenes, lang=args.lang, attempts=1)
    except Exception as exc:  # noqa: BLE001
        print(f"STORY_FAIL {exc}")
        return 1

    out = Path(args.out)
    final = build_story_film(
        sb, out,
        lang=args.lang, target_s=args.target,
        fps=args.fps, width=args.width, height=args.height,
        subtitles=not args.no_subtitles,
    )
    print(f"FINAL {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
