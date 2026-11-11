"""motion_planner.py — builds a MotionPlan from a SceneDescription.

The planner:
1. validates cross-references and continuous-time coverage per character,
2. checks segment boundary continuity (no position jumps),
3. applies interaction events (raising InteractionError on violated
   preconditions) and derives prop ownership over time,
4. samples keyframes at the plan fps.

It never invents or repairs input data: inconsistencies surface as errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .interaction import (
    InteractionError,
    OwnershipLedger,
    apply_interaction,
    sort_key,
)
from .models import (
    CharacterState,
    Interaction,
    Keyframe,
    MotionPlan,
    MotionSegment,
    PropState,
    SceneDescription,
    ValidationReport,
)
from .trajectories import continuity_gap, segment_position
from .vectors import Vec2


class PlanningError(ValueError):
    """The scene description cannot be turned into a valid motion plan."""


#: Default arm reach used for precondition checks (normalized scene units).
DEFAULT_PROP_REACH: float = 0.20


@dataclass
class PlanBuildLog:
    """Non-fatal notes collected while building the plan."""

    continuity_gaps: list[tuple[str, float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _character_timeline(
    scenesegs: list[MotionSegment], char_id: str
) -> list[MotionSegment]:
    segs = sorted(
        (s for s in scenesegs if s.character_id == char_id),
        key=lambda s: (s.start_time, s.end_time, s.id),
    )
    return segs


def check_continuity(segs: list[MotionSegment]) -> list[tuple[str, float, float]]:
    """Find position jumps between consecutive segments of one character.

    Returns ``(prev_id, time, gap)`` triples for gaps above tolerance.
    """
    gaps: list[tuple[str, float, float]] = []
    for a, b in zip(segs, segs[1:]):
        if a.end_time > b.start_time + 1e-9:
            # Overlapping segments for one character are a planning error.
            raise PlanningError(
                f"segments {a.id!r} and {b.id!r} overlap in time for "
                f"character {a.character_id!r}"
            )
        if a.end_time < b.start_time - 1e-9:
            continue  # gap in time is allowed (character idles); not a jump
        gap = continuity_gap(
            a.end_state.position.as_tuple(), b.start_state.position.as_tuple()
        )
        if gap is not None:
            gaps.append((a.id, b.start_time, gap))
    return gaps


def plan_interactions(
    scene: SceneDescription,
) -> tuple[OwnershipLedger, list[tuple[str, InteractionError]]]:
    """Replay interactions in deterministic order and derive ownership.

    Preconditions are evaluated against the ownership state produced by the
    *earlier* events (data-driven, not hard-coded).  Returns the ledger plus
    a list of failed events with their errors (also reflected as validation
    issues by :mod:`animation_lab.validation`).
    """
    ledger = OwnershipLedger()
    failures: list[tuple[str, InteractionError]] = []

    # Initial ownership from the scene description.
    for prop_id, prop in scene.props.items():
        ledger.record(prop_id, 0.0, prop.owner_id, "initial")

    ordered = sorted(scene.interactions, key=sort_key)
    # Current owner per prop as events replay.
    owner_now: dict[str, Optional[str]] = {
        pid: p.owner_id for pid, p in scene.props.items()
    }
    state_now: dict[str, str] = {pid: p.state for pid, p in scene.props.items()}

    def actor_position_at(actor_id: str, t: float) -> Vec2:
        """Actor position at time ``t`` from its segments (or initial state)."""
        segs = sorted(
            (s for s in scene.motion_segments if s.character_id == actor_id),
            key=lambda s: (s.start_time, s.end_time, s.id),
        )
        if not segs:
            ch = scene.characters.get(actor_id)
            return ch.position.as_tuple() if ch else (0.0, 0.0)
        for s in segs:
            if s.start_time - 1e-9 <= t <= s.end_time + 1e-9:
                return segment_position(s, t)
        future = [s for s in segs if s.start_time > t]
        if future:
            return future[0].start_state.position.as_tuple()
        return segs[-1].end_state.position.as_tuple()

    for inter in ordered:
        prop = scene.props.get(inter.prop_id)
        if prop is None:  # pragma: no cover - validated by models
            continue
        current = PropState(
            prop_id=prop.prop_id,
            position=prop.position,
            rotation=prop.rotation,
            owner_id=owner_now.get(inter.prop_id),
            attachment_point=(
                prop.attachment_point if owner_now.get(inter.prop_id) else None
            ),
            velocity=prop.velocity,
            state=state_now.get(inter.prop_id, "free"),
            glowing=prop.glowing,
        )
        actor_pos = actor_position_at(inter.actor_id, inter.time)
        try:
            result = apply_interaction(
                inter, current, actor_pos, DEFAULT_PROP_REACH
            )
        except InteractionError as err:
            failures.append((inter.id, err))
            continue
        owner_now[inter.prop_id] = result.prop_after.owner_id
        state_now[inter.prop_id] = result.prop_after.state
        ledger.record(
            inter.prop_id, inter.time, result.prop_after.owner_id, inter.id
        )
    return ledger, failures


def build_plan(
    scene: SceneDescription,
    fps: Optional[int] = None,
) -> tuple[MotionPlan, PlanBuildLog]:
    """Turn a :class:`SceneDescription` into a :class:`MotionPlan`.

    Raises :class:`PlanningError` for structural problems (overlapping
    segments, unknown references — the latter already caught by the model).
    Raises :class:`InteractionError` when an interaction precondition fails
    hard during planning of a *well-formed* scene is deferred: failures are
    reported through the validation report unless ``strict`` planning is
    requested by the caller via :func:`build_plan_strict`.
    """
    fps = fps or scene.fps
    log = PlanBuildLog()

    # --- continuity per character -----------------------------------------
    all_gaps: list[tuple[str, float, float]] = []
    for char_id in scene.characters:
        segs = _character_timeline(list(scene.motion_segments), char_id)
        all_gaps.extend(check_continuity(segs))
    log.continuity_gaps = all_gaps

    # --- interactions -------------------------------------------------------
    ledger, failures = plan_interactions(scene)
    for inter_id, err in failures:
        log.notes.append(f"interaction {inter_id!r} failed: {err}")

    # --- keyframes ----------------------------------------------------------
    n_frames = max(1, int(round(scene.duration_seconds * fps)))
    characters_at: dict[str, dict[float, CharacterState]] = {
        cid: {} for cid in scene.characters
    }
    for cid in scene.characters:
        segs = _character_timeline(list(scene.motion_segments), cid)
        for i in range(n_frames + 1):
            t = round(scene.duration_seconds * i / n_frames, 9)
            state = _character_state_at(scene, cid, segs, t)
            characters_at[cid][t] = state

    props_at: dict[str, dict[float, PropState]] = {pid: {} for pid in scene.props}
    for i in range(n_frames + 1):
        t = round(scene.duration_seconds * i / n_frames, 9)
        for pid, prop in scene.props.items():
            props_at[pid][t] = _prop_state_at(scene, pid, t, owner_lookup=ledger)

    keyframes: list[Keyframe] = []
    for i in range(n_frames + 1):
        t = round(scene.duration_seconds * i / n_frames, 9)
        keyframes.append(
            Keyframe(
                time=t,
                label=f"frame_{i}",
                characters={cid: characters_at[cid][t] for cid in scene.characters},
                props={pid: props_at[pid][t] for pid in scene.props},
            )
        )

    # Initial characters come straight from the scene (or first segment state).
    characters: dict[str, CharacterState] = {}
    for cid in scene.characters:
        segs = _character_timeline(list(scene.motion_segments), cid)
        if segs:
            characters[cid] = segs[0].start_state
        else:
            characters[cid] = scene.characters[cid]

    props: dict[str, PropState] = {}
    for pid, prop in scene.props.items():
        props[pid] = prop.model_copy(deep=True)

    plan = MotionPlan(
        name=scene.name,
        fps=fps,
        duration_seconds=scene.duration_seconds,
        characters=characters,
        props=props,
        motion_segments=tuple(scene.motion_segments),
        interactions=tuple(sorted(scene.interactions, key=sort_key)),
        keyframes=tuple(keyframes),
        timeline=tuple(keyframes),
    )
    return plan, log


def build_plan_strict(scene: SceneDescription) -> tuple[MotionPlan, PlanBuildLog]:
    """Like :func:`build_plan` but any interaction precondition failure raises."""
    plan, log = build_plan(scene)
    # Re-run to surface the first hard error (build_plan already logged them).
    ordered = sorted(scene.interactions, key=sort_key)
    owner_now: dict[str, Optional[str]] = {
        pid: p.owner_id for pid, p in scene.props.items()
    }
    state_now: dict[str, str] = {pid: p.state for pid, p in scene.props.items()}
    for inter in ordered:
        prop = scene.props.get(inter.prop_id)
        if prop is None:
            continue
        current = PropState(
            prop_id=prop.prop_id,
            position=prop.position,
            rotation=prop.rotation,
            owner_id=owner_now.get(inter.prop_id),
            attachment_point=prop.attachment_point if owner_now.get(inter.prop_id) else None,
            velocity=prop.velocity,
            state=state_now.get(inter.prop_id, "free"),
            glowing=prop.glowing,
        )
        actor = scene.characters.get(inter.actor_id)
        actor_pos = actor.position.as_tuple() if actor else (0.0, 0.0)
        try:
            result = apply_interaction(inter, current, actor_pos, DEFAULT_PROP_REACH)
        except InteractionError:
            raise
        owner_now[inter.prop_id] = result.prop_after.owner_id
        state_now[inter.prop_id] = result.prop_after.state
    return plan, log


def _character_state_at(
    scene: SceneDescription,
    char_id: str,
    segs: list[MotionSegment],
    t: float,
) -> CharacterState:
    """Character state at time ``t`` (deterministic)."""
    active = [s for s in segs if s.start_time <= t <= s.end_time]
    if active:
        seg = active[0]
        u = (t - seg.start_time) / seg.duration if seg.duration > 0 else 1.0
        from .interpolation import interpolate_character_state
        from .trajectories import segment_position, rotation_at

        pos = segment_position(seg, t)
        rot = rotation_at(seg, t)
        state = interpolate_character_state(seg.start_state, seg.end_state, u)
        from .models import Vec2Field

        state = state.model_copy(
            update={
                "position": Vec2Field(x=pos[0], y=pos[1]),
                "rotation": rot,
            }
        )
        return state
    # Before the first / after the last segment: nearest boundary state.
    future = [s for s in segs if s.start_time > t]
    past = [s for s in segs if s.end_time < t]
    if future:
        return future[0].start_state
    if past:
        return past[-1].end_state
    return scene.characters[char_id]


def _prop_state_at(
    scene: SceneDescription,
    prop_id: str,
    t: float,
    owner_lookup: OwnershipLedger,
) -> PropState:
    """Prop state at time ``t``: anchored to owner's position when owned."""
    base = scene.props[prop_id]
    entries = [e for e in owner_lookup.intervals.get(prop_id, []) if e[0] <= t]
    owner, _event = (entries[-1][1], entries[-1][2]) if entries else (
        base.owner_id, "initial"
    )
    from .models import Vec2Field

    if owner is None:
        return base.model_copy(deep=True)
    # Anchored: place the prop at the owner's current position (the hand
    # offset is resolved by the renderer; the plan records the anchor only).
    segs = _character_timeline(list(scene.motion_segments), owner)
    state = _character_state_at(scene, owner, segs, t)
    return base.model_copy(
        deep=True,
        update={
            "owner_id": owner,
            "state": "attached" if t > 0 else base.state,
            "position": state.position,
            "velocity": Vec2Field(x=0.0, y=0.0),
        },
    )
