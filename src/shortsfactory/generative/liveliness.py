"""Микроанимация персонажей и камеры для 2D story_film. Детерминировано (crc32)."""
from __future__ import annotations

import math
import zlib

import numpy as np
from PIL import Image


def _phase(key: str) -> float:
    return (zlib.crc32(key.encode("utf-8")) % 628) / 100.0


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def idle_transform(
    spr: Image.Image,
    t: float,
    key: str,
    *,
    speaking: float = 0.0,
) -> Image.Image:
    """Дыхание + покачивание корпуса + перенос веса + жест во время речи.

    Размер результата равен размеру входа, «ноги» остаются на месте.
    """
    w, h = spr.size
    if w < 2 or h < 2:
        return spr
    ph = _phase(key)

    breath = 1.0 + 0.008 * math.sin(2 * math.pi * 0.35 * t + ph)      # ±0.8% высоты
    angle = 1.2 * math.sin(2 * math.pi * 0.22 * t + ph * 0.7)          # ±1.2°
    dx = int(round(3.0 * math.sin(2 * math.pi * 0.13 * t + ph * 1.3))) # ±3 px

    if speaking > 0.05:
        angle += 0.9 * speaking * math.sin(2 * math.pi * 1.6 * t + ph)
        dx += int(round(2.0 * speaking * math.sin(2 * math.pi * 1.1 * t)))

    out = spr.rotate(angle, resample=Image.BICUBIC, expand=False, center=(w / 2, h))
    nh = max(1, min(h, int(round(h * breath))))
    if nh != h:
        out = out.resize((w, nh), Image.BICUBIC)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(out, (dx, h - nh))
    return canvas


def camera_transform(
    img: Image.Image,
    t: float,
    *,
    strength: float = 0.035,
    pan_px: int = 8,
) -> Image.Image:
    """Плавный push-in 3.5% со smoothstep + боковой дрейф вместо линейных 1.5%."""
    w, h = img.size
    zoom = 1.0 + strength * smoothstep(t)
    cw, ch = max(2, int(w / zoom)), max(2, int(h / zoom))
    cx = (w - cw) // 2 + int(pan_px * math.sin(math.pi * t))
    cy = (h - ch) // 2
    cx = max(0, min(w - cw, cx))
    cy = max(0, min(h - ch, cy))
    return img.crop((cx, cy, cx + cw, cy + ch)).resize((w, h), Image.LANCZOS)


def speech_envelope(speech: np.ndarray, sr: int, n_frames: int, fps: int) -> np.ndarray:
    """Нормированная огибающая речи по кадрам — интенсивность жестов."""
    env = np.zeros(max(0, n_frames), dtype=np.float32)
    if speech is None or len(speech) == 0:
        return env
    for i in range(n_frames):
        a = int(i / fps * sr)
        b = min(int((i + 1) / fps * sr), len(speech))
        if b > a:
            env[i] = float(np.sqrt((speech[a:b] ** 2).mean()))
    m = float(env.max())
    return env / m if m > 0 else env
