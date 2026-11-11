"""test_state_machine.py — legal transitions, events, sequences."""

from __future__ import annotations

import pytest

from animation_lab.state_machine import (
    STATES,
    CharacterFSM,
    IllegalTransitionError,
    TRANSITIONS,
    validate_sequence,
)


def test_all_states_defined():
    assert len(STATES) == 13
    assert "idle" in STATES and "landing" in STATES


def test_walk_to_idle_auto():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("walking", time=0.0)
    fsm.transition("idle", time=2.0)
    assert fsm.state == "idle"


def test_jump_fall_landing_chain():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("jumping", time=0.0)
    fsm.transition("falling", time=0.4)
    fsm.transition("landing", time=1.0)
    fsm.transition("idle", time=1.3)
    assert fsm.state == "idle"


def test_reach_to_holding_requires_event():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("reaching", time=0.0)
    # event-driven: must supply matching event
    assert not fsm.can_transition("holding", event=None)
    fsm.transition("holding", time=0.5, event="successful pickup")
    assert fsm.state == "holding"


def test_illegal_jump_to_idle():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("jumping", time=0.0)
    with pytest.raises(IllegalTransitionError):
        fsm.transition("idle", time=0.1)  # must fall and land first


def test_impossible_transition_sitting_to_jumping():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("sitting", time=0.0, event="sit segment starts")
    assert "jumping" not in TRANSITIONS.get("sitting", {})


def test_history_recorded():
    fsm = CharacterFSM(character_id="c")
    fsm.transition("walking", time=1.0)
    assert fsm.history == [(1.0, "idle", "walking")]


def test_unknown_state_rejected():
    fsm = CharacterFSM(character_id="c")
    with pytest.raises(ValueError):
        fsm.can_transition("flying")


def test_sequence_validation_reports_problems():
    problems = validate_sequence([
        ("jumping", 0.0), ("idle", 0.5),  # illegal: must fall first
    ])
    assert len(problems) == 1
    assert "illegal transition" in problems[0]
    assert validate_sequence([("walking", 0.0), ("idle", 1.0)]) == []


def test_time_regression_detected():
    problems = validate_sequence([("walking", 2.0), ("idle", 1.0)])
    assert any("time regression" in p for p in problems)
