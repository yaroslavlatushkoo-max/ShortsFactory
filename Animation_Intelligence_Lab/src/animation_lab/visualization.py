"""visualization.py — diagnostic rendering (PNG) and CSV export.

All drawing is diagnostic: trajectories, keyframe markers, prop paths,
attachment points, joint skeletons, scene bounds and detected problem
segments.  Uses matplotlib with the non-interactive Agg backend.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # noqa: S701 - headless rendering, Windows-safe
import matplotlib.pyplot as plt  # noqa: E402

from .models import MotionPlan, ValidationReport  # noqa: E402
from .rig_2d import Rig2D  # noqa: E402
from .simulation import SimulationResult, frame_csv  # noqa: E402
from .vectors import Vec2  # noqa: E402

# Scene is normalized [0,1]^2 with y growing downward: invert the axis so the
# picture matches the screen convention.


def _setup_axes(ax: plt.Axes, width: int, height: int) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)  # inverted: y down
    ax.set_aspect("equal")
    ax.set_facecolor("#f7f7fb")
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.grid(True, color="#dddddd", linewidth=0.6, alpha=0.7)
    ax.set_xlabel("x (normalized)")
    ax.set_ylabel("y (normalized, downward)")
    ax.set_title(
        f"Animation Intelligence Lab — diagnostic (canvas {width}x{height}px)"
    )


def _draw_bounds(ax: plt.Axes) -> None:
    ax.plot(
        [0, 1, 1, 0, 0], [0, 0, 1, 1, 0],
        color="#888888", linewidth=1.4, linestyle="--", label="scene bounds",
    )


def _draw_trajectories(
    ax: plt.Axes,
    result: SimulationResult,
    plan: MotionPlan,
) -> dict[str, Vec2]:
    """Draw per-character paths sampled from the simulation frames."""
    char_colors: dict[str, str] = {}
    palette = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed"]
    last: dict[str, Vec2] = {}
    for k, cid in enumerate(sorted(plan.characters)):
        color = palette[k % len(palette)]
        char_colors[cid] = color
        xs: list[float] = []
        ys: list[float] = []
        for frame in result.frames:
            st = frame.characters.get(cid)
            if st is None:
                continue
            xs.append(st.position.x)
            ys.append(st.position.y)
            last[cid] = (st.position.x, st.position.y)
        if xs:
            ax.plot(xs, ys, color=color, linewidth=1.8, alpha=0.9,
                    label=f"{cid} path")
            # keyframe markers every ~10% of frames
            step = max(1, len(xs) // 10)
            ax.scatter(
                xs[::step], ys[::step], s=14, color=color, zorder=3,
                edgecolors="white", linewidths=0.5,
            )
            ax.scatter([xs[0]], [ys[0]], s=90, marker="o", color=color,
                       zorder=4, edgecolors="black", linewidths=1.2)
            ax.scatter([xs[-1]], [ys[-1]], s=90, marker="s", color=color,
                       zorder=4, edgecolors="black", linewidths=1.2)
            ax.annotate(f"{cid} start", (xs[0], ys[0]), fontsize=7,
                        textcoords="offset points", xytext=(6, 6))
            ax.annotate(f"{cid} end", (xs[-1], ys[-1]), fontsize=7,
                        textcoords="offset points", xytext=(6, 6))
    return char_colors


def _draw_props(
    ax: plt.Axes,
    result: SimulationResult,
    plan: MotionPlan,
) -> None:
    palette = ["#b45309", "#0e7490", "#be185d"]
    for k, pid in enumerate(sorted(plan.props)):
        color = palette[k % len(palette)]
        xs: list[float] = []
        ys: list[float] = []
        attach_pts: list[Vec2] = []
        for frame in result.frames:
            st = frame.props.get(pid)
            if st is None:
                continue
            xs.append(st.position.x)
            ys.append(st.position.y)
            if st.owner_id is not None:
                attach_pts.append((st.position.x, st.position.y))
        if xs:
            ax.plot(xs, ys, color=color, linewidth=1.6, linestyle=":",
                    label=f"{pid} (prop) path")
            ax.scatter([xs[-1]], [ys[-1]], s=70, marker="*", color=color,
                       zorder=5, edgecolors="black", linewidths=0.8)
        if attach_pts:
            ax.scatter(
                [p[0] for p in attach_pts], [p[1] for p in attach_pts],
                s=10, color=color, alpha=0.55, zorder=2,
                label=f"{pid} attachment",
            )


def _draw_issues(ax: plt.Axes, report: ValidationReport) -> None:
    """Mark frames/times where the validator found errors."""
    for issue in report.issues:
        if issue.severity in ("error", "critical") and issue.time is not None:
            ax.axvline(issue.time if issue.time <= 1 else issue.time,
                       color="#ef4444", alpha=0.0)  # time axis not plotted here
    # Marker legend for report status instead.
    if report.issues:
        counts: dict[str, int] = {}
        for i in report.issues:
            counts[i.severity] = counts.get(i.severity, 0) + 1
        text = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        ax.text(
            0.01, 0.02, f"validation — {report.status} ({text})",
            transform=ax.transAxes, fontsize=8,
            bbox=dict(boxstyle="round", facecolor="#fee2e2", alpha=0.9),
        )


def _draw_skeleton(
    ax: plt.Axes,
    rig: Rig2D,
    root: Vec2,
    character_scale: float = 1.0,
    color: str = "#111827",
) -> None:
    """Draw the diagnostic joint/bone stick figure at a root position."""
    world = rig.world_positions()
    bones = [
        ("hip_center", "torso"), ("torso", "neck"), ("neck", "head"),
        ("torso", "shoulder_r"), ("shoulder_r", "elbow_r"),
        ("elbow_r", "hand_r"),
        ("torso", "shoulder_l"), ("shoulder_l", "elbow_l"),
        ("elbow_l", "hand_l"),
        ("hip_center", "hip_r"), ("hip_r", "knee_r"), ("knee_r", "foot_r"),
        ("hip_center", "hip_l"), ("hip_l", "knee_l"), ("knee_l", "foot_l"),
    ]
    from .vectors import add, scale, sub

    pts = {
        jid: add(root, scale(sub(p, (0.5, 0.5)), character_scale))
        for jid, p in world.items()
    }
    for a, b in bones:
        if a in pts and b in pts:
            ax.plot(
                [pts[a][0], pts[b][0]], [pts[a][1], pts[b][1]],
                color=color, linewidth=1.4, alpha=0.75, zorder=4,
            )
            ax.scatter([pts[b][0]], [pts[b][1]], s=9, color=color, zorder=5)


def _draw_skeletons_at_marks(
    ax: plt.Axes,
    result: SimulationResult,
    marks: Sequence[float],
    rig: Rig2D,
) -> None:
    """Stick figures at selected normalized times (diagnostic overlay)."""
    frames = result.frames
    if not frames:
        return
    for m in marks:
        idx = min(len(frames) - 1, int(m * (len(frames) - 1)))
        frame = frames[idx]
        for k, cid in enumerate(sorted(frame.characters)):
            st = frame.characters[cid]
            color = ["#2563eb", "#dc2626", "#059669"][k % 3]
            _draw_skeleton(ax, rig, st.position.as_tuple(), st.scale, color)


def render_diagnostic_png(
    result: SimulationResult,
    plan: MotionPlan,
    output_path: str | Path,
    width: int = 1280,
    height: int = 960,
    report: Optional[ValidationReport] = None,
    rig: Optional[Rig2D] = None,
) -> Path:
    """Render the diagnostic picture and save it as PNG.

    Returns the path of the written file.  Raises RuntimeError when nothing
    was written (matplotlib backend failure etc.).
    """
    if rig is None:
        from .rig_2d import demo_humanoid_rig

        rig = demo_humanoid_rig()
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    try:
        _setup_axes(ax, width, height)
        _draw_bounds(ax)
        _draw_trajectories(ax, result, plan)
        _draw_props(ax, result, plan)
        _draw_skeletons_at_marks(ax, result, (0.0, 0.25, 0.5, 0.75, 1.0), rig)
        if report is not None:
            _draw_issues(ax, report)
        ax.legend(loc="upper right", fontsize=7, framealpha=0.9)
        fig.tight_layout()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, format="png")
    finally:
        plt.close(fig)
    if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
        raise RuntimeError(f"failed to write PNG to {output_path}")
    return Path(output_path)


def save_frames_csv(
    result: SimulationResult,
    output_path: str | Path,
) -> Path:
    """Save the sampled frames as CSV (positions per frame)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = frame_csv(result)
    out.write_text(text, encoding="utf-8")
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"failed to write CSV to {output_path}")
    return out


def render_frame_strip(
    result: SimulationResult,
    output_path: str | Path,
    times: Sequence[float],
    width: int = 1600,
) -> Path:
    """Local frame viewer: a strip of simulated frames at given times."""
    n = len(times)
    if n == 0:
        raise ValueError("times must be non-empty")
    fig, axes = plt.subplots(1, n, figsize=(width / 100, 3.2), dpi=100)
    if n == 1:
        axes = [axes]
    for ax, t in zip(axes, times):
        _setup_axes(ax, width // n, 320)
        _draw_bounds(ax)
        idx = min(len(result.frames) - 1, int(round(t * result.fps)))
        frame = result.frames[idx]
        for k, cid in enumerate(sorted(frame.characters)):
            color = ["#2563eb", "#dc2626", "#059669"][k % 3]
            st = frame.characters[cid]
            ax.scatter([st.position.x], [st.position.y], s=120, color=color,
                       zorder=5, edgecolors="black")
            ax.annotate(f"{cid}@{t:.2f}s", (st.position.x, st.position.y),
                        fontsize=7, textcoords="offset points", xytext=(5, 5))
        for pid, st in frame.props.items():
            ax.scatter([st.position.x], [st.position.y], s=60, marker="*",
                       color="#b45309", zorder=5, edgecolors="black")
    fig.suptitle("Simulation frames (calculated states, not random art)")
    fig.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png")
    plt.close(fig)
    return out


def export_positions_csv(plan: MotionPlan, output_path: str | Path) -> Path:
    """Export keyframe character positions as CSV via the csv module."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["time_s", "character_id", "x_normalized", "y_normalized",
             "rotation_deg", "action"]
        )
        for frame in plan.keyframes:
            for cid in sorted(frame.characters):
                st = frame.characters[cid]
                writer.writerow(
                    [f"{frame.time:.4f}", cid, f"{st.position.x:.6f}",
                     f"{st.position.y:.6f}", f"{st.rotation:.3f}",
                     st.current_action or ""]
                )
    return out
