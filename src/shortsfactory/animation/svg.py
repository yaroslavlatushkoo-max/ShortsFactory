"""Small, dependency-free SVG subset renderer used by the v3 art system.

The animation engine ships its characters as ordinary ``.svg`` files so the
artwork is editable in any vector editor and versioned separately from the
Python code.  A full SVG implementation would be overkill: the assets only use
a deliberately small subset of the format, which this module rasterises with
Pillow at a supersampled resolution.

Supported: ``svg``, ``g``, ``path``, ``ellipse``, ``circle``, ``rect``,
``polygon``, ``polyline``, ``line``; ``fill``/``stroke``/``stroke-width``/
``opacity``/``fill-opacity``/``stroke-opacity``; ``transform`` with
``translate``/``scale``/``rotate``/``matrix``; solid colours, ``none`` and
``url(#gradient)`` with ``linearGradient``/``radialGradient`` stops;
``{{palette_token}}`` placeholders inside colour values.

Unsupported constructs raise :class:`SvgError` instead of being silently
ignored, so a broken asset is reported with its file name and element id.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageColor, ImageDraw

Matrix = tuple[float, float, float, float, float, float]
Point = tuple[float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TOKEN = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]|" + _NUMBER.pattern)


class SvgError(ValueError):
    """Raised when an asset uses an unsupported or malformed SVG construct."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ----------------------------------------------------------------- transforms


def _mul(a: Matrix, b: Matrix) -> Matrix:
    """Return ``a * b`` (apply ``b`` first, then ``a``)."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a2 * b1,
        a1 * b0 + a3 * b1,
        a0 * b2 + a2 * b3,
        a1 * b2 + a3 * b3,
        a0 * b4 + a2 * b5 + a4,
        a1 * b4 + a3 * b5 + a5,
    )


def _apply(m: Matrix, x: float, y: float) -> Point:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _translate(tx: float, ty: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _scale(sx: float, sy: float) -> Matrix:
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def _rotate(angle: float, cx: float = 0.0, cy: float = 0.0) -> Matrix:
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    return (c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy)


def _parse_transform(text: str) -> Matrix:
    matrix = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        values = [float(v) for v in _NUMBER.findall(args)]
        if name == "translate":
            m = _translate(values[0], values[1] if len(values) > 1 else 0.0)
        elif name == "scale":
            m = _scale(values[0], values[1] if len(values) > 1 else values[0])
        elif name == "rotate":
            m = _rotate(values[0], *(values[1:3] if len(values) >= 3 else (0.0, 0.0)))
        elif name == "matrix" and len(values) == 6:
            m = tuple(values)  # type: ignore[assignment]
        else:
            raise SvgError(f"Неподдерживаемая трансформация SVG: {name}({args})")
        matrix = _mul(matrix, m)
    return matrix


# ---------------------------------------------------------------- path parsing


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 14) -> list[Point]:
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def _flatten_quad(p0: Point, p1: Point, p2: Point, steps: int = 10) -> list[Point]:
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        out.append((mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
                    mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]))
    return out


def _flatten_arc(p0: Point, rx: float, ry: float, rotation: float, large: bool,
                 sweep: bool, p1: Point) -> list[Point]:
    """Endpoint -> centre parameterisation, approximated by a polyline."""
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-6 or ry < 1e-6:
        return [p1]
    phi = math.radians(rotation)
    dx, dy = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1 = math.cos(phi) * dx + math.sin(phi) * dy
    y1 = -math.sin(phi) * dx + math.cos(phi) * dy
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1.0:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cx1 = coef * rx * y1 / ry
    cy1 = -coef * ry * x1 / rx
    cx = math.cos(phi) * cx1 - math.sin(phi) * cy1 + (p0[0] + p1[0]) / 2.0
    cy = math.sin(phi) * cx1 + math.cos(phi) * cy1 + (p0[1] + p1[1]) / 2.0
    theta1 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    theta2 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    delta = theta2 - theta1
    if sweep and delta < 0:
        delta += 2 * math.pi
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    steps = max(6, int(abs(delta) / (math.pi / 18)))
    out: list[Point] = []
    for i in range(1, steps + 1):
        a = theta1 + delta * i / steps
        x = rx * math.cos(a)
        y = ry * math.sin(a)
        out.append((math.cos(phi) * x - math.sin(phi) * y + cx,
                    math.sin(phi) * x + math.cos(phi) * y + cy))
    return out


@dataclass
class SubPath:
    points: list[Point]
    closed: bool


def parse_path(data: str) -> list[SubPath]:
    tokens = _TOKEN.findall(data or "")
    subpaths: list[SubPath] = []
    points: list[Point] = []
    closed = False
    current: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_control: Point | None = None
    command = ""
    index = 0

    def flush() -> None:
        nonlocal points, closed
        if len(points) >= 2:
            subpaths.append(SubPath(points, closed))
        points = []
        closed = False

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                closed = True
                flush()
                current = start
                last_control = None
                continue
        elif not command:
            raise SvgError("Путь SVG начинается не с команды.")
        relative = command.islower()
        cmd = command.upper()

        def number() -> float:
            nonlocal index
            if index >= len(tokens) or tokens[index].isalpha():
                raise SvgError(f"Недостаточно чисел в пути SVG (команда {command}).")
            value = float(tokens[index])
            index += 1
            return value

        if cmd == "M":
            x, y = number(), number()
            if relative:
                x, y = current[0] + x, current[1] + y
            flush()
            current = (x, y)
            start = current
            points = [current]
            command = "l" if relative else "L"
            last_control = None
        elif cmd == "L":
            x, y = number(), number()
            if relative:
                x, y = current[0] + x, current[1] + y
            current = (x, y)
            points.append(current)
            last_control = None
        elif cmd == "H":
            x = number()
            if relative:
                x += current[0]
            current = (x, current[1])
            points.append(current)
            last_control = None
        elif cmd == "V":
            y = number()
            if relative:
                y += current[1]
            current = (current[0], y)
            points.append(current)
            last_control = None
        elif cmd == "C":
            values = [number() for _ in range(6)]
            c1 = (values[0], values[1])
            c2 = (values[2], values[3])
            end = (values[4], values[5])
            if relative:
                c1 = (current[0] + c1[0], current[1] + c1[1])
                c2 = (current[0] + c2[0], current[1] + c2[1])
                end = (current[0] + end[0], current[1] + end[1])
            points.extend(_flatten_cubic(current, c1, c2, end))
            current = end
            last_control = c2
        elif cmd == "S":
            values = [number() for _ in range(4)]
            c1 = (2 * current[0] - last_control[0], 2 * current[1] - last_control[1]) \
                if last_control else current
            c2 = (values[0], values[1])
            end = (values[2], values[3])
            if relative:
                c2 = (current[0] + c2[0], current[1] + c2[1])
                end = (current[0] + end[0], current[1] + end[1])
            points.extend(_flatten_cubic(current, c1, c2, end))
            current = end
            last_control = c2
        elif cmd == "Q":
            values = [number() for _ in range(4)]
            c = (values[0], values[1])
            end = (values[2], values[3])
            if relative:
                c = (current[0] + c[0], current[1] + c[1])
                end = (current[0] + end[0], current[1] + end[1])
            points.extend(_flatten_quad(current, c, end))
            current = end
            last_control = c
        elif cmd == "T":
            values = [number() for _ in range(2)]
            c = (2 * current[0] - last_control[0], 2 * current[1] - last_control[1]) \
                if last_control else current
            end = (values[0], values[1])
            if relative:
                end = (current[0] + end[0], current[1] + end[1])
            points.extend(_flatten_quad(current, c, end))
            current = end
            last_control = c
        elif cmd == "A":
            values = [number() for _ in range(7)]
            end = (values[5], values[6])
            if relative:
                end = (current[0] + end[0], current[1] + end[1])
            points.extend(_flatten_arc(current, values[0], values[1], values[2],
                                       bool(values[3]), bool(values[4]), end))
            current = end
            last_control = None
        else:
            raise SvgError(f"Неподдерживаемая команда пути SVG: {command}")
    flush()
    if not subpaths:
        raise SvgError("Пустой путь SVG.")
    return subpaths


# ------------------------------------------------------------------- elements


@dataclass
class Shape:
    kind: str
    attrs: dict[str, str]
    transform: Matrix


@dataclass
class Style:
    fill: str = "black"
    stroke: str = "none"
    stroke_width: float = 1.0
    opacity: float = 1.0
    fill_opacity: float = 1.0
    stroke_opacity: float = 1.0
    fill_rule: str = "nonzero"


def _style(attrs: dict[str, str], inherited: Style) -> Style:
    style = Style(
        fill=attrs.get("fill", inherited.fill),
        stroke=attrs.get("stroke", inherited.stroke),
        stroke_width=float(attrs["stroke-width"]) if "stroke-width" in attrs else inherited.stroke_width,
        opacity=float(attrs["opacity"]) if "opacity" in attrs else inherited.opacity,
        fill_opacity=float(attrs["fill-opacity"]) if "fill-opacity" in attrs else inherited.fill_opacity,
        stroke_opacity=float(attrs["stroke-opacity"]) if "stroke-opacity" in attrs else inherited.stroke_opacity,
        fill_rule=attrs.get("fill-rule", inherited.fill_rule),
    )
    return style


def _element_shapes(element: ET.Element, transform: Matrix, style: Style,
                    offsets: dict[str, Point], out: list[tuple[Shape, Style]],
                    svg_root: ET.Element) -> None:
    tag = _local(element.tag)
    attrs = {k: v for k, v in element.attrib.items() if not k.startswith("xmlns")}
    if tag in ("defs", "linearGradient", "radialGradient", "stop", "title", "desc", "metadata"):
        return
    local_transform = _parse_transform(attrs.get("transform", ""))
    if element.attrib.get("id") in offsets:
        dx, dy = offsets[element.attrib["id"]]
        local_transform = _mul(_translate(dx, dy), local_transform)
    matrix = _mul(transform, local_transform)
    current = _style(attrs, style)
    if tag == "g":
        for child in element:
            _element_shapes(child, matrix, current, offsets, out, svg_root)
        return
    if tag == "svg":
        for child in element:
            _element_shapes(child, matrix, current, offsets, out, svg_root)
        return
    if tag in ("path", "ellipse", "circle", "rect", "polygon", "polyline", "line"):
        out.append((Shape(tag, attrs, matrix), current))
        return
    if tag in ("text", "image", "use", "filter", "clipPath", "mask", "pattern"):
        raise SvgError(f"Неподдерживаемый элемент SVG: <{tag}>")
    # Unknown elements are ignored only when they carry no geometry.
    if list(element):
        for child in element:
            _element_shapes(child, matrix, current, offsets, out, svg_root)


def _shape_subpaths(shape: Shape) -> list[SubPath]:
    a = shape.attrs
    if shape.kind == "path":
        return parse_path(a.get("d", ""))
    if shape.kind == "ellipse":
        cx, cy = float(a.get("cx", 0)), float(a.get("cy", 0))
        rx, ry = float(a.get("rx", 0)), float(a.get("ry", 0))
        return [_ellipse_path(cx, cy, rx, ry)]
    if shape.kind == "circle":
        cx, cy, r = float(a.get("cx", 0)), float(a.get("cy", 0)), float(a.get("r", 0))
        return [_ellipse_path(cx, cy, r, r)]
    if shape.kind == "rect":
        x, y = float(a.get("x", 0)), float(a.get("y", 0))
        w, h = float(a.get("width", 0)), float(a.get("height", 0))
        rx = float(a.get("rx", 0) or 0)
        ry = float(a.get("ry", 0) or rx)
        if rx <= 0 and ry <= 0:
            return [SubPath([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
        rx = min(rx, w / 2)
        ry = min(ry, h / 2)
        pts: list[Point] = []
        for cx, cy, start in ((x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0),
                              (x + rx, y + h - ry, 90), (x + rx, y + ry, 180)):
            for i in range(9):
                ang = math.radians(start + i * 90 / 8)
                pts.append((cx + rx * math.cos(ang), cy + ry * math.sin(ang)))
        return [SubPath(pts, True)]
    if shape.kind in ("polygon", "polyline"):
        values = [float(v) for v in _NUMBER.findall(a.get("points", ""))]
        pts = list(zip(values[0::2], values[1::2]))
        if shape.kind == "polygon" and pts:
            return [SubPath(pts, True)]
        return [SubPath(pts, False)]
    if shape.kind == "line":
        return [SubPath([(float(a.get("x1", 0)), float(a.get("y1", 0))),
                         (float(a.get("x2", 0)), float(a.get("y2", 0)))], False)]
    raise SvgError(f"Неподдерживаемая фигура SVG: {shape.kind}")


def _ellipse_path(cx: float, cy: float, rx: float, ry: float, steps: int = 48) -> SubPath:
    pts = [(cx + rx * math.cos(2 * math.pi * i / steps),
            cy + ry * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return SubPath(pts, True)


# -------------------------------------------------------------------- gradients


@dataclass
class Gradient:
    kind: str
    attrs: dict[str, str]
    stops: list[tuple[float, str, float]]


def _gradients(root: ET.Element) -> dict[str, Gradient]:
    found: dict[str, Gradient] = {}
    for element in root.iter():
        tag = _local(element.tag)
        if tag not in ("linearGradient", "radialGradient"):
            continue
        gid = element.attrib.get("id")
        if not gid:
            continue
        stops: list[tuple[float, str, float]] = []
        for stop in element:
            if _local(stop.tag) != "stop":
                continue
            offset = stop.attrib.get("offset", "0")
            offset = float(offset[:-1]) / 100.0 if offset.endswith("%") else float(offset)
            color = stop.attrib.get("stop-color", "#000000")
            opacity = float(stop.attrib.get("stop-opacity", "1"))
            stops.append((offset, color, opacity))
        if len(stops) >= 2:
            found[gid] = Gradient(tag, dict(element.attrib), stops)
    return found


def _gradient_image(gradient: Gradient, bbox: tuple[int, int, int, int], size: tuple[int, int],
                    palette: dict[str, str]) -> Image.Image:
    """Render a gradient over the given device bbox; returns an RGBA image."""
    width, height = size
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    image = Image.new("RGBA", (bw, bh))
    pixels = image.load()
    a = gradient.attrs
    if gradient.kind == "linearGradient":
        gx1 = _coord(a.get("x1", "0%"), bw, 0.0)
        gy1 = _coord(a.get("y1", "0%"), bh, 0.0)
        gx2 = _coord(a.get("x2", "100%"), bw, 1.0)
        gy2 = _coord(a.get("y2", "0%"), bh, 0.0)
        dx, dy = gx2 - gx1, gy2 - gy1
        length2 = dx * dx + dy * dy or 1.0
        for y in range(bh):
            for x in range(bw):
                t = ((x - gx1) * dx + (y - gy1) * dy) / length2
                pixels[x, y] = _sample(gradient.stops, t, palette)
    else:
        cx = _coord(a.get("cx", "50%"), bw, 0.5)
        cy = _coord(a.get("cy", "50%"), bh, 0.5)
        r = max(_coord(a.get("r", "50%"), max(bw, bh), 0.5), 1.0)
        for y in range(bh):
            for x in range(bw):
                t = math.hypot(x - cx, y - cy) / r
                pixels[x, y] = _sample(gradient.stops, t, palette)
    return image


def _coord(value: str, extent: int, default_fraction: float) -> float:
    if value.endswith("%"):
        return float(value[:-1]) / 100.0 * extent
    try:
        return float(value)
    except ValueError:
        return default_fraction * extent


def _sample(stops: Sequence[tuple[float, str, float]], t: float,
            palette: dict[str, str]) -> tuple[int, int, int, int]:
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        o0, c0, a0 = stops[i]
        o1, c1, a1 = stops[i + 1]
        if o0 <= t <= o1:
            f = 0.0 if o1 == o0 else (t - o0) / (o1 - o0)
            rgb0 = ImageColor.getrgb(_resolve_color(c0, palette))
            rgb1 = ImageColor.getrgb(_resolve_color(c1, palette))
            return (
                round(rgb0[0] + (rgb1[0] - rgb0[0]) * f),
                round(rgb0[1] + (rgb1[1] - rgb0[1]) * f),
                round(rgb0[2] + (rgb1[2] - rgb0[2]) * f),
                round((a0 + (a1 - a0) * f) * 255),
            )
    o, c, alpha = stops[-1] if t > stops[-1][0] else stops[0]
    rgb = ImageColor.getrgb(_resolve_color(c, palette))
    return (rgb[0], rgb[1], rgb[2], round(alpha * 255))


def _resolve_color(value: str, palette: dict[str, str]) -> str:
    def token(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in palette:
            raise SvgError("В палитре нет цвета {%s}." % key)
        return str(palette[key])

    resolved = re.sub(r"\{\{(\w+)\}\}", token, value)
    if resolved.startswith("url("):
        return "#000000"
    if resolved == "none" or resolved == "transparent":
        return "#000000"
    try:
        ImageColor.getrgb(resolved)
    except ValueError as exc:
        raise SvgError(f"Неподдерживаемый цвет SVG: {value}") from exc
    return resolved


# --------------------------------------------------------------------- rendering


def _find_group(root: ET.Element, group: str) -> ET.Element | None:
    for element in root.iter():
        if element.attrib.get("id") == group:
            return element
    return None


def list_groups(svg_text: str) -> list[str]:
    """Return every ``id`` declared in an SVG document."""
    root = ET.fromstring(svg_text)
    return [element.attrib["id"] for element in root.iter() if "id" in element.attrib]


def _paint_polygon(canvas: Image.Image, pts: Sequence[tuple[float, float]],
                   rgba: tuple[int, int, int, int], alpha: float) -> None:
    """Fill a polygon, alpha-compositing translucent shapes instead of
    overwriting the pixels underneath (Pillow's ImageDraw replaces RGBA)."""
    if alpha >= 0.999:
        ImageDraw.Draw(canvas).polygon(pts, fill=rgba)
        return
    x0 = max(0, int(math.floor(min(p[0] for p in pts))) - 1)
    y0 = max(0, int(math.floor(min(p[1] for p in pts))) - 1)
    x1 = min(canvas.width, int(math.ceil(max(p[0] for p in pts))) + 2)
    y1 = min(canvas.height, int(math.ceil(max(p[1] for p in pts))) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon([(p[0] - x0, p[1] - y0) for p in pts], fill=rgba)
    canvas.alpha_composite(layer, (x0, y0))


def _paint_stroke(canvas: Image.Image, pts: Sequence[tuple[float, float]],
                  rgba: tuple[int, int, int, int], width: int, alpha: float) -> None:
    radius = width / 2.0
    if alpha >= 0.999:
        draw = ImageDraw.Draw(canvas)
        draw.line(pts, fill=rgba, width=width, joint="curve")
        for point in (pts[0], pts[-1]):
            draw.ellipse((point[0] - radius, point[1] - radius,
                          point[0] + radius, point[1] + radius), fill=rgba)
        return
    pad = int(math.ceil(width)) + 2
    x0 = max(0, int(math.floor(min(p[0] for p in pts))) - pad)
    y0 = max(0, int(math.floor(min(p[1] for p in pts))) - pad)
    x1 = min(canvas.width, int(math.ceil(max(p[0] for p in pts))) + pad)
    y1 = min(canvas.height, int(math.ceil(max(p[1] for p in pts))) + pad)
    if x1 <= x0 or y1 <= y0:
        return
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    local = [(p[0] - x0, p[1] - y0) for p in pts]
    draw.line(local, fill=rgba, width=width, joint="curve")
    for point in (local[0], local[-1]):
        draw.ellipse((point[0] - radius, point[1] - radius,
                      point[0] + radius, point[1] + radius), fill=rgba)
    canvas.alpha_composite(layer, (x0, y0))


def render_svg(
    svg_text: str,
    size: tuple[int, int],
    palette: dict[str, str] | None = None,
    group: str | None = None,
    offsets: dict[str, tuple[float, float]] | None = None,
    supersample: int = 3,
    background: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """Rasterise an SVG document (or a single group of it) into an RGBA image."""
    palette = palette or {}
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise SvgError(f"Повреждённый SVG: {exc}") from exc
    if _local(root.tag) != "svg":
        raise SvgError("Корневой элемент SVG должен быть <svg>.")
    view = root.attrib.get("viewBox", "")
    values = [float(v) for v in _NUMBER.findall(view)]
    if len(values) != 4:
        raise SvgError("В SVG отсутствует корректный viewBox.")
    vx, vy, vw, vh = values
    if vw <= 0 or vh <= 0:
        raise SvgError("Некорректный viewBox SVG.")
    scale = min(width / vw, height / vh)
    pad_x = (width - vw * scale) / 2.0
    pad_y = (height - vh * scale) / 2.0
    base = _mul(_translate(pad_x, pad_y), _mul(_scale(scale, scale), _translate(-vx, -vy)))

    start: ET.Element = root
    if group:
        found = _find_group(root, group)
        if found is None:
            raise SvgError(f"В SVG нет группы «{group}».")
        start = found
        base = _mul(base, _ancestor_transform(root, found))

    shapes: list[tuple[Shape, Style]] = []
    _element_shapes(start, base, Style(), offsets or {}, shapes, root)
    if not shapes:
        raise SvgError(f"Группа «{group}» не содержит графики." if group else "SVG не содержит графики.")

    canvas = Image.new("RGBA", (width * supersample, height * supersample),
                       background or (0, 0, 0, 0))
    gradients = _gradients(root)
    ss_matrix = _scale(supersample, supersample)

    for shape, style in shapes:
        matrix = _mul(ss_matrix, shape.transform)
        subpaths = _shape_subpaths(shape)
        alpha = style.opacity * style.fill_opacity
        fill = style.fill
        stroke = style.stroke
        if fill.startswith("url("):
            gid = fill[5:-1].strip("#) ")
            gradient = gradients.get(gid)
            if gradient is None:
                raise SvgError(f"Градиент «{gid}» не найден.")
            for subpath in subpaths:
                pts = [_apply(matrix, *p) for p in subpath.points]
                bbox = (int(min(p[0] for p in pts)), int(min(p[1] for p in pts)),
                        int(max(p[0] for p in pts)) + 1, int(max(p[1] for p in pts)) + 1)
                mask = Image.new("L", (width * supersample, height * supersample), 0)
                ImageDraw.Draw(mask).polygon(pts, fill=255)
                layer = _gradient_image(gradient, bbox, mask.size, palette)
                canvas.paste(layer, (bbox[0], bbox[1]), mask.crop(bbox))
        elif fill != "none":
            color = _resolve_color(fill, palette)
            rgba = ImageColor.getrgb(color) + (round(alpha * 255),)
            for subpath in subpaths:
                pts = [_apply(matrix, *p) for p in subpath.points]
                if len(pts) >= 3:
                    _paint_polygon(canvas, pts, rgba, alpha)

        if stroke != "none" and style.stroke_width > 0:
            color = _resolve_color(stroke, palette)
            stroke_alpha = style.opacity * style.stroke_opacity
            rgba = ImageColor.getrgb(color) + (round(stroke_alpha * 255),)
            line_width = max(1, round(style.stroke_width * scale * supersample))
            for subpath in subpaths:
                pts = [_apply(matrix, *p) for p in subpath.points]
                if subpath.closed:
                    pts = pts + [pts[0]]
                if len(pts) >= 2:
                    _paint_stroke(canvas, pts, rgba, line_width, stroke_alpha)

    return canvas.resize((width, height), Image.LANCZOS)


def _ancestor_transform(root: ET.Element, target: ET.Element) -> Matrix:
    chain: list[ET.Element] = []

    def walk(element: ET.Element, trail: list[ET.Element]) -> bool:
        if element is target:
            chain.extend(trail)
            return True
        for child in element:
            if walk(child, trail + [element]):
                return True
        return False

    walk(root, [])
    matrix = IDENTITY
    for element in chain:
        if _local(element.tag) in ("svg", "g"):
            matrix = _mul(matrix, _parse_transform(element.attrib.get("transform", "")))
    return matrix


@lru_cache(maxsize=64)
def load_svg(path: str) -> str:
    """Read an SVG file with a useful error message."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SvgError(f"Не удалось прочитать ассет {path}: {exc}") from exc
