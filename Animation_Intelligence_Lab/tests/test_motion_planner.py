"""test_motion_planner.py — plan building, continuity, interaction replay."""

from __future__ import annotations

import pytest

from animation_lab.fixtures import broken_handoff_scene, demo_handoff_scene
from animation_lab.models import (
    MotionSegment,
    SceneDescription,
    Vec2Field,
    CharacterState,
)
from animation_lab.motion_planner import (
    PlanningError,
    build_plan,
    build_plan_strict,
    check_continuity,
)
from animation_lab.validation import validate_plan


def _state(cid: str, pos: tuple[float, float]) -> CharacterState:
    return CharacterState(character_id=cid,
                          position=Vec2Field(x=pos[0], y=pos[1]))


def test_demo_handoff_builds_and_passes():
    scene = demo_handoff_scene()
    plan, log = build_plan(scene)
    assert plan.name == "glowing_orb_handoff"
    assert len(plan.keyframes) == int(plan.duration_seconds * plan.fps) + 1
    # interactions sorted deterministically
    times = [i.time for i in plan.interactions]
    assert times == sorted(times)
    report = validate_plan(plan)
    assert report.status == "pass", [
        f"{i.code}: {i.message}" for i in report.issues
    ]


def test_demo_handoff_transfer_changes_owner():
    scene = demo_handoff_scene()
    plan, _ = build_plan(scene)
    # before transfer: orb owned by A; after: by B
    pre = next(f for f in plan.keyframes if abs(f.time - 3.3) < 1e-6)
    post = next(f for f in plan.keyframes if abs(f.time - 3.5) < 1e-6)
    assert pre.props["orb_glow"].owner_id == "char_a"
    assert post.props["orb_glow"].owner_id == "char_b"
    # ownership is exclusive in every frame
    for f in plan.keyframes:
        st = f.props["orb_glow"]
        if st.owner_id is not None:
            assert st.owner_id in ("char_a", "char_b")


def test_broken_scene_fails_validation():
    scene = broken_handoff_scene()
    plan, _ = build_plan(scene)
    report = validate_plan(plan)
    assert report.status == "fail"
    codes = {i.code for i in report.issues}
    assert "DOUBLE_OWNERSHIP" in codes
    assert "TRAJECTORY_DISCONTINUITY" in codes


def test_overlapping_segments_rejected():
    seg1 = MotionSegment(
        id="s1", character_id="c", action_type="idle", start_time=0.0,
        end_time=2.0, start_state=_state("c", (0.1, 0.1)),
        end_state=_state("c", (0.1, 0.1)),
    )
    seg2 = MotionSegment(
        id="s2", character_id="c", action_type="walk", start_time=1.0,
        end_time=3.0, start_state=_state("c", (0.2, 0.2)),
        end_state=_state("c", (0.3, 0.3)),
    )
    scene = SceneDescription(
        duration_seconds=4.0,
        characters={"c": _state("c", (0.1, 0.1))},
        motion_segments=(seg1, seg2),
    )
    with pytest.raises(PlanningError):
        build_plan(scene)


def test_continuity_gap_detection():
    seg1 = MotionSegment(
        id="s1", character_id="c", action_type="walk", start_time=0.0,
        end_time=1.0, start_state=_state("c", (0.1, 0.1)),
        end_state=_state("c", (0.3, 0.3)),
    )
    seg2 = MotionSegment(
        id="s2", character_id="c", action_type="walk", start_time=1.0,
        end_time=2.0, start_state=_state("c", (0.7, 0.7)),
        end_state=_state("c", (0.8, 0.8)),
    )
    gaps = check_continuity([seg1, seg2])
    assert len(gaps) == 1
    assert gaps[0][2] > 0.1


def test_unknown_character_in_segment_rejected():
    seg = MotionSegment(
        id="s", character_id="ghost", action_type="walk", start_time=0.0,
        end_time=1.0, start_state=_state("ghost", (0.1, 0.1)),
        end_state=_state("ghost", (0.2, 0.2)),
    )
    with pytest.raises(ValueError):
        SceneDescription(
            duration_seconds=2.0,
            characters={"c": _state("c", (0.1, 0.1))},
            motion_segments=(seg,),
        )


def test_strict_plan_raises_on_failed_precondition():
    scene = broken_handoff_scene()
    with pytest.raises(Exception):  # noqa: B017
        build_plan_strict(scene)
