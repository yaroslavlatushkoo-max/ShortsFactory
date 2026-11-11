"""Mission 14 — clearly visible character animation: tests.

Fast unit tests (no Blender) for the Mission 14 script builder, plus one
integration test that renders a short benchmark in background Blender.
"""

from __future__ import annotations

import os

import pytest

from shortsfactory.generative.animation_pipeline import (
    LocomotionError,
    build_mission14_script,
)

KENNEY_MODEL = r"D:\blender_test_temp\kenney_blocky\unpacked\Models\GLB format\character-a.glb"


def test_mission14_build_writes_self_contained(tmp_path):
    model = tmp_path / "fake.glb"
    model.write_bytes(b"placeholder")
    script = build_mission14_script(model, tmp_path, width=180, height=320)
    src = script.read_text(encoding="utf-8")
    assert "import bpy" in src
    assert "MISSION14_OK" in src
    assert "AMPL" in src
    assert "TRAVEL" in src
    assert "bake_loop" in src
    assert "limbs" in src  # limb-swing diagnostic
    compile(src, str(script), "exec")


def test_mission14_build_requires_existing_model(tmp_path):
    with pytest.raises(LocomotionError):
        build_mission14_script(tmp_path / "missing.glb", tmp_path)


@pytest.mark.integration
def test_mission14_kenney_benchmark_render(tmp_path):
    """Real background-Blender render at tiny resolution (only if Blender)."""
    from shortsfactory.generative.animation_pipeline import render_locomotion
    from shortsfactory.generative.animation_pipeline.visual_metrics import analyze
    if not os.path.exists(KENNEY_MODEL):
        pytest.skip("Kenney model not present")
    blender_exe = r"D:\Blender\blender.exe"
    if not os.path.exists(blender_exe):
        pytest.skip("Blender not found")
    os.environ["BLENDER_EXECUTABLE"] = blender_exe
    script = build_mission14_script(KENNEY_MODEL, tmp_path,
                                    width=180, height=320)
    res = render_locomotion(script, timeout=1800)
    assert res.status == "OK", res.error
    assert res.video and tmp_path.joinpath("final.mp4").exists()
    diag = res.diagnostics
    assert diag
    assert "displacement" in diag
    assert "framing" in diag
    assert diag["framing"].get("passed") is True
    # visible character motion from rendered frames (stationary camera)
    m = analyze(res.video, w=180, h=320)
    assert m["walk_a"]["full"] > 1.0, m  # clear motion during travel walk
    assert m["walk_a"]["arms"] > m["idle_a"]["arms"], m  # limbs swing more than idle
