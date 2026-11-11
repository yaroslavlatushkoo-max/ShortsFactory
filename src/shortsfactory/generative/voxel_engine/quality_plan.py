"""Voxel engine — Mission 7 quality demo plan.

A single, deliberate 8–10 s scene ("Hero finds a mysterious cube") used for the
quality reset: one hero, one object, one small location, no dialogue.  The
rhythm follows the Mission 7 brief:
  0–2 s idle / 2–4 s notice→turn / 4–6 s walk→stop→bend /
  6–8 s pickup→react / final expressive hold pose.
"""

from __future__ import annotations

from ..animation_plan import (
    AnimationPlan,
    PlanAction,
    PlanCameraKey,
    PlanCharacter,
    PlanEvent,
    PlanObject,
)

IDEA = "Герой находит загадочный куб"


def build_quality_cube_plan(duration: float = 9.0, width: int = 1080, height: int = 1920, seed: int = 20261027) -> AnimationPlan:
    total = min(max(float(duration), 8.0), 10.0)

    hero = PlanCharacter(
        id="hero",
        name="Герой",
        rig="humanoid_v1",
        x=0.42,
        y=0.60,
        scale=1.0,
        facing=1,
        z=20,
        appear_at=0.0,
        palette={
            "skin": "#e8b48c",
            "hair": "#3a2a20",
            "shirt": "#2a6b5e",
            "pants": "#1f4f46",
            "eyes": "#4a6b8a",
            "shoes": "#22242c",
            "trim": "#1a3a34",
        },
    )
    cube = PlanObject(
        id="cube",
        kind="gem",
        z=15,
        appear_at=0.0,
        color="#8df0b4",
        keyframes=[
            {"t": 0.0, "x": 0.62, "y": 0.55, "scale": 0.5, "rotation": 0.0},
            {"t": 6.6, "x": 0.62, "y": 0.55, "scale": 0.5, "rotation": 0.0},
        ],
    )

    # ---- rhythm (seconds) ----
    t_idle0, t_idle1 = 0.0, 2.0
    t_turn0, t_turn1 = 2.0, 3.2
    t_walk0, t_walk1 = 3.2, 4.6
    t_bend0, t_bend1 = 4.6, 5.8
    t_pick0, t_pick1 = 5.8, 7.2
    t_attach = 6.6
    t_react0, t_react1 = 7.2, 8.4
    t_hold0, t_hold1 = 8.4, total

    actions = [
        PlanAction(character="hero", action="idle", start=t_idle0, duration=t_idle1 - t_idle0,
                   params={"sway": 0.03}),
        PlanAction(character="hero", action="turn", start=t_turn0, duration=t_turn1 - t_turn0,
                   params={"to_facing": 1}),
        PlanAction(character="hero", action="walk", start=t_walk0, duration=t_walk1 - t_walk0,
                   params={"to_x": 0.62, "cycles": 2.6, "stride": 0.45}),
        PlanAction(character="hero", action="bend", start=t_bend0, duration=t_bend1 - t_bend0,
                   params={"intensity": 1.0}),
        PlanAction(character="hero", action="pickup", start=t_pick0, duration=t_pick1 - t_pick0,
                   params={"intensity": 1.0}),
        PlanAction(character="hero", action="react", start=t_react0, duration=t_react1 - t_react0,
                   params={"intensity": 1.0}),
        PlanAction(character="hero", action="hold", start=t_hold0, duration=t_hold1 - t_hold0,
                   params={"intensity": 1.0}),
    ]

    events = [
        PlanEvent(t=t_attach, kind="attach", object="cube", character="hero",
                 point="hand_r", offset=(0.0, 0.0), scale=0.5),
    ]

    # plan camera keys (used by validation; the voxel camera uses its own shots)
    camera = [
        PlanCameraKey(t=0.0, x=0.50, y=0.55, zoom=1.0),
        PlanCameraKey(t=t_walk0, x=0.52, y=0.55, zoom=1.0),
        PlanCameraKey(t=t_attach, x=0.55, y=0.58, zoom=1.05),
        PlanCameraKey(t=t_react1, x=0.54, y=0.57, zoom=1.1),
        PlanCameraKey(t=total - 0.2, x=0.54, y=0.57, zoom=1.15),
    ]

    return AnimationPlan(
        scene_id="quality_cube_01",
        title="Герой находит загадочный куб",
        duration=round(total, 2),
        fps=30,
        width=width,
        height=height,
        environment="forest",
        characters=[hero],
        objects=[cube],
        actions=actions,
        events=events,
        camera=camera,
        contact_times=[t_attach, t_react0, t_hold0],
        blink_times=[1.0, 3.0, 6.0, 8.0],
        seed=seed,
        blend=0.12,
        extra_actions={"bend", "hold", "fall", "recover"},
    )
