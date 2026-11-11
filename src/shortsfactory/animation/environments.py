"""Reusable multi-layer environments for the animation engine.

An environment is a small declarative manifest (``animation/environments/*.json``)
plus a deterministic painter.  Every layer is a full stage-sized image with a
depth value: layers closer to the camera move further during a camera move,
which produces parallax without a 3D renderer.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ENVIRONMENTS_DIR = Path(__file__).with_name("environments")
SUPPORTED_ENVIRONMENT_VERSION = 1


class EnvironmentError(ValueError):
    """Raised when an environment manifest is missing or malformed."""


@dataclass(frozen=True)
class Layer:
    id: str
    depth: float
    image: Image.Image


DEFAULT_ROOM: dict[str, Any] = {
    "id": "room",
    "name": "Комната Мило и Нины",
    "version": 1,
    "layers": [
        {"id": "back", "depth": 0.42},
        {"id": "mid", "depth": 0.86},
        {"id": "front", "depth": 1.16},
    ],
    "anchors": {
        "table": {"x": 0.50, "y": 0.735},
        "window": {"x": 0.20, "y": 0.25},
        "shelf": {"x": 0.18, "y": 0.60},
    },
    "bounds": {"x_min": 0.06, "x_max": 0.94},
    "light": {"direction": "left", "color": "#fff3cf", "strength": 0.18},
    "palette": {
        "wall_top": "#f2e4d2",
        "wall_bottom": "#e2cdb4",
        "floor": "#c9a276",
        "floor_line": "#b08a60",
        "skirting": "#a9835e",
        "rug": "#d8b7a0",
        "rug_border": "#b9876f",
        "window": "#bfe4f2",
        "window_frame": "#f7f0e4",
        "frame": "#8a6a45",
        "table": "#8a5a38",
        "table_top": "#b57a4a",
        "shelf": "#a9794f",
        "plant": "#5f9e63",
        "plant_pot": "#c4704e",
        "accent": "#e8713c",
    },
}

DEFAULT_STREET: dict[str, Any] = {
    "id": "street",
    "name": "Улица",
    "version": 1,
    "layers": [
        {"id": "back", "depth": 0.32},
        {"id": "mid", "depth": 0.82},
        {"id": "front", "depth": 1.18},
    ],
    "anchors": {
        "bench": {"x": 0.72, "y": 0.82},
        "lamp": {"x": 0.18, "y": 0.58},
    },
    "bounds": {"x_min": 0.05, "x_max": 0.95},
    "light": {"direction": "right", "color": "#ffe9b8", "strength": 0.22},
    "palette": {
        "sky_top": "#9ed6ef",
        "sky_bottom": "#e9f4e4",
        "sun": "#fff2ae",
        "cloud": "#ffffff",
        "building_far": "#b9c9cf",
        "building_near": "#8fa9b4",
        "window_light": "#f7e6a8",
        "road": "#6f7784",
        "road_line": "#e9e2c8",
        "sidewalk": "#cfc6b4",
        "sidewalk_line": "#b5aa96",
        "curb": "#a99f8b",
        "lamp": "#3d4a54",
        "lamp_light": "#ffe08a",
        "bench": "#9b6a43",
        "bench_dark": "#7a4f31",
        "plant": "#5f9e63",
        "plant_pot": "#c4704e",
        "railing": "#3d4a54",
    },
}


DEFAULT_FOREST: dict[str, Any] = {
    "id": "forest",
    "name": "Сумеречный лес",
    "version": 1,
    "layers": [
        {"id": "back", "depth": 0.40},
        {"id": "mid", "depth": 0.82},
        {"id": "front", "depth": 1.18},
    ],
    "anchors": {
        "stone": {"x": 0.60, "y": 0.82},
    },
    "bounds": {"x_min": 0.20, "x_max": 0.80},
    "light": {"direction": "right", "color": "#6b5fd0", "strength": 0.10},
    "palette": {
        "sky_top": "#14142e",
        "sky_bottom": "#3a2a55",
        "horizon": "#4a3260",
        "moon": "#e8e6d8",
        "moon_glow": "#b9b0d8",
        "trunk_far": "#2a2238",
        "trunk_near": "#241c30",
        "branch": "#1e1828",
        "bush_dark": "#1b2630",
        "bush_darker": "#15202a",
        "ground_top": "#2a3a34",
        "ground_bottom": "#16221f",
        "glow": "#bfe6c0",
        "glow_halo": "#6fe6a0",
        "firefly": "#d8f2c8",
    },
}


def environment_names() -> tuple[str, ...]:
    names = set()
    if ENVIRONMENTS_DIR.exists():
        names.update(path.stem for path in ENVIRONMENTS_DIR.glob("*.json"))
    names.update(("room", "street", "forest"))
    return tuple(sorted(names))


def _manifest(name: str) -> dict[str, Any]:
    fallback = None
    if name == "room":
        fallback = DEFAULT_ROOM
    elif name == "street":
        fallback = DEFAULT_STREET
    elif name == "forest":
        fallback = DEFAULT_FOREST
    path = ENVIRONMENTS_DIR / f"{name}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EnvironmentError(f"Не удалось прочитать окружение <{name}>: {exc}.") from exc
    elif fallback is not None:
        data = dict(fallback)
    else:
        raise EnvironmentError(
            f"Окружение <{name}> не найдено. Доступны: {', '.join(environment_names())}."
        )
    if not isinstance(data, dict):
        raise EnvironmentError(f"Манифест окружения <{name}> должен быть объектом.")
    if int(data.get("version", 0)) != SUPPORTED_ENVIRONMENT_VERSION:
        raise EnvironmentError(
            f"Окружение <{name}>: версия {data.get('version')!r} не поддерживается "
            f"(нужна {SUPPORTED_ENVIRONMENT_VERSION})."
        )
    base = dict(fallback or {})
    for key in ("palette", "anchors", "bounds", "light"):
        merged = dict(base.get(key, {}))
        merged.update(data.get(key, {}) or {})
        data[key] = merged
    layers = data.get("layers") or base.get("layers") or []
    if not layers:
        raise EnvironmentError(f"Окружение <{name}> не содержит слоёв.")
    for layer in layers:
        if not isinstance(layer, dict) or not layer.get("id"):
            raise EnvironmentError(f"Окружение <{name}>: у слоя нет id.")
        depth = layer.get("depth", 1.0)
        try:
            depth = float(depth)
        except (TypeError, ValueError) as exc:
            raise EnvironmentError(f"Окружение <{name}>: глубина слоя {layer['id']} не число.") from exc
        if not 0.0 < depth <= 2.0:
            raise EnvironmentError(f"Окружение <{name}>: глубина слоя {layer['id']} вне 0..2.")
        layer["depth"] = depth
    data["layers"] = layers
    return data


def load_environment(name: str) -> dict[str, Any]:
    return _manifest(name)


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (200, 200, 200)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _gradient(width: int, height: int, top: str, bottom: str) -> Image.Image:
    top_rgb = _hex(top)
    bottom_rgb = _hex(bottom)
    image = Image.new("RGB", (1, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        u = y / max(1, height - 1)
        color = tuple(round(a + (b - a) * u) for a, b in zip(top_rgb, bottom_rgb))
        draw.point((0, y), fill=color)
    return image.resize((width, height), Image.BILINEAR)


def _room_back(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    wall_h = int(height * 0.72)
    image = _gradient(width, wall_h, palette["wall_top"], palette["wall_bottom"]).convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    # The mid layer paints the floor, but parallax can expose the bottom of this
    # layer; fill it with the floor colour so no dark gap appears between planes.
    draw.rectangle((0, wall_h, width, height), fill=palette["floor"])

    # window with sky, sill and curtains
    wl, wt = width * 0.055, height * 0.11
    wr, wb = width * 0.35, height * 0.40
    frame_pad = max(2.0, width * 0.012)
    bar_w = max(1, int(width * 0.010))
    draw.rectangle((wl - frame_pad, wt - frame_pad, wr + frame_pad, wb + frame_pad), fill=palette["window_frame"])
    draw.rectangle((wl, wt, wr, wb), fill=palette["window"])
    draw.rectangle((wl, wb - (wb - wt) * 0.35, wr, wb), fill="#cfe9c2")
    draw.polygon(
        [(wl, wb - 4), (wl + (wr - wl) * 0.25, wb - (wb - wt) * 0.24), (wl + (wr - wl) * 0.55, wb - 4)],
        fill="#9ccb8f",
    )
    draw.polygon(
        [(wl + (wr - wl) * 0.42, wb - 4), (wl + (wr - wl) * 0.72, wb - (wb - wt) * 0.30), (wr, wb - 4)],
        fill="#8bbd7f",
    )
    draw.ellipse(
        (wr - (wr - wl) * 0.32, wt + (wb - wt) * 0.10, wr - (wr - wl) * 0.18, wt + (wb - wt) * 0.24),
        fill="#fff3b0",
    )
    draw.line((wl, (wt + wb) / 2, wr, (wt + wb) / 2), fill=palette["window_frame"], width=bar_w)
    draw.line(((wl + wr) / 2, wt, (wl + wr) / 2, wb), fill=palette["window_frame"], width=bar_w)
    draw.rectangle((wl - frame_pad * 2, wb + frame_pad, wr + frame_pad * 2, wb + frame_pad * 3), fill=palette["window_frame"])
    for side in (-1, 1):
        curtain_x = wl if side < 0 else wr
        draw.polygon(
            [
                (curtain_x, wt - frame_pad),
                (curtain_x + side * width * 0.045, wt - frame_pad),
                (curtain_x + side * width * 0.030, wb),
                (curtain_x, wb),
            ],
            fill=palette["accent"],
        )

    # framed picture
    pl, pt, pr, pb = width * 0.72, height * 0.12, width * 0.94, height * 0.29
    inset_x = (pr - pl) * 0.08
    inset_y = (pb - pt) * 0.10
    draw.rectangle((pl, pt, pr, pb), fill=palette["frame"])
    draw.rectangle((pl + inset_x, pt + inset_y, pr - inset_x, pb - inset_y), fill="#bfe4f2")
    draw.ellipse((pl + inset_x * 2, pt + inset_y * 2, pr - inset_x * 2, pb - inset_y * 2), fill=palette["accent"])
    draw.polygon(
        [
            (pl + inset_x * 1.4, pb - inset_y * 1.4),
            (pl + (pr - pl) * 0.45, pt + (pb - pt) * 0.45),
            (pr - inset_x * 1.4, pb - inset_y * 1.4),
        ],
        fill="#8bbd7f",
    )

    # wall shelf with books and a plant
    shelf_l, shelf_r = width * 0.055, width * 0.30
    shelf_y = height * 0.60
    draw.rectangle((shelf_l, shelf_y, shelf_r, shelf_y + height * 0.012), fill=palette["shelf"])
    for index, color in enumerate(("#d96b52", "#4f8fb0", "#e8c14a", "#7bc4a4")):
        bx = shelf_l + width * 0.012 + index * width * 0.023
        bw = width * 0.018
        bh = height * 0.045 + (index % 2) * height * 0.008
        draw.rectangle((bx, shelf_y - bh, bx + bw, shelf_y), fill=color, outline="#3a2a1a", width=2)
    pot_x = shelf_r - width * 0.035
    draw.rectangle(
        (pot_x - width * 0.014, shelf_y - height * 0.028, pot_x + width * 0.014, shelf_y),
        fill=palette["plant_pot"],
        outline="#3a2a1a",
        width=2,
    )
    for dx, dy, rr in ((-0.020, -0.052, 0.020), (0.0, -0.062, 0.024), (0.020, -0.048, 0.018)):
        draw.ellipse(
            (
                pot_x + width * (dx - rr),
                shelf_y + height * (dy - rr),
                pot_x + width * (dx + rr),
                shelf_y + height * (dy + rr),
            ),
            fill=palette["plant"],
        )
    return canvas


def _room_mid(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    floor_y = int(height * 0.72)
    draw.rectangle((0, floor_y, width, height), fill=palette["floor"])
    board_h = max(6, int(height * 0.026))
    for index, y in enumerate(range(floor_y + board_h, height, board_h)):
        draw.line((0, y, width, y), fill=palette["floor_line"], width=max(1, int(height * 0.0016)))
        seam = (width * 0.18) * (1 if index % 2 == 0 else -1)
        draw.line((width * 0.5 + seam, y - board_h, width * 0.5 + seam, y), fill=palette["floor_line"], width=max(1, int(height * 0.0012)))
    draw.rectangle((0, floor_y - int(height * 0.012), width, floor_y), fill=palette["skirting"])

    rug_w, rug_h = width * 0.66, height * 0.17
    rcx, rcy = width * 0.46, height * 0.89
    draw.ellipse((rcx - rug_w / 2, rcy - rug_h / 2, rcx + rug_w / 2, rcy + rug_h / 2), fill=palette["rug_border"])
    draw.ellipse(
        (rcx - rug_w / 2 + 14, rcy - rug_h / 2 + 10, rcx + rug_w / 2 - 14, rcy + rug_h / 2 - 10),
        fill=palette["rug"],
    )

    tw, th = width * 0.32, height * 0.030
    tcx = float(spec["anchors"]["table"]["x"]) * width
    tcy = float(spec["anchors"]["table"]["y"]) * height
    draw.ellipse((tcx - tw * 0.45, tcy + th + height * 0.070, tcx + tw * 0.45, tcy + th + height * 0.092), fill="#c49a78")
    for sign in (-1, 1):
        leg_x = tcx + sign * (tw / 2 - width * 0.02)
        draw.rectangle((leg_x - 12, tcy + th, leg_x + 12, tcy + th + height * 0.08), fill=palette["table"])
    draw.rectangle((tcx - tw / 2, tcy, tcx + tw / 2, tcy + th), fill=palette["table_top"])
    draw.rectangle((tcx - tw / 2, tcy + th, tcx + tw / 2, tcy + th + height * 0.014), fill=palette["table"])
    draw.line((tcx - tw / 2, tcy, tcx + tw / 2, tcy), fill="#f0c894", width=max(2, int(height * 0.0025)))
    return image


def _room_front(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # soft vignette at the bottom; keeps focus on the actors without a hard bar
    band = int(height * 0.085)
    for index in range(band):
        alpha = int(120 * (index / max(1, band - 1)) ** 2)
        y = height - band + index
        draw.line((0, y, width, y), fill=(18, 24, 34, alpha))
    return image


def _street_back(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    horizon = int(height * 0.62)
    image = _gradient(width, horizon, palette["sky_top"], palette["sky_bottom"]).convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((width * 0.72, height * 0.08, width * 0.86, height * 0.20), fill=palette["sun"])
    for cx, cy, rw, rh in ((0.14, 0.18, 0.10, 0.035), (0.30, 0.12, 0.08, 0.028), (0.55, 0.20, 0.09, 0.030)):
        draw.ellipse(
            (width * (cx - rw), height * (cy - rh), width * (cx + rw), height * (cy + rh)),
            fill=palette["cloud"],
        )
    # distant skyline
    for index, (x, w, h, color) in enumerate(
        (
            (0.00, 0.16, 0.30, palette["building_far"]),
            (0.13, 0.12, 0.38, palette["building_near"]),
            (0.24, 0.18, 0.26, palette["building_far"]),
            (0.40, 0.14, 0.34, palette["building_near"]),
            (0.53, 0.17, 0.24, palette["building_far"]),
            (0.68, 0.13, 0.36, palette["building_near"]),
            (0.80, 0.20, 0.28, palette["building_far"]),
        )
    ):
        left = width * x
        right = width * (x + w)
        top = horizon - height * h
        draw.rectangle((left, top, right, horizon), fill=color)
        for row in range(3):
            for column in range(max(1, int(w / 0.035))):
                wx = left + width * 0.012 + column * width * 0.030
                wy = top + height * 0.030 + row * height * 0.045
                if wx + width * 0.012 < right:
                    draw.rectangle((wx, wy, wx + width * 0.012, wy + height * 0.022), fill=palette["window_light"])
    return canvas


def _street_mid(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    horizon = int(height * 0.62)
    draw.rectangle((0, horizon, width, height), fill=palette["road"])
    draw.rectangle((0, horizon, width, height * 0.70), fill=palette["sidewalk"])
    draw.rectangle((0, height * 0.70, width, height * 0.715), fill=palette["curb"])
    for x in range(0, width, int(width * 0.16)):
        draw.line((x, height * 0.70, x + width * 0.10, height * 0.70), fill=palette["sidewalk_line"], width=3)
    for x in range(int(width * 0.05), width, int(width * 0.24)):
        draw.rectangle((x, height * 0.86, x + width * 0.10, height * 0.875), fill=palette["road_line"])
    # lamp post
    lx = width * float(spec["anchors"]["lamp"]["x"])
    ly = height * float(spec["anchors"]["lamp"]["y"])
    draw.rectangle((lx - width * 0.008, ly - height * 0.30, lx + width * 0.008, ly + height * 0.10), fill=palette["lamp"])
    draw.ellipse((lx - width * 0.035, ly - height * 0.34, lx + width * 0.035, ly - height * 0.28), fill=palette["lamp_light"])
    # bench
    bx = width * float(spec["anchors"]["bench"]["x"])
    by = height * float(spec["anchors"]["bench"]["y"])
    draw.rectangle((bx - width * 0.12, by - height * 0.045, bx + width * 0.12, by), fill=palette["bench"])
    draw.rectangle((bx - width * 0.12, by - height * 0.10, bx + width * 0.12, by - height * 0.075), fill=palette["bench_dark"])
    for sign in (-1, 1):
        draw.rectangle((bx + sign * width * 0.10 - 6, by, bx + sign * width * 0.10 + 6, by + height * 0.07), fill=palette["bench_dark"])
    # planter
    px = width * 0.50
    draw.rectangle((px - width * 0.045, height * 0.78, px + width * 0.045, height * 0.84), fill=palette["plant_pot"])
    for dx, dy, rr in ((-0.02, -0.02, 0.03), (0.02, -0.03, 0.035), (0.0, -0.05, 0.028)):
        draw.ellipse(
            (px + width * (dx - rr), height * 0.80 + height * (dy - rr), px + width * (dx + rr), height * 0.80 + height * (dy + rr)),
            fill=palette["plant"],
        )
    return image


def _street_front(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # foreground railing at the very bottom
    y = height * 0.965
    draw.rectangle((0, y, width, height), fill=palette["railing"])
    for x in range(0, width, int(width * 0.12)):
        draw.rectangle((x, y - height * 0.055, x + width * 0.014, y), fill=palette["railing"])
    draw.rectangle((0, y - height * 0.060, width, y - height * 0.050), fill=palette["railing"])
    return image


def _forest_back(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    horizon = int(height * 0.62)
    image = _gradient(width, horizon, palette["sky_top"], palette["sky_bottom"]).convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(image, (0, 0))
    draw = ImageDraw.Draw(canvas)

    # low glowing horizon band
    band = int(height * 0.06)
    for index in range(band):
        alpha = int(60 * (1.0 - index / max(1, band)))
        y = horizon - band + index
        draw.line((0, y, width, y), fill=_hex(palette["glow_halo"]) + (alpha,))

    # moon upper-right with soft halo
    mx, my, mr = width * 0.78, height * 0.13, width * 0.045
    for r, alpha in ((mr * 3.2, 22), (mr * 2.2, 40), (mr * 1.5, 70)):
        o = int(alpha)
        draw.ellipse((mx - r, my - r, mx + r, my + r), fill=_hex(palette["moon_glow"]) + (o,))
    draw.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=palette["moon"])
    draw.ellipse((mx - mr * 0.55, my - mr * 0.45, mx - mr * 0.05, my + mr * 0.35), fill=_hex(palette["sky_top"]) + (120,))

    # distant tree silhouettes along the horizon
    draw.rectangle((0, horizon, width, height), fill=palette["horizon"])
    for base, scale in ((0.02, 1.0), (0.22, 0.8), (0.50, 1.15), (0.74, 0.9), (0.90, 1.05)):
        bx = base * width
        h = height * 0.16 * scale
        draw.polygon(
            [
                (bx - width * 0.02, horizon),
                (bx - width * 0.008, horizon - h * 0.7),
                (bx, horizon - h),
                (bx + width * 0.008, horizon - h * 0.7),
                (bx + width * 0.02, horizon),
            ],
            fill=palette["trunk_far"],
        )
    return canvas


def _forest_mid(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # ground with a soft vertical gradient
    ground_top = int(height * 0.72)
    for y in range(ground_top, height):
        u = (y - ground_top) / max(1, height - ground_top)
        color = tuple(round(a + (b - a) * u) for a, b in zip(_hex(palette["ground_top"]), _hex(palette["ground_bottom"])))
        draw.line((0, y, width, y), fill=color + (255,))

    # tree trunks on the sides (near layer silhouettes)
    trunk_color = palette["trunk_near"]
    for base, tw, th in ((0.03, 0.05, 0.20), (0.90, 0.06, 0.24)):
        bx = base * width
        tw = tw * width
        th = th * height
        draw.rectangle((bx - tw / 2, ground_top - th, bx + tw / 2, height), fill=trunk_color)
        # a couple of branches
        for bx2, by2, bl2 in ((bx - tw * 1.4, ground_top - th * 0.75, tw * 0.9),
                               (bx + tw * 1.3, ground_top - th * 0.55, tw * 0.7)):
            draw.line((bx, ground_top - th * 0.85, bx2, by2), fill=trunk_color, width=max(4, int(tw * 0.35)))

    # bushes
    for base, w, h, color in ((0.18, 0.24, 0.10, palette["bush_dark"]),
                               (0.55, 0.30, 0.13, palette["bush_darker"]),
                               (0.80, 0.20, 0.09, palette["bush_dark"])):
        bx = base * width
        bw = w * width
        bh = h * height
        draw.ellipse((bx - bw / 2, ground_top - bh, bx + bw / 2, ground_top + bh * 0.3), fill=_hex(color) + (255,))

    # soft glow on the ground where the stone sits
    sx = width * float(spec["anchors"]["stone"]["x"])
    sy = height * float(spec["anchors"]["stone"]["y"])
    for r, alpha in ((width * 0.16, 26), (width * 0.10, 48), (width * 0.06, 90)):
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=_hex(palette["glow_halo"]) + (int(alpha),))
    return image


def _forest_front(width: int, height: int, spec: dict[str, Any]) -> Image.Image:
    palette = spec["palette"]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # dark foreground foliage across the top corners
    branch = palette["branch"]
    for base, flip in ((0.05, 1), (0.95, -1)):
        bx = base * width
        for i in range(6):
            x0 = bx + flip * i * width * 0.012
            y0 = height * 0.02 + i * height * 0.012
            x1 = x0 + flip * width * 0.09
            y1 = y0 + height * 0.02
            draw.line((x0, y0, x1, y1), fill=_hex(branch) + (255,), width=max(5, int(height * 0.006)))
            draw.ellipse((x1 - width * 0.02, y1 - height * 0.012, x1 + width * 0.02, y1 + height * 0.012),
                         fill=_hex(palette["bush_darker"]) + (255,))

    # bottom vignette keeps focus on the actors
    band = int(height * 0.10)
    for index in range(band):
        alpha = int(130 * (index / max(1, band - 1)) ** 2)
        y = height - band + index
        draw.line((0, y, width, y), fill=(8, 12, 16, alpha))

    # sparse fireflies (deterministic positions)
    import random
    rng = random.Random(20261023)
    for _ in range(14):
        fx = rng.uniform(0.1, 0.9) * width
        fy = rng.uniform(0.25, 0.85) * height
        rr = rng.uniform(2, 4)
        draw.ellipse((fx - rr, fy - rr, fx + rr, fy + rr), fill=_hex(palette["firefly"]) + (90,))
    return image


_PAINTERS = {
    ("room", "back"): _room_back,
    ("room", "mid"): _room_mid,
    ("room", "front"): _room_front,
    ("street", "back"): _street_back,
    ("street", "mid"): _street_mid,
    ("street", "front"): _street_front,
    ("forest", "back"): _forest_back,
    ("forest", "mid"): _forest_mid,
    ("forest", "front"): _forest_front,
}


def build_layers(
    name: str,
    width: int,
    height: int,
    overrides: dict[str, Any] | None = None,
) -> list[Layer]:
    spec = load_environment(name)
    if overrides:
        palette = dict(spec["palette"])
        palette.update(overrides.get("palette", {}) or {})
        spec["palette"] = palette
        anchors = dict(spec["anchors"])
        anchors.update(overrides.get("anchors", {}) or {})
        spec["anchors"] = anchors
        for key in ("table_x", "table_y"):
            if key in overrides:
                anchors.setdefault("table", {"x": 0.5, "y": 0.735})
                anchors["table"]["x" if key.endswith("x") else "y"] = float(overrides[key])
    layers: list[Layer] = []
    for layer in spec["layers"]:
        painter = _PAINTERS.get((name, layer["id"]))
        if painter is None:
            raise EnvironmentError(
                f"Окружение <{name}>: неизвестный слой <{layer['id']}>."
            )
        layers.append(Layer(layer["id"], float(layer["depth"]), painter(width, height, spec)))
    return layers


def environment_bounds(name: str) -> tuple[float, float]:
    spec = load_environment(name)
    bounds = spec.get("bounds", {})
    return (float(bounds.get("x_min", 0.05)), float(bounds.get("x_max", 0.95)))
