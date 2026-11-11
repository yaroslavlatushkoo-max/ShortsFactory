"""test_simulation.py — determinism, event ordering, frame sampling, CSV."""

from __future__ import annotations

import pytest

from animation_lab.exporter import import_scene
from animation_lab.fixtures import demo_handoff_scene, simple_walk_scene
from animation_lab.models import MotionSegment, Vec2Field, CharacterState
from animation_lab.motion_planner import build_plan
from animation_lab.simulation import Simulator, frame_csv


def _plan_for(scene):
    plan, _ = build_plan(scene)
    return plan


def test_simulation_repeatable():
    plan = _plan_for(demo_handoff_scene())
    r1 = Simulator(plan).run()
    r2 = Simulator(plan).run()
    assert len(r1.frames) == len(r2.frames)
    for f1, f2 in zip(r1.frames, r2.frames):
        assert f1.time == f2.time
        for cid in f1.characters:
            assert f1.characters[cid].position == f2.characters[cid].position
        for pid in f1.props:
            assert f1.props[pid].position == f2.props[pid].position
            assert f1.props[pid].owner_id == f2.props[pid].owner_id


def test_event_log_sorted_by_time():
    plan = _plan_for(demo_handoff_scene())
    events = Simulator(plan).build_event_log()
    times = [e.time for e in events]
    assert times == sorted(times)
    kinds = [e.kind for e in events if abs(e.time - 2.9) < 1e-9]
    # pickup event present exactly once
    assert kinds.count("pickup") == 1


def test_state_at_outside_duration_raises():
    plan = _plan_for(simple_walk_scene())
    sim = Simulator(plan)
    with pytest.raises(ValueError):
        sim.state_at(-0.1)
    with pytest.raises(ValueError):
        sim.state_at(plan.duration_seconds + 0.1)


def test_state_at_before_first_segment_is_start_state():
    plan = _plan_for(demo_handoff_scene())
    sim = Simulator(plan)
    chars, _props = sim.state_at(0.0)
    assert chars["char_a"].position.x == pytest.approx(0.15)


def test_prop_ownership_timeline_through_simulation():
    plan = _plan_for(demo_handoff_scene())
    sim = Simulator(plan)
    _, props_before = sim.state_at(2.8)
    assert props_before["orb_glow"].owner_id is None
    _, props_held = sim.state_at(3.0)
    assert props_held["orb_glow"].owner_id == "char_a"
    _, props_after = sim.state_at(3.5)
    assert props_after["orb_glow"].owner_id == "char_b"


def test_frame_csv_structure():
    plan = _plan_for(simple_walk_scene())
    result = Simulator(plan).run()
    text = frame_csv(result)
    lines = text.strip().splitlines()
    assert lines[0].startswith("time")
    assert "char.walker.x" in lines[0]
    assert len(lines) == len(result.frames) + 1


def test_fps_override_changes_frame_count():
    scene = simple_walk_scene()
    plan, _ = build_plan(scene, fps=10)
    result = Simulator(plan).run(fps=10)
    assert len(result.frames) == int(plan.duration_seconds * 10) + 1


def test_character_moves_between_frames():
    plan = _plan_for(simple_walk_scene())
    result = Simulator(plan).run()
    first = result.frames[0].characters["walker"].position.x
    mid = result.frames[len(result.frames) // 2].characters["walker"].position.x
    assert mid > first + 0.05
