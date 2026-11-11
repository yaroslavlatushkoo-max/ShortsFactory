"""test_exporter.py — JSON round-trip, schema validation, adapters, CSV."""

from __future__ import annotations

import json

import pytest

from animation_lab.exporter import (
    ExportError,
    KeyframeListAdapter,
    export_csv,
    export_json,
    export_schema,
    import_json,
    validate_against_schema,
)
from animation_lab.fixtures import demo_handoff_scene, simple_walk_scene
from animation_lab.models import SCHEMA_VERSION
from animation_lab.motion_planner import build_plan
from animation_lab.simulation import Simulator


@pytest.fixture(scope="module")
def plan_and_schema(tmp_path_factory):
    scene = demo_handoff_scene()
    plan, _ = build_plan(scene)
    schema_path = export_schema(tmp_path_factory.mktemp("schema") / "schema.json")
    return plan, schema_path


def test_export_import_roundtrip(plan_and_schema, tmp_path):
    plan, _ = plan_and_schema
    out = export_json(plan, tmp_path / "plan.json")
    assert out.exists() and out.stat().st_size > 0
    reimported = import_json(out)
    assert reimported.name == plan.name
    assert reimported.fps == plan.fps
    assert len(reimported.keyframes) == len(plan.keyframes)
    # deep equality of the first frame
    assert (reimported.keyframes[0].characters["char_a"].position ==
            plan.keyframes[0].characters["char_a"].position)


def test_exported_json_is_versioned(plan_and_schema, tmp_path):
    plan, _ = plan_and_schema
    out = export_json(plan, tmp_path / "plan.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "units" in data
    assert data["units"]["position"] == "normalized_scene_units [0,1]"


def test_schema_validation_passes(plan_and_schema, tmp_path):
    plan, schema_path = plan_and_schema
    errors = validate_against_schema(plan, schema_path)
    assert errors == []


def test_import_rejects_wrong_version(plan_and_schema, tmp_path):
    plan, _ = plan_and_schema
    out = export_json(plan, tmp_path / "plan.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    data["schema_version"] = "0.0.1"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExportError, match="schema_version"):
        import_json(bad)


def test_keyframe_list_adapter(plan_and_schema):
    plan, _ = plan_and_schema
    adapter = KeyframeListAdapter()
    payload = adapter.convert(plan)
    assert payload["target"] == "keyframe-list"
    assert "char_a" in payload["tracks"]
    track = payload["tracks"]["char_a"]
    assert track[0]["t"] == pytest.approx(0.0)
    assert all({"t", "x", "y", "rot"} <= set(k) for k in track)


def test_csv_export(plan_and_schema, tmp_path):
    plan, _ = plan_and_schema
    result = Simulator(plan).run()
    out = export_csv(result, tmp_path / "frames.csv")
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("time")
    assert len(text.splitlines()) == len(result.frames) + 1
