"""Mission 11 — isolated animation pipeline tests (fast, no Blender runs)."""

import pytest

from shortsfactory.generative.animation_pipeline.clips_map import (
    find_clip,
    semantic_clip_map,
)
from shortsfactory.generative.animation_pipeline.importer import ModelInspectionError
from shortsfactory.generative.animation_pipeline.player import AnimationPipelineError, build_schedule

CLIPS = [
    "attack-kick-left", "die", "drive", "emote-no", "emote-yes",
    "holding-both", "holding-left", "holding-right", "idle",
    "interact-left", "interact-right", "pick-up", "sit", "sprint",
    "static", "walk", "wheelchair-move-back",
]


def test_semantic_map_finds_core_actions():
    m = semantic_clip_map(CLIPS)
    assert "idle" in m and "walk" in m
    assert "run" in m and "sprint" in m["run"]  # sprint alias
    assert "pickup" in m and "pick-up" in m["pickup"]
    assert "hold" in m
    assert "react" in m


def test_find_clip_prefers_shortest_specific():
    assert find_clip(CLIPS, "idle") == "idle"
    assert find_clip(CLIPS, "pickup") == "pick-up"
    assert find_clip(CLIPS, "react") in ("emote-no", "emote-yes")
    assert find_clip(CLIPS, "nonexistent") is None


def test_build_schedule_requires_existing_model(tmp_path):
    missing = tmp_path / "nope.glb"
    with pytest.raises((ModelInspectionError, AnimationPipelineError)):
        build_schedule(missing, ["idle"])


def test_build_schedule_layouts_sequentially():
    # inspect_model would need Blender; instead validate the layout logic by
    # stubbing the inspection to avoid a real Blender dependency here.
    from shortsfactory.generative.animation_pipeline import player as pl
    insp = pl.inspect_model
    pl.inspect_model = lambda p: type("I", (), {
        "clips": [
            type("C", (), {"name": n, "duration_seconds": 1.0,
                            "frame_start": 0.0, "frame_end": 30.0,
                            "curve_count": 4, "animated_targets": ["root"]})
            for n in CLIPS
        ]
    })()
    try:
        sched = build_schedule("fake.glb", ["idle", "walk", "pickup"],
                               segment_length=2.0)
        assert len(sched) == 3
        assert sched[0].clip == "idle" and sched[0].start_seconds == 0.0
        assert sched[1].clip == "walk" and sched[1].start_seconds > 0.0
        assert sched[2].clip == "pick-up"
        # durations capped to clip duration (1.0)
        assert sched[0].duration_seconds == 1.0
    finally:
        pl.inspect_model = insp
