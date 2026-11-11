"""Генератор иконки ShortsFactory Pro (детерминированный, Pillow).

Пиксель-арт «блочное лицо» 16×16 (сетка Minecraft-стиля), апскейл
nearest-neighbor до 256/128/64/48/32/16 и упаковка в assets/icon.ico.
Запуск: python make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# 16x16 логическая сетка: символы — ключи палитры.
GRID = [
    "................",
    "................",
    "..hhhhhhhhhhhh..",
    ".hhhhhhhhhhhhhh.",
    ".hhfffffffffhss.",  # правый край — волос/кожа
    ".hfffffffffssss.",
    ".hffffwwffwwfff.",
    ".hffffwbffwbfff.",  # глаза: белок+зрачок
    ".hffffwwffwwfff.",
    ".hffffffffffffm.",
    ".hffffffmmmmffm.",  # усы/рот
    ".hfffffffmmffff.",
    ".hfffffffffffff.",
    "..hhffffffffff..",
    "..ffffffffffss..",
    "................",
]

PALETTE = {
    ".": (0, 0, 0, 0),          # прозрачный фон
    "h": (62, 44, 26, 255),     # волосы (тёмно-каштановый)
    "f": (176, 122, 84, 255),   # кожа
    "s": (62, 44, 26, 255),     # бакенбарды
    "w": (240, 240, 235, 255),  # белок глаза
    "b": (70, 60, 160, 255),    # зрачок (синий)
    "m": (86, 62, 40, 255),     # рот/усы
}

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets" / "icon.ico"


def build_base() -> Image.Image:
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(GRID):
        for x, ch in enumerate(row):
            px[x, y] = PALETTE.get(ch, PALETTE["."])
    return img


def main() -> None:
    base = build_base()
    frames = []
    for size in (256, 128, 64, 48, 32, 16):
        frames.append(base.resize((size, size), Image.NEAREST))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in (256, 128, 64, 48, 32, 16)],
                   append_images=frames[1:])
    print(f"icon written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
