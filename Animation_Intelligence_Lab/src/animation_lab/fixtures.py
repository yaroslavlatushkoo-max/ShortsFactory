"""fixtures.py — built-in demo scenes: the correct handoff and a broken one.

``demo_handoff_scene`` implements the reference scenario:

    Character A stands on the left, character B on the right.  A walks to B,
    turns to face B, reaches for the glowing orb, picks it up, holds it,
    transfers it to B.  B takes the orb, examines it, reacts with surprise
    and steps back.  A returns to idle.

``broken_handoff_scene`` is intentionally malformed so the validator must
detect concrete problems (state transitions without pickup backing,
unreachable prop, position jump).
"""

from __future__ import annotations

from typing import Any

from .models import (
    CharacterState,
    Interaction,
    InteractionPrecondition,
    MotionSegment,
    PropState,
    SceneDescription,
    TrajectorySpec,
    Vec2Field,
)


def _state(
    cid: str,
    pos: tuple[float, float],
    rotation: float = 0.0,
    action: str | None = None,
    emotion: str = "neutral",
    gaze: tuple[float, float] | None = None,
) -> CharacterState:
    return CharacterState(
        character_id=cid,
        position=Vec2Field(x=pos[0], y=pos[1]),
        rotation=rotation,
        emotion=emotion,  # type: ignore[arg-type]
        current_action=action,
        gaze_target=Vec2Field(x=gaze[0], y=gaze[1]) if gaze else None,
    )


def demo_handoff_scene() -> SceneDescription:
    """Reference scenario: A walks to B and hands over the glowing orb."""
    timeline: list[MotionSegment] = []
    interactions: list[Interaction] = []

    def seg(
        id: str,
        cid: str,
        action: str,
        t0: float,
        t1: float,
        p0: tuple[float, float],
        p1: tuple[float, float],
        rot0: float,
        rot1: float,
        easing: str = "ease_in_out",
        traj: TrajectorySpec | None = None,
        emotion0: str = "neutral",
        emotion1: str = "neutral",
    ) -> None:
        timeline.append(
            MotionSegment(
                id=id,
                character_id=cid,
                action_type=action,  # type: ignore[arg-type]
                start_time=t0,
                end_time=t1,
                start_state=_state(cid, p0, rot0, action, emotion0),
                end_state=_state(cid, p1, rot1, action, emotion1),
                trajectory=traj or TrajectorySpec(easing=easing),  # type: ignore[arg-type]
                easing=easing,  # type: ignore[arg-type]
            )
        )

    # --- character A: walk from left toward the orb position ----------------
    seg("a_walk", "char_a", "walk", 0.2, 2.0,
        (0.15, 0.55), (0.55, 0.55), 90.0, 90.0, easing="ease_in_out")
    seg("a_turn", "char_a", "turn", 2.0, 2.4,
        (0.55, 0.55), (0.55, 0.55), 90.0, 90.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))
    # --- character A: reach down to the orb ---------------------------------
    seg("a_reach", "char_a", "reach", 2.4, 2.9,
        (0.55, 0.55), (0.55, 0.55), 90.0, 90.0, easing="ease_in_out",
        traj=TrajectorySpec(kind="hold"))
    seg("a_hold", "char_a", "hold", 2.9, 3.4,
        (0.55, 0.55), (0.55, 0.55), 90.0, 90.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))
    # --- handoff moment (interaction at 3.4) --------------------------------
    seg("a_handoff", "char_a", "handoff", 3.4, 3.8,
        (0.55, 0.55), (0.62, 0.55), 90.0, 90.0, easing="ease_in_out")
    seg("a_idle_after", "char_a", "idle", 3.8, 6.0,
        (0.62, 0.55), (0.62, 0.55), 90.0, 90.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))
    # --- character B: receives, examines, reacts, steps back ----------------
    seg("b_wait", "char_b", "idle", 0.0, 3.4,
        (0.85, 0.55), (0.85, 0.55), 270.0, 270.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))
    seg("b_receive", "char_b", "hold", 3.4, 3.9,
        (0.85, 0.55), (0.85, 0.55), 270.0, 270.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))
    seg("b_examine", "char_b", "speak", 3.9, 4.6,
        (0.85, 0.55), (0.85, 0.55), 270.0, 270.0, easing="linear",
        traj=TrajectorySpec(kind="hold"), emotion0="curious", emotion1="curious")
    seg("b_react", "char_b", "react", 4.6, 5.1,
        (0.85, 0.55), (0.85, 0.55), 270.0, 270.0, easing="ease_out",
        traj=TrajectorySpec(kind="hold"), emotion0="surprised",
        emotion1="surprised")
    seg("b_step_back", "char_b", "step_back", 5.1, 5.7,
        (0.85, 0.55), (0.93, 0.55), 270.0, 270.0, easing="ease_out")
    seg("b_idle_end", "char_b", "idle", 5.7, 6.0,
        (0.93, 0.55), (0.93, 0.55), 270.0, 270.0, easing="linear",
        traj=TrajectorySpec(kind="hold"))

    interactions = [
        Interaction(
            id="i_pickup", kind="pickup", time=2.9,
            actor_id="char_a", prop_id="orb_glow", attachment_point="hand_r",
            preconditions=(
                InteractionPrecondition(
                    check="prop_is_free",
                    description="orb must rest free on the ground",
                ),
                InteractionPrecondition(
                    check="actor_near_prop",
                    description="actor A must be within arm reach",
                ),
            ),
            expected_prop_state_after="attached",
            expected_owner_after="char_a",
        ),
        Interaction(
            id="i_transfer", kind="transfer", time=3.4,
            actor_id="char_a", prop_id="orb_glow", receiver_id="char_b",
            attachment_point="hand_r",
            preconditions=(
                InteractionPrecondition(
                    check="owner_is_actor",
                    description="orb must belong to A before the transfer",
                ),
            ),
            expected_prop_state_after="transferring",
            expected_owner_after="char_b",
        ),
    ]

    return SceneDescription(
        schema_version="1.0.0",
        name="glowing_orb_handoff",
        fps=30,
        duration_seconds=6.0,
        characters={
            "char_a": _state("char_a", (0.15, 0.55), 90.0, "idle",
                             gaze=(0.55, 0.55)),
            "char_b": _state("char_b", (0.85, 0.55), 270.0, "idle",
                             gaze=(0.55, 0.55)),
        },
        props={
            "orb_glow": PropState(
                prop_id="orb_glow",
                position=Vec2Field(x=0.62, y=0.60),
                state="free",
                glowing=True,
            )
        },
        motion_segments=timeline,
        interactions=interactions,
    )


def broken_handoff_scene() -> SceneDescription:
    """Intentionally flawed scene the validator must catch.

    Flaws:
    * ``ghost_pickup`` — B reaches then holds, but no pickup interaction
      exists for him (UNBACKED_EVENT_TRANSITION);
    * ``far_pickup`` — A tries to grab the orb from across the scene
      (TARGET_UNREACHABLE);
    * ``teleport`` — A's position jumps between segments
      (TRAJECTORY_DISCONTINUITY);
    * a double-claim of the orb at the same instant (DOUBLE_OWNERSHIP).
    """
    segs: list[MotionSegment] = []
    interactions: list[Interaction] = []

    def seg(
        id: str, cid: str, action: str, t0: float, t1: float,
        p0: tuple[float, float], p1: tuple[float, float],
        rot: float = 90.0,
    ) -> None:
        segs.append(
            MotionSegment(
                id=id,
                character_id=cid,
                action_type=action,  # type: ignore[arg-type]
                start_time=t0,
                end_time=t1,
                start_state=_state(cid, p0, rot, action),
                end_state=_state(cid, p1, rot, action),
            )
        )

    # A walks, then teleports (position jump), then grabs from too far away.
    seg("b_walk", "char_b", "walk", 0.0, 1.0, (0.20, 0.40), (0.50, 0.40))
    seg("teleport", "char_b", "walk", 1.0, 1.5, (0.90, 0.40), (0.92, 0.40))
    seg("far_pickup", "char_b", "reach", 1.5, 2.0, (0.92, 0.40), (0.92, 0.40))
    seg("b_hold", "char_b", "hold", 2.0, 2.5, (0.92, 0.40), (0.92, 0.40))
    seg("ghost_hold", "char_b", "hold", 2.5, 3.0, (0.92, 0.40), (0.92, 0.40))
    seg("idle_tail", "char_b", "idle", 3.0, 4.0, (0.92, 0.40), (0.92, 0.40))

    # Two actors claim the orb at the same instant, from far away.
    interactions = [
        Interaction(
            id="claim_a", kind="pickup", time=2.0,
            actor_id="char_a", prop_id="orb_glow",
            expected_prop_state_after="attached", expected_owner_after="char_a",
        ),
        Interaction(
            id="claim_b", kind="pickup", time=2.0,
            actor_id="char_b", prop_id="orb_glow",
            expected_prop_state_after="attached", expected_owner_after="char_b",
        ),
    ]

    return SceneDescription(
        schema_version="1.0.0",
        name="broken_handoff_negative_test",
        fps=30,
        duration_seconds=4.0,
        characters={
            "char_a": _state("char_a", (0.10, 0.80), 90.0, "idle"),
            "char_b": _state("char_b", (0.20, 0.40), 90.0, "idle"),
        },
        props={
            "orb_glow": PropState(
                prop_id="orb_glow",
                position=Vec2Field(x=0.55, y=0.60),
                state="free",
                glowing=True,
            )
        },
        motion_segments=segs,
        interactions=interactions,
    )


def simple_walk_scene() -> SceneDescription:
    """Minimal scene: one character walks across the scene and stops."""
    seg = MotionSegment(
        id="walk_1",
        character_id="walker",
        action_type="walk",
        start_time=0.0,
        end_time=3.0,
        start_state=_state("walker", (0.10, 0.50), 90.0, "walk"),
        end_state=_state("walker", (0.80, 0.50), 90.0, "idle"),
        trajectory=TrajectorySpec(kind="linear", easing="ease_in_out"),
        easing="ease_in_out",
    )
    return SceneDescription(
        schema_version="1.0.0",
        name="simple_walk",
        fps=30,
        duration_seconds=3.5,
        characters={"walker": _state("walker", (0.10, 0.50), 90.0, "idle")},
        motion_segments=(seg,),
    )


SCENE_BUILDERS: dict[str, Any] = {
    "object_handoff": demo_handoff_scene,
    "simple_walk": simple_walk_scene,
    "broken_handoff": broken_handoff_scene,
}
