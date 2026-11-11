"""Mission 15 — full-body multi-joint animation tests.

Unit tests for bone-group detection and script generation (no Blender), plus
an integration test that renders the authored EMPTY-segment joint demo in
background Blender at a tiny resolution.
"""

from __future__ import annotations

import os

import pytest

from shortsfactory.generative.animation_pipeline import (
    FullBodyResult,
    build_authored_rig_script,
    detect_control_groups,
    render_authored_rig,
)

BLENDER = r"D:\Blender\blender.exe"


def test_detect_control_groups_counts():
    bones = [f"torso_{i}" for i in (1, 2, 3)] + \
            [f"neck_{i}" for i in (1, 2)] + ["head"] + \
            [f"arm_L_{i}" for i in (1, 2, 3)] + \
            [f"arm_R_{i}" for i in (1, 2, 3)] + \
            [f"leg_L_{i}" for i in (1, 2, 3, 4)] + \
            [f"leg_R_{i}" for i in (1, 2, 3, 4)]
    g = detect_control_groups(bones)
    assert len(g.get("spine", [])) == 3
    assert len(g.get("neck", [])) == 2
    assert len(g.get("head", [])) == 1
    assert len(g.get("arm_l", [])) == 3
    assert len(g.get("arm_r", [])) == 3
    assert len(g.get("leg_l", [])) == 4
    assert len(g.get("leg_r", [])) == 4


def test_joint_script_generates_and_compiles(tmp_path):
    script = build_authored_rig_script(tmp_path, scenario="joint",
                                       width=180, height=320)
    src = script.read_text(encoding="utf-8")
    for marker in ("EMPTIES", "TRAJ", "set_rot", "FULLBODY_OK", "make_part"):
        assert marker in src
    compile(src, str(script), "exec")


def test_fullbody_script_generates_and_compiles(tmp_path):
    script = build_authored_rig_script(tmp_path, scenario="fullbody",
                                       width=180, height=320)
    src = script.read_text(encoding="utf-8")
    assert "FULLBODY_OK" in src
    assert "TRAJ" in src
    compile(src, str(script), "exec")


@pytest.mark.integration
def test_authored_joint_benchmark_render(tmp_path):
    if not os.path.exists(BLENDER):
        pytest.skip("Blender not found")
    os.environ["BLENDER_EXECUTABLE"] = BLENDER
    script = build_authored_rig_script(tmp_path, scenario="joint",
                                       width=180, height=320)
    res = render_authored_rig(script, timeout=1800)
    assert res.status == "OK", res.error
    assert tmp_path.joinpath("final.mp4").exists()
    d = res.diagnostics or {}
    assert d.get("framing", {}).get("passed") is True
    limbs = d.get("bone_delta") or d.get("limbs") or {}
    # key controlled joints must show visible angular motion
    assert limbs.get("torso_1", {}).get("max_delta_rad", 0) > 0.05
    assert limbs.get("arm_L_1", {}).get("max_delta_rad", 0) > 0.05
    assert limbs.get("head", {}).get("max_delta_rad", 0) > 0.02
