"""test_validation.py — analyzer detects real problems, not unconditional PASS."""

from __future__ import annotations


from animation_lab.fixtures import (
    broken_handoff_scene,
    demo_handoff_scene,
    simple_walk_scene,
)
from animation_lab.motion_planner import build_plan
from animation_lab.validation import (
    check_bounds,
    check_ownership_conflicts,
    check_reachability,
    check_state_transitions,
    validate_plan,
)


def test_valid_handoff_passes_with_no_issues():
    scene = demo_handoff_scene()
    plan, _ = build_plan(scene)
    report = validate_plan(plan)
    assert report.status == "pass"
    assert report.issues == ()


def test_broken_scene_produces_specific_codes():
    scene = broken_handoff_scene()
    plan, _ = build_plan(scene)
    report = validate_plan(plan)
    assert report.status == "fail"
    codes = {i.code for i in report.issues}
    assert "DOUBLE_OWNERSHIP" in codes
    assert "TRAJECTORY_DISCONTINUITY" in codes
    # every issue carries a message and severity
    for issue in report.issues:
        assert issue.message
        assert issue.severity in ("info", "warning", "error", "critical")


def test_unreachable_pickup_detected():
    scene = broken_handoff_scene()
    plan, _ = build_plan(scene)
    issues = check_reachability(plan, reach=0.20)
    assert issues, "far-away pickup must be flagged"
    assert all(i.code == "TARGET_UNREACHABLE" for i in issues)


def test_out_of_bounds_detected():
    scene = demo_handoff_scene()
    data = scene.model_dump()
    # teleport char_a far outside the scene in its end state
    for seg in data["motion_segments"]:
        if seg["id"] == "a_idle_after":
            seg["end_state"]["position"] = {"x": 1.4, "y": 0.55}
    from animation_lab.models import SceneDescription

    scene2 = SceneDescription.model_validate(data)
    plan, _ = build_plan(scene2)
    issues = check_bounds(plan)
    assert any(i.code == "OUT_OF_BOUNDS" for i in issues)


def test_empty_plan_flagged():
    from animation_lab.models import CharacterState, SceneDescription, Vec2Field

    scene = SceneDescription(
        duration_seconds=1.0,
        characters={
            "c": CharacterState(character_id="c",
                                position=Vec2Field(x=0.5, y=0.5))
        },
    )
    plan, _ = build_plan(scene)
    report = validate_plan(plan)
    codes = {i.code for i in report.issues}
    assert "EMPTY_PLAN" in codes
    assert report.status != "pass" or report.issues


def test_ownership_conflict_checker_direct():
    scene = broken_handoff_scene()
    plan, _ = build_plan(scene)
    issues = check_ownership_conflicts(plan)
    assert any(i.code == "DOUBLE_OWNERSHIP" for i in issues)
    assert any(i.severity == "critical" for i in issues)


def test_issue_fields_populated():
    scene = broken_handoff_scene()
    plan, _ = build_plan(scene)
    report = validate_plan(plan)
    for issue in report.issues:
        assert issue.code
        assert issue.message
        if issue.code == "TRAJECTORY_DISCONTINUITY":
            assert issue.time is not None
            assert issue.subject_id is not None
            assert len(issue.related_events) >= 1
            assert issue.suggestion


def test_state_transition_checker_runs():
    scene = simple_walk_scene()
    plan, _ = build_plan(scene)
    # walking -> idle is legal: no state issues
    assert check_state_transitions(plan) == []
