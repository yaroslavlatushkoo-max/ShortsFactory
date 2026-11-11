"""interaction.py — prop pickup / attach / transfer / detach / release logic.

Every operation validates its preconditions and raises
:class:`InteractionError` when they fail — broken data is never silently
repaired.  Single-ownership invariant: at any instant a prop has at most one
owner; during ``transfer`` ownership moves atomically at the event time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Interaction, PropState
from .rig_2d import ATTACHMENT_POINTS
from .vectors import EPS, Vec2, add, distance, sub


class InteractionError(ValueError):
    """A violated precondition of an interaction event."""


@dataclass
class InteractionResult:
    """Outcome of applying an interaction at a point in time."""

    interaction_id: str
    kind: str
    time: float
    prop_before: PropState
    prop_after: PropState
    notes: tuple[str, ...] = ()


# Ordering used by the simulator when two events share the same timestamp.
_KIND_ORDER: dict[str, int] = {
    "pickup": 0, "attach": 1, "transfer": 2, "detach": 3, "release": 4,
}


def sort_key(inter: Interaction) -> tuple[float, int, str]:
    """Deterministic sort: time, then kind order, then id (lexicographic)."""
    return (inter.time, _KIND_ORDER.get(inter.kind, 99), inter.id)


def check_preconditions(
    inter: Interaction,
    prop: PropState,
    actor_position: Vec2,
    prop_reach: float,
    characters: Optional[set[str]] = None,
) -> None:
    """Validate that ``inter`` can be applied to the current world state.

    Raises :class:`InteractionError` describing the first violated rule.
    """
    if characters is not None and inter.actor_id not in characters:
        raise InteractionError(
            f"{inter.id}: unknown actor {inter.actor_id!r}"
        )
    if prop.prop_id != inter.prop_id:
        raise InteractionError(
            f"{inter.id}: event targets prop {inter.prop_id!r} but world "
            f"state carries {prop.prop_id!r}"
        )

    if inter.kind in ("pickup", "attach"):
        if prop.state not in ("free",):
            raise InteractionError(
                f"{inter.id}: cannot {inter.kind} prop {prop.prop_id!r} in "
                f"state {prop.state!r} (must be 'free'; currently owned by "
                f"{prop.owner_id!r})"
            )
        if prop.owner_id is not None:
            raise InteractionError(
                f"{inter.id}: prop {prop.prop_id!r} already owned by "
                f"{prop.owner_id!r}"
            )
        gap = distance(actor_position, prop.position.as_tuple())
        if gap > prop_reach + EPS:
            raise InteractionError(
                f"{inter.id}: prop {prop.prop_id!r} out of reach "
                f"(distance {gap:.4f} > reach {prop_reach:.4f})"
            )
        if inter.attachment_point and inter.attachment_point not in ATTACHMENT_POINTS:
            raise InteractionError(
                f"{inter.id}: unknown attachment point {inter.attachment_point!r}"
            )

    elif inter.kind == "transfer":
        if prop.owner_id is None:
            raise InteractionError(
                f"{inter.id}: cannot transfer unowned prop {prop.prop_id!r}"
            )
        if prop.owner_id != inter.actor_id:
            raise InteractionError(
                f"{inter.id}: prop {prop.prop_id!r} is owned by "
                f"{prop.owner_id!r}, not by actor {inter.actor_id!r}"
            )
        if inter.receiver_id is None:
            raise InteractionError(f"{inter.id}: transfer needs receiver_id")
        if inter.receiver_id == inter.actor_id:
            raise InteractionError(
                f"{inter.id}: actor cannot transfer a prop to themselves"
            )

    elif inter.kind in ("detach", "release"):
        if prop.owner_id is None:
            raise InteractionError(
                f"{inter.id}: cannot {inter.kind} unowned prop {prop.prop_id!r}"
            )
        if prop.owner_id != inter.actor_id:
            raise InteractionError(
                f"{inter.id}: prop {prop.prop_id!r} is owned by "
                f"{prop.owner_id!r}, not by actor {inter.actor_id!r}"
            )


def apply_interaction(
    inter: Interaction,
    prop: PropState,
    actor_position: Vec2,
    prop_reach: float,
    receiver_position: Optional[Vec2] = None,
) -> InteractionResult:
    """Apply an interaction to a prop state, validating preconditions first.

    Positional continuity: on pickup/attach the prop keeps its world position
    but gains an owner + attachment point; on transfer the world position is
    preserved while ownership flips; on release the prop keeps its world
    position and inherits the actor's velocity so the free flight continues
    from the same state.
    """
    check_preconditions(inter, prop, actor_position, prop_reach)

    before = prop.model_copy(deep=True)
    notes: list[str] = []

    if inter.kind in ("pickup", "attach"):
        after = prop.model_copy(deep=True, update={
            "owner_id": inter.actor_id,
            "attachment_point": inter.attachment_point or "hand_r",
            "state": "attached",
            "velocity": (0.0, 0.0),
        })
        notes.append(f"ownership -> {inter.actor_id!r}, anchored to "
                     f"{after.attachment_point!r}")

    elif inter.kind == "transfer":
        assert inter.receiver_id is not None
        after = prop.model_copy(deep=True, update={
            "owner_id": inter.receiver_id,
            "state": "transferring",
        })
        notes.append(f"ownership {inter.actor_id!r} -> {inter.receiver_id!r}")

    elif inter.kind == "detach":
        after = prop.model_copy(deep=True, update={
            "owner_id": None,
            "attachment_point": None,
            "state": "released",
        })
        notes.append("detached, stays where it is")

    elif inter.kind == "release":
        after = prop.model_copy(deep=True, update={
            "owner_id": None,
            "attachment_point": None,
            "state": "released",
        })
        notes.append("released with actor velocity inherited")

    return InteractionResult(
        interaction_id=inter.id,
        kind=inter.kind,
        time=inter.time,
        prop_before=before,
        prop_after=after,
        notes=tuple(notes),
    )


@dataclass
class OwnershipLedger:
    """Tracks ownership intervals per prop across the whole timeline."""

    intervals: dict[str, list[tuple[float, Optional[str], str]]] = field(
        default_factory=dict
    )  # prop_id -> [(time, owner or None, event_id)]

    def record(self, prop_id: str, time: float, owner: Optional[str], event_id: str) -> None:
        self.intervals.setdefault(prop_id, []).append(
            (round(float(time), 9), owner, event_id)
        )

    def conflicts(self) -> list[tuple[str, float, str, str]]:
        """Detect double-ownership conflicts: two different owners recorded
        at the *same* timestamp for the same prop, or a re-pickup without a
        release in between."""
        out: list[tuple[str, float, str, str]] = []
        for prop_id, entries in self.intervals.items():
            entries = sorted(entries, key=lambda e: e[0])
            for i in range(1, len(entries)):
                t0, o0, e0 = entries[i - 1]
                t1, o1, e1 = entries[i]
                if t0 == t1 and o0 is not None and o1 is not None and o0 != o1:
                    out.append((prop_id, t0, e0, e1))
            # Overlapping ownership: new owner assigned while still owned.
            for i in range(1, len(entries)):
                t0, o0, e0 = entries[i - 1]
                t1, o1, e1 = entries[i]
                if t1 > t0 and o0 is not None and o1 is not None and o1 != o0:
                    out.append((prop_id, t1, e0, e1))
        return out
