"""Time-dependent character paths shared by the renderer and storyboard compiler.

Animation Intelligence Lab owns the trajectory geometry.  The Factory accepts a
small, explicit path document in an action's ``params`` so the same geometry can
be evaluated at every rendered frame instead of being reduced to a final x.

Coordinate system: normalized stage coordinates, origin at the top-left, x/y in
[0, 1], y grows downwards.  Character ``y`` remains the floor line in the scene;
a path's y is converted to a bounded hip offset (``path_lift``), because the
front-view rig cannot turn a vertical path into a true 3D step.
"""

from __future__ import annotations

from typing import Any, Iterable

PATH_KINDS = ("linear", "hold", "bezier3", "waypoints", "arc")
PATH_EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out")
ROOT_SCENE_Y = 0.65
MAX_LIFT = 0.12


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def apply_easing(t: float, easing: str | None) -> float:
    t = _clamp(t)
    if easing == "ease_in":
        return t * t
    if easing == "ease_out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if easing == "ease_in_out":
        return t * t * (3.0 - 2.0 * t)
    return t


def _point(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {"x": _clamp(float(value["x"])), "y": _clamp(float(value["y"]))}
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return {"x": _clamp(float(value[0])), "y": _clamp(float(value[1]))}
    raise ValueError(f"Некорректная точка траектории: {value!r}")


def normalize_path(raw: Any) -> dict[str, Any]:
    """Validate and normalize a path document; raises ``ValueError``."""
    if not isinstance(raw, dict):
        raise ValueError("path должен быть объектом")
    kind = str(raw.get("kind", "linear"))
    if kind not in PATH_KINDS:
        raise ValueError(f"Неизвестный тип траектории: {kind}")
    easing = str(raw.get("easing", "linear"))
    if easing not in PATH_EASINGS:
        raise ValueError(f"Неизвестный easing: {easing}")
    points = [_point(item) for item in raw.get("points", [])]
    if kind == "hold":
        if not points:
            raise ValueError("hold требует хотя бы одну точку")
        points = points[:1]
    elif kind == "bezier3":
        if len(points) == 3:
            points = [points[0], points[1], points[2], points[0]]
        if len(points) != 4:
            raise ValueError("bezier3 требует 3 или 4 точки (p0,p1,p2[,p3])")
        points[-1] = points[-1]
    elif kind in {"linear", "arc"}:
        if len(points) != 2:
            raise ValueError(f"{kind} требует две точки")
    elif kind == "waypoints":
        if len(points) < 2:
            raise ValueError("waypoints требует минимум две точки")
    arc_height = max(0.0, min(0.35, float(raw.get("arc_height", 0.06))))
    return {
        "kind": kind,
        "points": points,
        "easing": easing,
        "arc_height": arc_height,
        "source": str(raw.get("source", "manual")),
    }


def _quadratic(p0: dict[str, float], control: dict[str, float], p1: dict[str, float], t: float) -> dict[str, float]:
    mt = 1.0 - t
    return {
        "x": _clamp(mt * mt * p0["x"] + 2 * mt * t * control["x"] + t * t * p1["x"]),
        "y": _clamp(mt * mt * p0["y"] + 2 * mt * t * control["y"] + t * t * p1["y"]),
    }


def _bezier(points: list[dict[str, float]], t: float) -> dict[str, float]:
    p0, p1, p2, p3 = points
    mt = 1.0 - t
    return {
        "x": _clamp(mt**3 * p0["x"] + 3 * mt * mt * t * p1["x"] + 3 * mt * t * t * p2["x"] + t**3 * p3["x"]),
        "y": _clamp(mt**3 * p0["y"] + 3 * mt * mt * t * p1["y"] + 3 * mt * t * t * p2["y"] + t**3 * p3["y"]),
    }


def position_at(raw_path: Any, u: float) -> dict[str, float] | None:
    """Evaluate a path at normalized time ``u``; returns ``None`` if absent."""
    if not raw_path:
        return None
    path = normalize_path(raw_path)
    t = apply_easing(u, path["easing"])
    points = path["points"]
    kind = path["kind"]
    if kind == "hold":
        return dict(points[0])
    if kind == "linear":
        p0, p1 = points
        return {
            "x": _clamp(p0["x"] + (p1["x"] - p0["x"]) * t),
            "y": _clamp(p0["y"] + (p1["y"] - p0["y"]) * t),
        }
    if kind == "bezier3":
        return _bezier(points, t)
    if kind == "waypoints":
        scaled = t * (len(points) - 1)
        index = min(len(points) - 2, int(scaled))
        local = scaled - index
        p0, p1 = points[index], points[index + 1]
        return {
            "x": _clamp(p0["x"] + (p1["x"] - p0["x"]) * local),
            "y": _clamp(p0["y"] + (p1["y"] - p0["y"]) * local),
        }
    # arc: quadratic lift above a straight baseline
    p0, p1 = points
    control = {
        "x": (p0["x"] + p1["x"]) / 2,
        "y": min(p0["y"], p1["y"]) - path["arc_height"],
    }
    return _quadratic(p0, control, p1, t)


def path_metrics(raw_path: Any, samples: int = 65) -> dict[str, Any]:
    """Return geometric metrics for QC and for comparing animation profiles."""
    path = normalize_path(raw_path)
    points = path["points"]
    start = points[0]
    end = points[-1]
    sampled = [position_at(path, index / max(1, samples - 1)) for index in range(samples)]
    sampled = [item for item in sampled if item is not None]
    length = 0.0
    for left, right in zip(sampled, sampled[1:]):
        length += ((right["x"] - left["x"]) ** 2 + (right["y"] - left["y"]) ** 2) ** 0.5
    chord = ((end["x"] - start["x"]) ** 2 + (end["y"] - start["y"]) ** 2) ** 0.5
    max_deviation = 0.0
    if chord > 1e-9:
        dx = end["x"] - start["x"]
        dy = end["y"] - start["y"]
        for point in sampled:
            distance = abs(dx * (point["y"] - start["y"]) - dy * (point["x"] - start["x"])) / chord
            max_deviation = max(max_deviation, distance)
    evaluated = position_at(path, 1.0) or end
    endpoint_error = ((evaluated["x"] - end["x"]) ** 2 + (evaluated["y"] - end["y"]) ** 2) ** 0.5
    return {
        "kind": path["kind"],
        "easing": path["easing"],
        "start": start,
        "end": end,
        "chord_length": round(chord, 6),
        "path_length": round(length, 6),
        "curvature_ratio": round(length / chord, 6) if chord > 1e-9 else 1.0,
        "max_deviation": round(max_deviation, 6),
        "endpoint_error": round(endpoint_error, 8),
        "samples": samples,
    }


def path_lift(y: float) -> float:
    """Convert path scene y to a bounded hip offset in stage-height units."""
    return max(-MAX_LIFT, min(MAX_LIFT, (ROOT_SCENE_Y - float(y)) * 0.35))


def endpoint(raw_path: Any) -> dict[str, float] | None:
    if not raw_path:
        return None
    path = normalize_path(raw_path)
    return dict(path["points"][-1])


def path_from_lab_segment(
    start: dict[str, float] | Iterable[float],
    end: dict[str, float] | Iterable[float],
    trajectory: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert a Lab MotionSegment trajectory to the Factory path document."""
    start_point = _point(start)
    end_point = _point(end)
    trajectory = trajectory or {}
    kind = str(trajectory.get("kind", "linear"))
    controls = [_point(item) for item in trajectory.get("control_points", [])]
    easing = str(trajectory.get("easing", "linear"))
    if kind == "bezier3":
        if not controls:
            return normalize_path({"kind": "linear", "points": [start_point, end_point], "easing": easing})
        p1 = controls[0]
        p2 = controls[1] if len(controls) > 1 else controls[0]
        return normalize_path(
            {"kind": "bezier3", "points": [start_point, p1, p2, end_point], "easing": easing}
        )
    if kind == "waypoints":
        points = [start_point, *controls, end_point]
        if len(points) < 3:
            return normalize_path({"kind": "linear", "points": [start_point, end_point], "easing": easing})
        return normalize_path({"kind": "waypoints", "points": points, "easing": easing})
    if kind == "arc":
        lift = 0.06
        if controls:
            lift = max(0.02, min(0.30, start_point["y"] - controls[0]["y"]))
        return normalize_path(
            {
                "kind": "arc",
                "points": [start_point, end_point],
                "easing": easing,
                "arc_height": lift,
            }
        )
    if kind == "hold" or start_point == end_point:
        return normalize_path({"kind": "hold", "points": [start_point], "easing": easing})
    return normalize_path({"kind": "linear", "points": [start_point, end_point], "easing": easing})
