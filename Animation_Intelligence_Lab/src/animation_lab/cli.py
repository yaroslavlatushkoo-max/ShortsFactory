"""cli.py — Typer-based command line interface.

Commands:
    animation-lab validate <scene.json>
    animation-lab simulate <scene.json> --fps 30
    animation-lab plot <scene.json> --output out.png
    animation-lab export <scene.json> --format json|csv
    animation-lab report <scene.json> --output report.json
    animation-lab example --output example.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .exporter import (
    KeyframeListAdapter,
    export_json,
    import_scene,
    validate_against_schema,
)
from .models import SCHEMA_VERSION
from .motion_planner import build_plan
from .simulation import Simulator
from .validation import validate_plan
from .visualization import render_diagnostic_png, save_frames_csv

app = typer.Typer(
    name="animation-lab",
    help="Animation Intelligence Lab — character motion planning, "
         "simulation and validation.",
    no_args_is_help=True,
    add_completion=False,
)

DEFAULT_SCHEMA = Path(__file__).parent.parent.parent / "schemas" / "motion_plan.schema.json"


def _load_scene(path: Path):
    try:
        return import_scene(path)
    except FileNotFoundError:
        typer.secho(f"error: file not found: {path}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from None
    except Exception as err:  # pydantic ValidationError and friends
        typer.secho(f"error: invalid scene file: {err}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from None


def _plan_from_scene(path: Path, fps: Optional[int] = None):
    scene = _load_scene(path)
    try:
        plan, log = build_plan(scene, fps=fps)
    except Exception as err:
        typer.secho(f"error: planning failed: {err}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from None
    report = validate_plan(plan)
    plan = plan.model_copy(update={"validation_report": report})
    return scene, plan, report, log


@app.command()
def validate(
    scene_path: Path = typer.Argument(..., help="Scene JSON file."),
    schema: Optional[Path] = typer.Option(
        None, help="Path to motion_plan.schema.json."
    ),
) -> None:
    """Validate a scene and print the diagnostic report."""
    schema_path = schema or DEFAULT_SCHEMA
    _scene, plan, report, _log = _plan_from_scene(scene_path)
    if schema_path and Path(schema_path).exists():
        errors = validate_against_schema(plan, schema_path)
        for e in errors:
            typer.secho(f"schema: {e}", fg=typer.colors.YELLOW)
    typer.echo(f"plan: {plan.name} | status: {report.status}")
    if not report.issues:
        typer.secho("no issues found", fg=typer.colors.GREEN)
    for issue in report.issues:
        color = {
            "critical": typer.colors.RED,
            "error": typer.colors.RED,
            "warning": typer.colors.YELLOW,
            "info": typer.colors.CYAN,
        }[issue.severity]
        typer.secho(
            f"[{issue.severity.upper()}] {issue.code} "
            f"(t={issue.time if issue.time is not None else '-'}) "
            f"{issue.message}",
            fg=color,
        )
        if issue.suggestion:
            typer.secho(f"    suggestion: {issue.suggestion}", fg=typer.colors.WHITE)
    raise typer.Exit(code=0 if report.status == "pass" else 1)


@app.command()
def simulate(
    scene_path: Path = typer.Argument(..., help="Scene JSON file."),
    fps: Optional[int] = typer.Option(None, help="Frames per second."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Optional CSV output path."
    ),
) -> None:
    """Run the simulation and print the event log."""
    _scene, plan, report, _log = _plan_from_scene(scene_path, fps=fps)
    sim = Simulator(plan)
    result = sim.run(fps=fps or plan.fps)
    typer.echo(
        f"simulated {plan.name}: {len(result.frames)} frames at "
        f"{result.fps} fps, duration {result.duration_seconds}s"
    )
    for ev in result.events:
        typer.echo(f"  t={ev.time:6.3f}s  {ev.kind:<14} {ev.description}")
    if report.issues:
        typer.secho(
            f"validation: {report.status} ({len(report.issues)} issue(s))",
            fg=typer.colors.YELLOW,
        )
    if output is not None:
        written = save_frames_csv(result, output)
        typer.secho(f"frames CSV written: {written}", fg=typer.colors.GREEN)


@app.command()
def plot(
    scene_path: Path = typer.Argument(..., help="Scene JSON file."),
    output: Path = typer.Option("output/scene.png", "--output", "-o"),
    width: int = typer.Option(1280, help="Canvas width in px."),
    height: int = typer.Option(960, help="Canvas height in px."),
) -> None:
    """Render the diagnostic PNG (trajectories, keyframes, skeletons)."""
    _scene, plan, report, _log = _plan_from_scene(scene_path)
    sim = Simulator(plan)
    result = sim.run()
    out = render_diagnostic_png(
        result, plan, output, width=width, height=height, report=report
    )
    typer.secho(f"PNG written: {out}", fg=typer.colors.GREEN)


@app.command()
def export(
    scene_path: Path = typer.Argument(..., help="Scene JSON file."),
    format: str = typer.Option("json", help="Export format: json or csv."),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    fps: Optional[int] = typer.Option(None, help="Frames per second."),
) -> None:
    """Export the motion plan (JSON, engine-agnostic) or frame CSV."""
    fmt = format.lower()
    if fmt not in ("json", "csv"):
        typer.secho(f"error: unsupported format {format!r}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    _scene, plan, report, _log = _plan_from_scene(scene_path, fps=fps)
    if fmt == "json":
        target = output or Path(f"output/{plan.name}.plan.json")
        written = export_json(plan, target)
    else:
        sim = Simulator(plan)
        result = sim.run()
        target = output or Path(f"output/{plan.name}.frames.csv")
        written = save_frames_csv(result, target)
    typer.secho(f"export written: {written}", fg=typer.colors.GREEN)


@app.command()
def report(
    scene_path: Path = typer.Argument(..., help="Scene JSON file."),
    output: Path = typer.Option("output/report.json", "--output", "-o"),
) -> None:
    """Write the validation report as JSON."""
    _scene, plan, r, _log = _plan_from_scene(scene_path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "plan_name": plan.name,
        "status": r.status,
        "issues": [i.model_dump(mode="json") for i in r.issues],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    typer.secho(f"report written: {output} (status: {r.status})",
                fg=typer.colors.GREEN)


@app.command()
def example(
    output: Path = typer.Option("output/example.json", "--output", "-o"),
) -> None:
    """Write the built-in glowing-orb handoff demo scene to JSON."""
    from .fixtures import demo_handoff_scene

    scene = demo_handoff_scene()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(scene.model_dump_json(indent=2), encoding="utf-8")
    typer.secho(f"example scene written: {output}", fg=typer.colors.GREEN)


@app.command()
def adapters() -> None:
    """List available EngineAdapter implementations."""
    typer.echo("built-in adapters:")
    typer.echo(f"  - {KeyframeListAdapter.target} (reference adapter)")
    typer.echo("subclass animation_lab.exporter.EngineAdapter to add your own")


if __name__ == "__main__":  # pragma: no cover
    app()
