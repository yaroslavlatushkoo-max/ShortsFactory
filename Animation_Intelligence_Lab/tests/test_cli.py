"""test_cli.py — Typer CLI end-to-end using the CliRunner."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from animation_lab.cli import app
from animation_lab.exporter import export_schema
from animation_lab.fixtures import (
    broken_handoff_scene,
    demo_handoff_scene,
    simple_walk_scene,
)

runner = CliRunner()


@pytest.fixture(scope="module")
def schema_file(tmp_path_factory):
    return export_schema(tmp_path_factory.mktemp("cli") / "motion_plan.schema.json")


@pytest.fixture(scope="module")
def handoff_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("scenes") / "object_handoff.json"
    p.write_text(demo_handoff_scene().model_dump_json(indent=2),
                 encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def broken_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("scenes") / "broken.json"
    p.write_text(broken_handoff_scene().model_dump_json(indent=2),
                 encoding="utf-8")
    return p


def test_example_command(tmp_path):
    out = tmp_path / "example.json"
    result = runner.invoke(app, ["example", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["name"] == "glowing_orb_handoff"


def test_validate_passes_demo(handoff_file, schema_file):
    result = runner.invoke(
        app, ["validate", str(handoff_file), "--schema", str(schema_file)]
    )
    assert result.exit_code == 0, result.output
    assert "no issues found" in result.output


def test_validate_fails_broken_scene(broken_file):
    result = runner.invoke(app, ["validate", str(broken_file)])
    assert result.exit_code == 1
    assert "DOUBLE_OWNERSHIP" in result.output


def test_simulate_command(handoff_file):
    result = runner.invoke(app, ["simulate", str(handoff_file)])
    assert result.exit_code == 0, result.output
    assert "frames at" in result.output


def test_simulate_csv_output(handoff_file, tmp_path):
    csv_out = tmp_path / "frames.csv"
    result = runner.invoke(
        app, ["simulate", str(handoff_file), "--output", str(csv_out)]
    )
    assert result.exit_code == 0, result.output
    assert csv_out.exists()
    assert csv_out.read_text(encoding="utf-8").startswith("time")


def test_plot_command(handoff_file, tmp_path):
    png = tmp_path / "handoff.png"
    result = runner.invoke(
        app, ["plot", str(handoff_file), "--output", str(png)]
    )
    assert result.exit_code == 0, result.output
    assert png.exists() and png.stat().st_size > 1000


def test_export_json_command(handoff_file, tmp_path):
    out = tmp_path / "plan.json"
    result = runner.invoke(
        app, ["export", str(handoff_file), "--format", "json",
              "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"]


def test_export_csv_command(handoff_file, tmp_path):
    out = tmp_path / "frames.csv"
    result = runner.invoke(
        app, ["export", str(handoff_file), "--format", "csv",
              "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_export_rejects_unknown_format(handoff_file):
    result = runner.invoke(
        app, ["export", str(handoff_file), "--format", "xml"]
    )
    assert result.exit_code == 2


def test_report_command(handoff_file, tmp_path):
    out = tmp_path / "report.json"
    result = runner.invoke(
        app, ["report", str(handoff_file), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "pass"
    assert data["issues"] == []


def test_missing_file_exit_code_2():
    result = runner.invoke(app, ["validate", "no_such_file.json"])
    assert result.exit_code == 2
    assert "not found" in result.output


def test_invalid_scene_exit_code_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a scene"}', encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 2
    assert "invalid scene" in result.output
