"""validation.py — motion quality analyzer.

Inspects the *actual content* of a plan (trajectories, interactions,
keyframes, timing) and produces a :class:`ValidationReport` with per-issue
severity, code, message, time, subject, related events and a suggestion.

The analyzer never returns an unconditional PASS: every check inspects real
plan data, and an empty plan produces issues of its own.
"""

from __future__ import annotations

import math
from typing import Optional

from .models import (
    MotionPlan,
    ValidationIssue,
    ValidationReport,
)
from .state_machine import TRANSITIONS
from .trajectories import segment_velocity
from .vectors import Vec2, in_unit_square


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    time: Optional[float] = None,
    subject_id: Optional[str] = None,
    subject_kind: Optional[str] = None,
    related: tuple[str, ...] = (),
    suggestion: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        time=time,
        subject_id=subject_id,
        subject_kind=subject_kind,  # type: ignore[arg-type]
        related_events=related,
        suggestion=suggestion,
    )


def check_position_jumps(
    plan: MotionPlan, jump_threshold: float = 0.05
) -> list[ValidationIssue]:
    """Position jumps between consecutive frames of one character."""
    issues: list[ValidationIssue] = []
    frames = plan.keyframes
    for cid in sorted(plan.characters):
        for a, b in zip(frames, frames[1:]):
            pa = a.characters[cid].position
            pb = b.characters[cid].position
            d = math.hypot(pb.x - pa.x, pb.y - pa.y)
            dt = b.time - a.time
            if dt <= 0:
                continue
            if d > jump_threshold:
                issues.append(
                    _issue(
                        "error",
                        "POSITION_JUMP",
                        f"character {cid!r} jumped {d:.4f} scene units in "
                        f"{dt * 1000:.1f} ms between frames at t={b.time:.3f}s",
                        time=b.time,
                        subject_id=cid,
                        subject_kind="character",
                        suggestion=(
                            "add an intermediate motion segment or reduce "
                            "segment speed"
                        ),
                    )
                )
    return issues


def check_speed_and_acceleration(plan: MotionPlan) -> list[ValidationIssue]:
    """Per-segment speed/acceleration sanity based on sampled evaluation."""
    issues: list[ValidationIssue] = []
    for seg in plan.motion_segments:
        limit = seg.trajectory.max_speed or seg.constraints.max_speed
        n = max(8, int(seg.duration * 30))
        speeds: list[float] = []
        for i in range(n + 1):
            t = seg.start_time + seg.duration * i / n
            v = segment_velocity(seg, t)
            speeds.append(math.hypot(v[0], v[1]))
        over = [i for i, s in enumerate(speeds) if s > limit * 1.05]
        if over:
            t_over = seg.start_time + seg.duration * over[0] / n
            issues.append(
                _issue(
                    "warning",
                    "SPEED_LIMIT_EXCEEDED",
                    f"segment {seg.id!r} reaches {max(speeds):.4f} u/s, "
                    f"limit {limit:.4f} u/s",
                    time=t_over,
                    subject_id=seg.id,
                    subject_kind="segment",
                    related=(seg.id,),
                    suggestion="increase segment duration or lower max_speed",
                )
            )
        # Acceleration heuristic: big speed change within the segment.
        if len(speeds) >= 3:
            accel = (max(speeds) - min(speeds)) / max(seg.duration, 1e-6)
            if accel > seg.constraints.max_acceleration * 2.0:
                issues.append(
                    _issue(
                        "warning",
                        "SUSPICIOUS_ACCELERATION",
                        f"segment {seg.id!r}: speed changes by {accel:.3f} "
                        f"u/s^2 (limit {seg.constraints.max_acceleration:.3f})",
                        time=seg.start_time,
                        subject_id=seg.id,
                        subject_kind="segment",
                        suggestion="use ease_in_out instead of linear for fast legs",
                    )
                )
    return issues


def check_trajectory_continuity(
    plan: MotionPlan, tolerance: float = 1e-3
) -> list[ValidationIssue]:
    """End state of one segment vs start state of the next (per character)."""
    issues: list[ValidationIssue] = []
    by_char: dict[str, list] = {}
    for seg in plan.motion_segments:
        by_char.setdefault(seg.character_id, []).append(seg)
    for cid, segs in by_char.items():
        segs = sorted(segs, key=lambda s: (s.start_time, s.end_time, s.id))
        for a, b in zip(segs, segs[1:]):
            if a.end_time < b.start_time - 1e-9:
                continue  # idle gap is fine
            gap = math.hypot(
                b.start_state.position.x - a.end_state.position.x,
                b.start_state.position.y - a.end_state.position.y,
            )
            if gap > tolerance:
                issues.append(
                    _issue(
                        "error",
                        "TRAJECTORY_DISCONTINUITY",
                        f"position discontinuity {gap:.4f} u between "
                        f"{a.id!r} and {b.id!r} for {cid!r}",
                        time=b.start_time,
                        subject_id=cid,
                        subject_kind="character",
                        related=(a.id, b.id),
                        suggestion="align end_state of the earlier segment "
                        "with start_state of the next",
                    )
                )
    return issues


def check_state_transitions(plan: MotionPlan) -> list[ValidationIssue]:
    """State-machine plausibility of the action_type order per character."""
    issues: list[ValidationIssue] = []
    # Segment action types mapped to FSM states.
    action_to_state = {
        "idle": "idle", "walk": "walking", "run": "running", "turn": "turning",
        "reach": "reaching", "hold": "holding", "speak": "speaking",
        "react": "reacting", "jump": "jumping", "fall": "falling",
        "land": "landing", "sit": "sitting", "stand": "standing",
        "step_back": "walking", "handoff": "holding",
    }
    by_char: dict[str, list] = {}
    for seg in plan.motion_segments:
        by_char.setdefault(seg.character_id, []).append(seg)
    for cid, segs in by_char.items():
        segs = sorted(segs, key=lambda s: (s.start_time, s.end_time, s.id))
        current = "idle"
        for seg in segs:
            target = action_to_state.get(seg.action_type)
            if target is None:
                continue
            if target == current:
                continue
            table = TRANSITIONS.get(current, {})
            if target not in table:
                issues.append(
                    _issue(
                        "error",
                        "ILLEGAL_STATE_TRANSITION",
                        f"{cid!r}: action {seg.action_type!r} implies state "
                        f"{target!r}, unreachable from {current!r} "
                        f"(segment {seg.id!r})",
                        time=seg.start_time,
                        subject_id=cid,
                        subject_kind="character",
                        related=(seg.id,),
                        suggestion="insert an intermediate action (e.g. turn "
                        "or idle) that legally connects the states",
                    )
                )
                current = target  # resync to keep going
                continue
            condition = table[target]
            if condition.startswith("event:") and target in (
                "holding", "transferring",
            ):
                # Event-driven transitions must be backed by an interaction.
                backed = any(
                    i.actor_id == cid or i.receiver_id == cid
                    for i in plan.interactions
                )
                if not backed:
                    issues.append(
                        _issue(
                            "error",
                            "UNBACKED_EVENT_TRANSITION",
                            f"{cid!r}: {current!r}->{target!r} needs a pickup/"
                            f"transfer interaction, none found",
                            time=seg.start_time,
                            subject_id=cid,
                            subject_kind="character",
                            related=(seg.id,),
                            suggestion="add a pickup/transfer interaction event",
                        )
                    )
            current = target
    return issues


def check_bounds(plan: MotionPlan, margin: float = 0.02) -> list[ValidationIssue]:
    """Characters and props leaving the normalized scene."""
    issues: list[ValidationIssue] = []
    for frame in plan.keyframes:
        for cid, st in sorted(frame.characters.items()):
            p = st.position.as_tuple()
            if not in_unit_square(p) and not (
                -margin <= p[0] <= 1 + margin and -margin <= p[1] <= 1 + margin
            ):
                issues.append(
                    _issue(
                        "error",
                        "OUT_OF_BOUNDS",
                        f"character {cid!r} at ({p[0]:.3f}, {p[1]:.3f}) leaves "
                        f"the scene at t={frame.time:.3f}s",
                        time=frame.time,
                        subject_id=cid,
                        subject_kind="character",
                        suggestion="clamp target positions into [0,1]^2",
                    )
                )
        for pid, st in sorted(frame.props.items()):
            p = st.position.as_tuple()
            if not in_unit_square(p) and not (
                -margin <= p[0] <= 1 + margin and -margin <= p[1] <= 1 + margin
            ):
                issues.append(
                    _issue(
                        "error",
                        "OUT_OF_BOUNDS",
                        f"prop {pid!r} at ({p[0]:.3f}, {p[1]:.3f}) leaves the "
                        f"scene at t={frame.time:.3f}s",
                        time=frame.time,
                        subject_id=pid,
                        subject_kind="prop",
                        suggestion="keep prop positions within [0,1]^2",
                    )
                )
    return issues


def check_ownership_conflicts(plan: MotionPlan) -> list[ValidationIssue]:
    """Two owners at the same instant / pickup of an owned prop."""
    issues: list[ValidationIssue] = []
    owner_timeline: dict[str, list[tuple[float, Optional[str], str]]] = {}
    initial = {pid: p.owner_id for pid, p in plan.props.items()}
    for pid, own in initial.items():
        owner_timeline.setdefault(pid, []).append((0.0, own, "initial"))
    for inter in sorted(plan.interactions, key=lambda i: (i.time, i.kind, i.id)):
        pid = inter.prop_id
        if inter.kind in ("pickup", "attach"):
            owner_timeline.setdefault(pid, []).append(
                (inter.time, inter.actor_id, inter.id)
            )
        elif inter.kind == "transfer":
            owner_timeline.setdefault(pid, []).append(
                (inter.time, inter.receiver_id, inter.id)
            )
        elif inter.kind in ("detach", "release"):
            owner_timeline.setdefault(pid, []).append((inter.time, None, inter.id))

    for pid, entries in owner_timeline.items():
        entries.sort(key=lambda e: (e[0], e[2]))
        for (t0, o0, e0), (t1, o1, e1) in zip(entries, entries[1:]):
            if o0 is not None and o1 is not None and o0 != o1 and t0 == t1:
                issues.append(
                    _issue(
                        "critical",
                        "DOUBLE_OWNERSHIP",
                        f"prop {pid!r} claimed by {o0!r} and {o1!r} at the "
                        f"same instant t={t0:.3f}s",
                        time=t0,
                        subject_id=pid,
                        subject_kind="prop",
                        related=(e0, e1),
                        suggestion="sequence pickup/transfer events at "
                        "distinct times",
                    )
                )
    return issues


def check_reachability(plan: MotionPlan, reach: float = 0.20) -> list[ValidationIssue]:
    """Pickup interactions whose prop is unreachable from the actor's path."""
    issues: list[ValidationIssue] = []
    by_char: dict[str, list] = {}
    for seg in plan.motion_segments:
        by_char.setdefault(seg.character_id, []).append(seg)
    for inter in plan.interactions:
        if inter.kind not in ("pickup", "attach"):
            continue
        prop = plan.props.get(inter.prop_id)
        if prop is None:
            continue
        segs = by_char.get(inter.actor_id, [])
        # Character position at the interaction time.
        pos: Optional[Vec2] = None
        for seg in segs:
            if seg.start_time - 1e-9 <= inter.time <= seg.end_time + 1e-9:
                from .trajectories import segment_position

                pos = segment_position(seg, inter.time)
                break
        if pos is None:
            if segs:
                last = max(segs, key=lambda s: s.end_time)
                pos = (
                    last.end_state.position.as_tuple()
                    if last.end_time <= inter.time
                    else min(segs, key=lambda s: s.start_time).start_state.position.as_tuple()
                )
            else:
                ch = plan.characters.get(inter.actor_id)
                pos = ch.position.as_tuple() if ch else None
        if pos is None:
            continue
        d = math.hypot(
            pos[0] - prop.position.x, pos[1] - prop.position.y
        )
        if d > reach:
            issues.append(
                _issue(
                    "error",
                    "TARGET_UNREACHABLE",
                    f"prop {inter.prop_id!r} is {d:.3f} u from "
                    f"{inter.actor_id!r} at t={inter.time:.3f}s "
                    f"(reach {reach:.3f})",
                    time=inter.time,
                    subject_id=inter.actor_id,
                    subject_kind="character",
                    related=(inter.id,),
                    suggestion="move the character closer before the pickup "
                    "or add a reach segment",
                )
            )
    return issues


def check_missing_keyframes(plan: MotionPlan) -> list[ValidationIssue]:
    """Keyframe grid coverage of the whole duration."""
    issues: list[ValidationIssue] = []
    frames = plan.keyframes
    if not frames:
        return [
            _issue(
                "error",
                "MISSING_KEYFRAMES",
                "plan contains no keyframes",
                suggestion="run the simulator to sample the timeline",
            )
        ]
    n_expected = int(round(plan.duration_seconds * plan.fps)) + 1
    if len(frames) < n_expected:
        issues.append(
            _issue(
                "warning",
                "MISSING_KEYFRAMES",
                f"expected {n_expected} frames at {plan.fps} fps, "
                f"found {len(frames)}",
                suggestion="resample the simulation at the plan fps",
            )
        )
    times = [f.time for f in frames]
    if any(b < a for a, b in zip(times, times[1:])):
        issues.append(
            _issue(
                "error",
                "TIMELINE_ORDER",
                "keyframe times are not monotonically increasing",
                suggestion="re-sort frames by time",
            )
        )
    return issues


def check_timing_dependencies(plan: MotionPlan) -> list[ValidationIssue]:
    """Segments/interactions extending beyond the plan duration; zero-length."""
    issues: list[ValidationIssue] = []
    for seg in plan.motion_segments:
        if seg.end_time > plan.duration_seconds + 1e-9:
            issues.append(
                _issue(
                    "error",
                    "TIMING_DEPENDENCY",
                    f"segment {seg.id!r} ends at {seg.end_time:.3f}s beyond "
                    f"plan duration {plan.duration_seconds:.3f}s",
                    time=seg.end_time,
                    subject_id=seg.id,
                    subject_kind="segment",
                    suggestion="extend duration_seconds or shorten the segment",
                )
            )
    for inter in plan.interactions:
        if inter.time > plan.duration_seconds + 1e-9:
            issues.append(
                _issue(
                    "error",
                    "TIMING_DEPENDENCY",
                    f"interaction {inter.id!r} at {inter.time:.3f}s beyond "
                    f"plan duration",
                    time=inter.time,
                    subject_id=inter.id,
                    subject_kind="interaction",
                    suggestion="move the event inside the plan duration",
                )
            )
    return issues


def check_incompatible_actions(plan: MotionPlan) -> list[ValidationIssue]:
    """One character in two overlapping motion segments."""
    issues: list[ValidationIssue] = []
    by_char: dict[str, list] = {}
    for seg in plan.motion_segments:
        by_char.setdefault(seg.character_id, []).append(seg)
    for cid, segs in by_char.items():
        segs = sorted(segs, key=lambda s: (s.start_time, s.end_time, s.id))
        for a, b in zip(segs, segs[1:]):
            if b.start_time < a.end_time - 1e-9:
                issues.append(
                    _issue(
                        "error",
                        "INCOMPATIBLE_ACTIONS",
                        f"{cid!r}: segments {a.id!r} and {b.id!r} overlap in "
                        f"[{b.start_time:.3f}, {min(a.end_time, b.end_time):.3f}]",
                        time=b.start_time,
                        subject_id=cid,
                        subject_kind="character",
                        related=(a.id, b.id),
                        suggestion="sequence segments without overlap",
                    )
                )
    return issues


def check_collisions(plan: MotionPlan) -> list[ValidationIssue]:
    """Character-character overlaps across frames."""
    from .collision import character_overlap

    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, float]] = set()
    for frame in plan.keyframes:
        cids = sorted(frame.characters)
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                a, b = frame.characters[cids[i]], frame.characters[cids[j]]
                rep = character_overlap(a, b)
                if rep is None:
                    continue
                key = (rep.a_id, rep.b_id or "", rep.kind)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(
                    _issue(
                        "error" if rep.kind == "collision" else "warning",
                        "CHARACTER_COLLISION" if rep.kind == "collision"
                        else "PROXIMITY_WARNING",
                        f"t={frame.time:.3f}s: {rep.message}",
                        time=frame.time,
                        subject_id=rep.a_id,
                        subject_kind="character",
                        related=(rep.b_id or "",),
                        suggestion="keep characters apart by at least two "
                        "body radii",
                    )
                )
    return issues


def validate_plan(plan: MotionPlan) -> ValidationReport:
    """Run every analyzer pass over the plan and assemble the report."""
    issues: list[ValidationIssue] = []
    issues += check_position_jumps(plan)
    issues += check_speed_and_acceleration(plan)
    issues += check_trajectory_continuity(plan)
    issues += check_state_transitions(plan)
    issues += check_bounds(plan)
    issues += check_ownership_conflicts(plan)
    issues += check_reachability(plan)
    issues += check_missing_keyframes(plan)
    issues += check_timing_dependencies(plan)
    issues += check_incompatible_actions(plan)
    issues += check_collisions(plan)

    if not plan.motion_segments:
        issues.append(
            _issue(
                "warning",
                "EMPTY_PLAN",
                "plan has no motion segments",
                suggestion="add at least one motion segment per character",
            )
        )

    if any(i.severity in ("error", "critical") for i in issues):
        status = "fail"
    elif any(i.severity == "warning" for i in issues):
        status = "warnings"
    else:
        status = "pass"
    return ValidationReport(status=status, issues=tuple(issues))
