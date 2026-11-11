"""simulation.py — deterministic simulator over a MotionPlan.

Given a plan, the simulator reproduces character/prop states at any time
exactly (pure functions of time).  Event logs are ordered by
(time, kind-order, id) so identical inputs always produce identical logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import (
    CharacterState,
    Interaction,
    Keyframe,
    MotionPlan,
    PropState,
)
from .motion_planner import (
    DEFAULT_PROP_REACH,
    _character_state_at,
    _prop_state_at,
    _character_timeline,
    plan_interactions,
)
from .interaction import OwnershipLedger
from .vectors import Vec2


@dataclass(frozen=True)
class SimEvent:
    """One entry of the simulation event log."""

    time: float
    kind: str
    subject_id: str
    description: str
    order_index: int


@dataclass
class SimulationResult:
    """Full outcome of one simulation run."""

    plan_name: str
    fps: int
    duration_seconds: float
    frames: tuple[Keyframe, ...]
    events: tuple[SimEvent, ...] = ()
    ledger: OwnershipLedger = field(default_factory=OwnershipLedger)


class Simulator:
    """Deterministic simulator for a :class:`MotionPlan`."""

    def __init__(self, plan: MotionPlan) -> None:
        self.plan = plan

    # ------------------------------------------------------------------
    def state_at(self, time: float) -> tuple[dict[str, CharacterState], dict[str, PropState]]:
        """Full scene state at ``time`` seconds (deterministic, repeatable)."""
        if time < 0 or time > self.plan.duration_seconds + 1e-9:
            raise ValueError(
                f"time {time} outside plan duration [0, {self.plan.duration_seconds}]"
            )
        # Stable, ordered evaluation: characters then props, in id order.
        chars: dict[str, CharacterState] = {}
        for cid in sorted(self.plan.characters):
            segs = _character_timeline(list(self.plan.motion_segments), cid)
            base = self.plan.characters[cid]
            if segs:
                chars[cid] = _character_state_at_from_segments(base, segs, time)
            else:
                chars[cid] = base.model_copy(deep=True)
        props: dict[str, PropState] = {}
        for pid in sorted(self.plan.props):
            props[pid] = self.prop_state_at(pid, time, chars)
        return chars, props

    def prop_state_at(
        self, prop_id: str, time: float, chars: dict[str, CharacterState]
    ) -> PropState:
        """Prop state at ``time``, anchored to its owner's position when owned."""
        base = self.plan.props[prop_id]
        # Recompute ownership at ``time`` from interaction events.
        owner: Optional[str] = base.owner_id
        for inter in self.plan.interactions:
            if inter.prop_id != prop_id or inter.time > time + 1e-9:
                continue
            if inter.kind in ("pickup", "attach"):
                owner = inter.actor_id
            elif inter.kind == "transfer":
                owner = inter.receiver_id
            elif inter.kind in ("detach", "release"):
                owner = None
        if owner is None:
            return base.model_copy(deep=True)
        state = base.model_copy(deep=True)
        anchor = chars[owner] if owner in chars else None
        from .models import Vec2Field

        if anchor is not None:
            state = state.model_copy(
                update={
                    "owner_id": owner,
                    "state": "attached",
                    "position": anchor.position,
                    "velocity": Vec2Field(x=0.0, y=0.0),
                }
            )
        else:
            state = state.model_copy(update={"owner_id": owner, "state": "attached"})
        return state

    # ------------------------------------------------------------------
    def build_event_log(self) -> list[SimEvent]:
        """Deterministic event log: sorted by (time, kind order, id)."""
        events: list[SimEvent] = []
        idx = 0
        for inter in sorted(self.plan.interactions, key=lambda i: (i.time, i.kind, i.id)):
            events.append(
                SimEvent(
                    time=inter.time,
                    kind=inter.kind,
                    subject_id=inter.prop_id,
                    description=(
                        f"{inter.actor_id} {inter.kind} {inter.prop_id}"
                        + (f" -> {inter.receiver_id}" if inter.receiver_id else "")
                    ),
                    order_index=idx,
                )
            )
            idx += 1
        for seg in sorted(
            self.plan.motion_segments,
            key=lambda s: (s.start_time, s.action_type, s.id),
        ):
            events.append(
                SimEvent(
                    time=seg.start_time,
                    kind="segment_start",
                    subject_id=seg.character_id,
                    description=(
                        f"{seg.character_id} starts {seg.action_type!r} "
                        f"({seg.id}), ends {seg.end_time:.3f}s"
                    ),
                    order_index=idx,
                )
            )
            idx += 1
        events.sort(key=lambda e: (e.time, e.kind, e.order_index))
        return events

    # ------------------------------------------------------------------
    def run(self, fps: Optional[int] = None) -> SimulationResult:
        """Sample the whole timeline at ``fps`` (plan fps by default)."""
        fps = fps or self.plan.fps
        if fps < 1:
            raise ValueError("fps must be >= 1")
        n = max(1, int(round(self.plan.duration_seconds * fps)))
        frames: list[Keyframe] = []
        for i in range(n + 1):
            t = round(self.plan.duration_seconds * i / n, 9)
            chars, props = self.state_at(t)
            frames.append(
                Keyframe(time=t, label=f"frame_{i}", characters=chars, props=props)
            )
        ledger, _failures = plan_interactions(_plan_to_scene_like(self.plan))
        return SimulationResult(
            plan_name=self.plan.name,
            fps=fps,
            duration_seconds=self.plan.duration_seconds,
            frames=tuple(frames),
            events=tuple(self.build_event_log()),
            ledger=ledger,
        )


def _plan_to_scene_like(plan: MotionPlan):
    """Wrap a plan so :func:`plan_interactions` can replay its events."""

    class _SceneLike:
        pass

    s = _SceneLike()
    s.props = {pid: p.model_copy(deep=True) for pid, p in plan.props.items()}
    s.characters = {cid: c.model_copy(deep=True) for cid, c in plan.characters.items()}
    s.interactions = list(plan.interactions)
    s.motion_segments = list(plan.motion_segments)
    return s


def _character_state_at_from_segments(
    base: CharacterState,
    segs: list,
    t: float,
) -> CharacterState:
    """Evaluate one character's state at ``t`` from its segments."""
    active = [s for s in segs if s.start_time <= t <= s.end_time]
    if active:
        seg = active[0]
        from .interpolation import interpolate_character_state
        from .models import Vec2Field
        from .trajectories import rotation_at, segment_position

        u = (t - seg.start_time) / seg.duration if seg.duration > 0 else 1.0
        pos = segment_position(seg, t)
        rot = rotation_at(seg, t)
        state = interpolate_character_state(seg.start_state, seg.end_state, u)
        return state.model_copy(
            update={"position": Vec2Field(x=pos[0], y=pos[1]), "rotation": rot}
        )
    future = [s for s in segs if s.start_time > t]
    past = [s for s in segs if s.end_time < t]
    if future:
        return future[0].start_state
    if past:
        return past[-1].end_state
    return base.model_copy(deep=True)


def frame_csv(result: SimulationResult) -> str:
    """Serialize sampled frames to CSV (positions of characters and props)."""
    header = ["time"]
    char_ids = sorted(result.frames[0].characters) if result.frames else []
    prop_ids = sorted(result.frames[0].props) if result.frames else []
    header += [f"char.{c}.x" for c in char_ids]
    header += [f"char.{c}.y" for c in char_ids]
    header += [f"prop.{p}.x" for p in prop_ids]
    header += [f"prop.{p}.y" for p in prop_ids]
    lines = [",".join(header)]
    for f in result.frames:
        row = [f"{f.time:.4f}"]
        row += [f"{f.characters[c].position.x:.6f}" for c in char_ids]
        row += [f"{f.characters[c].position.y:.6f}" for c in char_ids]
        row += [f"{f.props[p].position.x:.6f}" for p in prop_ids]
        row += [f"{f.props[p].position.y:.6f}" for p in prop_ids]
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"
