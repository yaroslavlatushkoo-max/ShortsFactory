"""Mission 13 — locomotion & smooth-transition tests.

Fast unit tests for phase layout / script text (no Blender), plus one
integration test that renders a short benchmark in background Blender when an
executable is available (marked with ``integration``).
"""

from __future__ import annotations

import pytest

from shortsfactory.generative.animation_pipeline import (
    LocomotionError,
    build_locomotion_script,
    compute_phases,
)

CLIPS = [
    "attack-kick-left", "die", "emote-no", "emote-yes", "holding-both",
    "idle", "pick-up", "sit", "sprint", "static", "walk",
]

KENNEY_MODEL = r"D:\blender_test_temp\kenney_blocky\unpacked\Models\GLB format\character-a.glb"


def test_compute_phases_requires_idle_walk():
    with pytest.raises(LocomotionError):
        compute_phases(["idle"])  # no walk


def test_compute_phases_clamps_walk_to_whole_cycles():
    phases = compute_phases(CLIPS, clip_cycles={"walk": 16}, fps=30)
    assert [p.action for p in phases] == ["idle", "walk", "idle"]
    walk = phases[1]
    # 3.2s at 30fps = 96 frames = exactly 6 cycles of 16 frames
    assert walk.duration == pytest.approx(3.2, abs=0.01)
    assert phases[0].start == 0.0
    assert phases[2].start == pytest.approx(4.0, abs=0.01)


def test_compute_phases_defaults_walk_cycle_16():
    phases = compute_phases(CLIPS)
    assert phases[1].duration > 0.0
    assert phases[1].clip == "walk"
    assert phases[0].clip == "idle"


def test_build_script_writes_self_contained(tmp_path):
    # script generation is pure Python (no Blender needed)
    model = tmp_path / "fake.glb"
    model.write_bytes(b"placeholder")  # path only embedded at build time
    phases = compute_phases(CLIPS, clip_cycles={"walk": 16})
    script = build_locomotion_script(model, tmp_path, phases=phases,
                                     width=180, height=320)
    src = script.read_text(encoding="utf-8")
    assert "import bpy" in src
    assert "bpy.ops.import_scene.gltf" in src
    assert "TRAJ" in src
    assert "bpy.ops.render.render(animation=True)" in src
    assert "LOCOMOTION_OK" in src
    compile(src, str(script), "exec")


def test_build_script_requires_existing_model(tmp_path):
    with pytest.raises(LocomotionError):
        build_locomotion_script(tmp_path / "missing.glb", tmp_path, phases=[])


@pytest.mark.integration
def test_kenney_locomotion_benchmark_render(tmp_path):
    """Real background-Blender render at tiny resolution (only if Blender)."""
    from shortsfactory.generative.animation_pipeline import (
        render_locomotion,
        inspect_model,
    )
    import os
    if not os.path.exists(KENNEY_MODEL):
        pytest.skip("Kenney model not present")
    blender_exe = r"D:\Blender\blender.exe"
    if not os.path.exists(blender_exe):
        pytest.skip("Blender not found")
    os.environ["BLENDER_EXECUTABLE"] = blender_exe
    insp = inspect_model(KENNEY_MODEL)
    clip_cycles = {c.name: max(1, int(c.frame_end - c.frame_start))
                   for c in insp.clips}
    phases = compute_phases([c.name for c in insp.clips],
                            clip_cycles=clip_cycles, fps=30)
    script = build_locomotion_script(KENNEY_MODEL, tmp_path, phases=phases,
                                     width=180, height=320, start_x=-0.5,
                                     end_x=0.5)
    res = render_locomotion(script, timeout=1800)
    assert res.status == "OK", res.error
    assert res.video and tmp_path.joinpath("final.mp4").exists()
    assert res.diagnostics
    samples = res.diagnostics.get("samples", {})
    assert samples
