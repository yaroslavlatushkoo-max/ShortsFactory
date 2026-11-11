"""Mission 13.2 — refined locomotion: framing / transitions / foot sliding.

Fast unit tests (no Blender) for the refined script builder plus one
integration test that runs a short background-Blender render.
"""

from __future__ import annotations

import os

import pytest

from shortsfactory.generative.animation_pipeline import (
    LocomotionError,
    build_refined_locomotion_script,
    compute_phases,
)

CLIPS = [
    "attack-kick-left", "die", "emote-no", "emote-yes", "holding-both",
    "idle", "pick-up", "sit", "sprint", "static", "walk",
]

KENNEY_MODEL = r"D:\blender_test_temp\kenney_blocky\unpacked\Models\GLB format\character-a.glb"

REFINED_SPEC = (("idle", 1.2, 0.0), ("walk", 3.6, 1.4), ("idle", 2.2, 0.0))


def _phases(clip_cycles=None):
    return compute_phases(CLIPS, phase_spec=REFINED_SPEC,
                          clip_cycles=clip_cycles or {"walk": 16}, fps=30)


def test_refined_build_writes_self_contained(tmp_path):
    model = tmp_path / "fake.glb"
    model.write_bytes(b"placeholder")
    phases = _phases()
    script = build_refined_locomotion_script(model, tmp_path, phases=phases,
                                             width=180, height=320)
    src = script.read_text(encoding="utf-8")
    assert "import bpy" in src
    assert "FIT_FRACTION" in src
    assert "natural_speed" in src
    assert "framing" in src
    assert "LOCOMOTION_OK" in src
    compile(src, str(script), "exec")


def test_refined_build_requires_existing_model(tmp_path):
    with pytest.raises(LocomotionError):
        build_refined_locomotion_script(tmp_path / "missing.glb", tmp_path)


def test_refined_phases_clamp_walk_to_whole_cycles():
    phases = _phases({"walk": 16})
    walk = phases[1]
    assert walk.action == "walk"
    # 3.6s @30fps = 108 frames -> 6.75 cycles -> clamps to 7 cycles = 3.733s
    assert walk.duration == pytest.approx(7 * 16 / 30.0, abs=0.01)
    assert phases[0].start == 0.0
    assert phases[2].start == pytest.approx(walk.start + walk.duration, abs=0.01)


@pytest.mark.integration
def test_refined_kenney_benchmark_render(tmp_path):
    """Real background-Blender render at tiny resolution (only if Blender)."""
    from shortsfactory.generative.animation_pipeline import (
        inspect_model,
        render_locomotion,
    )
    if not os.path.exists(KENNEY_MODEL):
        pytest.skip("Kenney model not present")
    blender_exe = r"D:\Blender\blender.exe"
    if not os.path.exists(blender_exe):
        pytest.skip("Blender not found")
    os.environ["BLENDER_EXECUTABLE"] = blender_exe
    insp = inspect_model(KENNEY_MODEL)
    clip_cycles = {c.name: max(1, int(c.frame_end - c.frame_start))
                   for c in insp.clips}
    phases = _phases(clip_cycles)
    script = build_refined_locomotion_script(KENNEY_MODEL, tmp_path,
                                             phases=phases,
                                             width=180, height=320)
    res = render_locomotion(script, timeout=1800)
    assert res.status == "OK", res.error
    assert res.video and tmp_path.joinpath("final.mp4").exists()
    diag = res.diagnostics
    assert diag
    assert diag.get("displacement", {}).get("total") is not None
    assert "framing" in diag
    assert "foot" in diag
    assert diag["foot"].get("contact") == "NOT_VERIFIED"
