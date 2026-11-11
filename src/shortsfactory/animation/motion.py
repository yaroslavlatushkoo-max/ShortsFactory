"""Keyframe interpolation, reusable poses and the built-in action library.

Every action maps normalised progress ``u`` in ``[0, 1]`` to a partial pose
(joint angles in radians plus a vertical hip offset). The engine blends
overlapping actions and resolves the horizontal position as a chain so the
character walks *to* a place and stays there.

Actions are built from named poses (:data:`POSES`). A pose is a plain dict of
joint angles, so the same "reach", "offer" or "surprise" can be reused by any
action or scene. Coordination is deliberate: reaching for an object moves the
torso, shoulder, elbow, wrist and head together, not just one limb.

Poses are deterministic: no randomness, no external models. Blinking is
procedural, and mouth movement is driven by the loudness envelope of the
synthesized voice track (see :mod:`shortsfactory.animation.voice`); there is
no phoneme recognition. See :data:`UNSUPPORTED` for what is missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

#: Movement types that are explicitly *not* implemented.
UNSUPPORTED: dict[str, str] = {
    "climb": "нет опоры/скалолазания",
    "swim": "нет водной среды",
    "fly": "нет полёта",
    "drive": "нет транспорта и посадки в него",
    "ride": "нет езды верхом",
}

TAU = math.tau


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def smoothstep(u: float) -> float:
    u = clamp(u)
    return u * u * (3.0 - 2.0 * u)


def ease(u: float, kind: str = "smooth") -> float:
    u = clamp(u)
    if kind == "linear":
        return u
    if kind == "ease_in":
        return u * u
    if kind == "ease_out":
        return 1.0 - (1.0 - u) ** 2
    return smoothstep(u)


@dataclass
class ActionState:
    """Result of evaluating an action at a given progress."""

    angles: dict[str, float] = field(default_factory=dict)
    root_x: float | None = None       # stage-normalised horizontal position
    root_dx: float = 0.0             # temporary horizontal offset, stage units
    root_dy: float = 0.0             # hip offset, fractions of character height
    facing: int = 0                  # 0 = keep, else +1 / -1 override
    mouth: float = 0.0
    blink: float = 1.0
    expression: str = ""             # neutral/surprise/joy/confused/annoyed
    gaze: float = 0.0                # -1 .. 1, pupil offset


# --------------------------------------------------------------- pose library

#: Reusable poses. Angles are local to the rig (see :mod:`rig`), in radians.
#: A positive arm angle swings a down-pointing limb towards the character's
#: forward direction.
POSES: dict[str, dict[str, float]] = {
    "neutral": {
        "torso": 0.0,
        "head": 0.0,
        "arm_l": 0.05,
        "arm_r": 0.05,
        "forearm_l": -0.12,
        "forearm_r": -0.12,
    },
    "reach_forward": {
        "torso": -0.40,
        "hips": 0.08,
        "head": -0.10,
        "arm_r": 0.90,
        "forearm_r": -0.28,
        "hand_r": 0.10,
        "arm_l": 0.15,
        "forearm_l": -0.40,
        "hand_l": 0.10,
        "leg_l": -0.10,
        "leg_r": 0.10,
        "shin_r": -0.18,
    },
    "offer": {
        "torso": -0.10,
        "head": 0.04,
        "arm_r": 0.95,
        "forearm_r": -0.20,
        "hand_r": 0.0,
        "arm_l": 0.12,
        "forearm_l": -0.20,
    },
    "carry": {
        "torso": 0.04,
        "head": -0.08,
        "arm_r": 0.35,
        "forearm_r": 1.30,
        "hand_r": 0.12,
        "arm_l": 0.08,
        "forearm_l": -0.18,
    },
    "point": {
        "torso": 0.06,
        "head": -0.06,
        "arm_r": 1.60,
        "forearm_r": 0.10,
        "hand_r": -0.05,
        "arm_l": 0.12,
        "forearm_l": -0.15,
    },
    "surprise": {
        "torso": -0.12,
        "hips": 0.05,
        "head": -0.10,
        "arm_l": 1.25,
        "arm_r": 1.25,
        "forearm_l": 0.55,
        "forearm_r": 0.55,
        "hand_l": 0.35,
        "hand_r": 0.35,
        "leg_l": -0.10,
        "leg_r": 0.10,
        "shin_l": -0.12,
        "shin_r": -0.12,
    },
    "joy": {
        "torso": -0.04,
        "head": -0.06,
        "arm_l": 2.05,
        "arm_r": 2.05,
        "forearm_l": 0.25,
        "forearm_r": 0.25,
        "hand_l": 0.15,
        "hand_r": 0.15,
    },
    "confused": {
        "torso": 0.05,
        "head": 0.16,
        "arm_r": 0.70,
        "forearm_r": 1.70,
        "hand_r": 0.15,
        "arm_l": 0.14,
        "forearm_l": -0.25,
    },
    "annoyed": {
        "torso": 0.04,
        "head": -0.10,
        "arm_l": 0.45,
        "arm_r": 0.45,
        "forearm_l": -0.55,
        "forearm_r": -0.55,
        "hand_l": 0.05,
        "hand_r": 0.05,
    },
    "crouch": {
        "torso": -0.30,
        "hips": 0.06,
        "head": 0.10,
        "leg_l": 0.82,
        "leg_r": 0.82,
        "shin_l": -1.42,
        "shin_r": -1.42,
        "arm_l": 0.38,
        "arm_r": 0.38,
        "forearm_l": 0.22,
        "forearm_r": 0.22,
    },
    "sit": {
        "torso": 0.05,
        "leg_l": 1.32,
        "leg_r": 1.32,
        "shin_l": -1.30,
        "shin_r": -1.30,
        "arm_l": 0.22,
        "arm_r": 0.22,
        "forearm_l": 0.55,
        "forearm_r": 0.55,
    },
    "pickup": {
        "torso": -0.64,
        "hips": 0.12,
        "head": 0.22,
        "arm_r": 0.82,
        "forearm_r": 0.16,
        "hand_r": 0.10,
        "arm_l": 0.26,
        "forearm_l": -0.30,
        "leg_l": 0.74,
        "leg_r": 0.74,
        "shin_l": -1.30,
        "shin_r": -1.30,
    },
    "shrug": {
        "torso": 0.03,
        "head": 0.10,
        "arm_l": 0.95,
        "arm_r": 0.95,
        "forearm_l": 1.42,
        "forearm_r": 1.42,
        "hand_l": 0.35,
        "hand_r": 0.35,
    },
    "clap": {
        "torso": -0.04,
        "head": -0.04,
        "arm_l": 1.05,
        "arm_r": 1.05,
        "forearm_l": 1.05,
        "forearm_r": 1.05,
        "hand_l": -0.20,
        "hand_r": -0.20,
    },
    "laugh": {
        "torso": -0.12,
        "head": 0.14,
        "arm_l": 0.50,
        "arm_r": 0.50,
        "forearm_l": -0.30,
        "forearm_r": -0.30,
        "hand_l": 0.10,
        "hand_r": 0.10,
        "leg_l": -0.05,
        "leg_r": 0.05,
    },
    "cry": {
        "torso": -0.15,
        "head": 0.18,
        "arm_l": 1.35,
        "arm_r": 1.35,
        "forearm_l": 1.85,
        "forearm_r": 1.85,
        "hand_l": 0.30,
        "hand_r": 0.30,
    },
    "stumble": {
        "torso": -0.55,
        "head": 0.20,
        "arm_l": 0.70,
        "arm_r": 0.55,
        "forearm_l": 0.15,
        "forearm_r": 0.55,
        "leg_l": -0.50,
        "leg_r": 0.45,
        "shin_l": 0.20,
        "shin_r": -0.50,
    },
    "dodge": {
        "torso": 0.25,
        "head": 0.10,
        "arm_l": 1.10,
        "arm_r": 0.60,
        "forearm_l": 0.60,
        "forearm_r": 0.90,
        "leg_l": 0.30,
        "leg_r": -0.30,
    },
    "dance": {
        "torso": 0.12,
        "head": -0.10,
        "arm_l": 2.10,
        "arm_r": 1.40,
        "forearm_l": 0.40,
        "forearm_r": 0.90,
        "hand_l": 0.30,
        "hand_r": 0.20,
        "leg_l": -0.20,
        "leg_r": 0.20,
    },
    "fight": {
        "torso": -0.12,
        "head": -0.05,
        "arm_l": 1.25,
        "arm_r": 1.35,
        "forearm_l": 1.50,
        "forearm_r": 1.60,
        "hand_l": 0.20,
        "hand_r": 0.20,
        "leg_l": -0.25,
        "leg_r": 0.25,
        "shin_l": 0.10,
        "shin_r": -0.10,
    },
    "stop": {
        "torso": 0.05,
        "head": 0.0,
        "leg_l": -0.20,
        "leg_r": 0.20,
        "shin_l": -0.10,
        "shin_r": -0.10,
        "arm_l": 0.10,
        "arm_r": 0.10,
        "forearm_l": -0.15,
        "forearm_r": -0.15,
    },
}

POSE_NAMES: tuple[str, ...] = tuple(sorted(POSES))

#: Expression used by a pose unless the action overrides it.
POSE_EXPRESSION: dict[str, str] = {
    "surprise": "surprise",
    "joy": "joy",
    "confused": "confused",
    "annoyed": "annoyed",
}


def pose_angles(name: str, intensity: float = 1.0) -> dict[str, float]:
    """Angles of a named pose, scaled towards neutral by ``intensity``."""
    if name not in POSES:
        raise KeyError(name)
    scale = clamp(intensity, 0.0, 2.0)
    base = POSES["neutral"]
    angles = dict(base)
    for joint, value in POSES[name].items():
        rest = base.get(joint, 0.0)
        angles[joint] = rest + (value - rest) * scale
    return angles


def rest_state(base_x: float) -> ActionState:
    return ActionState(angles={}, root_x=base_x, root_dy=0.0, facing=0, mouth=0.0, blink=1.0)


# ------------------------------------------------------------------ actions


def _idle(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    sway = float(params.get("sway", 0.035))
    breathe = math.sin(TAU * clamp(u)) * sway
    return ActionState(
        angles={
            "torso": breathe * 0.5,
            "head": -breathe * 0.4,
            "arm_l": 0.05 + breathe * 0.3,
            "arm_r": 0.05 - breathe * 0.3,
            "forearm_l": -0.12,
            "forearm_r": -0.12,
        },
        root_x=base_x,
        root_dy=0.004 * math.sin(TAU * clamp(u) + 1.0),
    )


def _walk(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    cycles = float(params.get("cycles", 3.0))
    stride = float(params.get("stride", 0.55))
    phase = TAU * cycles * clamp(u)
    swing = math.sin(phase)
    counter = math.sin(phase + math.pi)
    forward = 0.06
    return ActionState(
        angles={
            "leg_l": stride * swing,
            "shin_l": -0.9 * stride * max(0.0, swing),
            "leg_r": stride * counter,
            "shin_r": -0.9 * stride * max(0.0, counter),
            "foot_l": 0.25 * max(0.0, -swing),
            "foot_r": 0.25 * max(0.0, -counter),
            # The rig mirrors the left arm, so a positive value swings it back.
            "arm_l": 0.45 * stride * swing,
            "arm_r": -0.45 * stride * counter,
            "forearm_l": -0.35,
            "forearm_r": -0.35,
            "torso": forward,
            "head": -forward * 0.6,
        },
        root_dy=0.012 * math.sin(2.0 * phase) - 0.006,
    )


def _stop(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    settle = 1.0 - smoothstep(u)
    brake = math.sin(math.pi * clamp(u)) * 0.18
    angles = pose_angles("stop", settle)
    angles["leg_l"] = angles.get("leg_l", 0.0) + brake
    angles["leg_r"] = angles.get("leg_r", 0.0) - brake
    return ActionState(angles=angles, root_dy=-0.010 * settle)


def _turn(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    #: Absolute facing the character ends up with (+1 right, -1 left).
    to_facing = int(params.get("to_facing", -1))
    pivot = math.sin(math.pi * clamp(u))
    return ActionState(
        angles={
            "torso": 0.20 * pivot,
            "hips": 0.15 * pivot,
            "arm_l": 0.35 * pivot,
            "arm_r": 0.35 * pivot,
            "forearm_l": -0.45 * pivot,
            "forearm_r": -0.45 * pivot,
        },
        facing=to_facing if u >= 0.5 else 0,
        root_dy=-0.012 * pivot,
    )


def _wave(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    raise_arm = ease(min(1.0, u * 4.0), "smooth")
    lower_arm = 1.0 - ease(max(0.0, (u - 0.85) / 0.15), "smooth")
    lift = raise_arm * lower_arm
    wag = math.sin(TAU * 2.2 * clamp(u)) * lift
    return ActionState(
        angles={
            "arm_r": 2.55 * lift,
            "forearm_r": 0.35 * lift + 0.55 * wag,
            "hand_r": 0.35 * wag,
            "arm_l": 0.10 * lift,
            "torso": 0.04 * lift,
            "head": -0.10 * lift,
        },
        root_dy=0.004 * lift,
        mouth=0.25 * lift,
        expression="joy" if lift > 0.4 else "",
    )


def _pose_action(
    pose_name: str,
    *,
    ramp_in: float = 3.0,
    ramp_out: float = 0.0,
    expression: str = "",
    mouth: float = 0.0,
    root_dy: float = 0.0,
    bounce: float = 0.0,
) -> Callable[[float, dict[str, Any], float], ActionState]:
    """Build an action that settles into a reusable pose and optionally exits."""

    def action(u: float, params: dict[str, Any], base_x: float) -> ActionState:
        intensity = float(params.get("intensity", 1.0))
        u = clamp(u)
        rise = smoothstep(min(1.0, u * ramp_in))
        fall = (
            1.0
            if ramp_out <= 0
            else 1.0 - smoothstep(max(0.0, (u - (1.0 - ramp_out)) / ramp_out))
        )
        weight = rise * fall
        angles = pose_angles(pose_name, intensity * weight)
        bob = math.sin(math.pi * u) * bounce
        return ActionState(
            angles=angles,
            root_dy=root_dy * weight + bob * 0.02,
            mouth=mouth * weight,
            expression=expression if weight > 0.25 else "",
            gaze=float(params.get("gaze", 0.0)) * weight,
        )

    return action


_reach = _pose_action("reach_forward", ramp_in=2.0, ramp_out=0.16)
_offer = _pose_action("offer", ramp_in=2.5, ramp_out=0.18)
_carry = _pose_action("carry", ramp_in=2.5, ramp_out=0.20)
_point = _pose_action("point", ramp_in=3.0, ramp_out=0.15, mouth=0.15)
_joy = _pose_action("joy", ramp_in=2.2, ramp_out=0.22, expression="joy", mouth=0.5, bounce=0.6)
_confused = _pose_action("confused", ramp_in=2.2, ramp_out=0.20, expression="confused")
_annoyed = _pose_action("annoyed", ramp_in=2.2, ramp_out=0.20, expression="annoyed")
_surprise = _pose_action("surprise", ramp_in=2.8, ramp_out=0.25, expression="surprise", mouth=0.85, bounce=1.0)


def _run(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    state = _walk(
        u,
        {
            **params,
            "cycles": float(params.get("cycles", 4.0)),
            "stride": float(params.get("stride", 0.82)),
        },
        base_x,
    )
    state.angles["torso"] = -0.24 + state.angles.get("torso", 0.0) * 0.35
    state.angles["head"] = 0.12 + state.angles.get("head", 0.0) * 0.25
    state.angles["arm_l"] = state.angles.get("arm_l", 0.0) * 1.35
    state.angles["arm_r"] = state.angles.get("arm_r", 0.0) * 1.35
    state.angles["forearm_l"] = -0.78
    state.angles["forearm_r"] = -0.78
    state.root_dy += 0.018
    state.expression = ""
    return state


def _jump(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    u = clamp(u)
    crouch = smoothstep(min(1.0, u / 0.16)) * (
        1.0 - smoothstep(max(0.0, (u - 0.16) / 0.10))
    )
    air = smoothstep(clamp((u - 0.22) / 0.18)) * (
        1.0 - smoothstep(clamp((u - 0.62) / 0.12))
    )
    land = smoothstep(clamp((u - 0.66) / 0.10)) * (
        1.0 - smoothstep(clamp((u - 0.80) / 0.20))
    )
    lift = math.sin(math.pi * clamp((u - 0.18) / 0.52)) if 0.18 <= u <= 0.72 else 0.0
    angles = pose_angles("crouch", crouch + land)
    if air > 0.0:
        extended = {
            "leg_l": -0.20,
            "leg_r": 0.14,
            "shin_l": 0.16,
            "shin_r": -0.10,
            "arm_l": 1.65,
            "arm_r": 1.65,
            "forearm_l": 0.20,
            "forearm_r": 0.20,
            "hand_l": 0.25,
            "hand_r": 0.25,
        }
        for joint, value in extended.items():
            angles[joint] = angles.get(joint, 0.0) * (1.0 - air) + value * air
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dy=-0.13 * crouch + 0.17 * lift - 0.11 * land,
        mouth=0.45 * air,
        expression="surprise" if air > 0.45 else "",
    )


def _land(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    crouch = smoothstep(min(1.0, u / 0.14)) * (
        1.0 - smoothstep(max(0.0, (u - 0.30) / 0.55))
    )
    return ActionState(
        angles=pose_angles("crouch", crouch),
        root_x=base_x,
        root_dy=-0.13 * crouch,
        expression="surprise" if crouch > 0.5 else "",
    )


def _laugh(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 3.2)) * (
        1.0 - smoothstep(max(0.0, (u - 0.82) / 0.18))
    )
    bounce = abs(math.sin(TAU * 3.0 * clamp(u))) * 0.022 * weight
    angles = pose_angles("laugh", float(params.get("intensity", 1.0)) * weight)
    angles["head"] = angles.get("head", 0.0) + 0.06 * math.sin(TAU * 3.0 * clamp(u)) * weight
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dy=-0.008 * weight + bounce,
        mouth=0.85 * weight,
        blink=0.7 if weight > 0.35 else 1.0,
        expression="laugh" if weight > 0.3 else "",
    )


def _cry(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 3.0)) * (
        1.0 - smoothstep(max(0.0, (u - 0.85) / 0.15))
    )
    shake = math.sin(TAU * 2.5 * clamp(u)) * 0.03 * weight
    angles = pose_angles("cry", float(params.get("intensity", 1.0)) * weight)
    angles["torso"] = angles.get("torso", 0.0) + shake
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dy=-0.015 * weight,
        mouth=0.30 * weight,
        expression="cry" if weight > 0.3 else "",
    )


def _stumble(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    u = clamp(u)
    lurch = math.sin(math.pi * min(1.0, u / 0.62)) if u < 0.72 else 0.0
    recover = smoothstep(clamp((u - 0.55) / 0.45))
    angles = pose_angles("stumble", max(0.0, lurch - recover * 0.55))
    angles["head"] = angles.get("head", 0.0) + 0.10 * recover
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dx=0.055 * lurch,
        root_dy=-0.035 * lurch,
        mouth=0.55 * lurch,
        expression="surprise" if lurch > 0.35 else "",
    )


def _dodge(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    side = 1.0 if float(params.get("side", 1.0)) >= 0 else -1.0
    weight = math.sin(math.pi * clamp(u))
    angles = pose_angles("dodge", weight * float(params.get("intensity", 1.0)))
    angles["torso"] = angles.get("torso", 0.0) + 0.16 * side * weight
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dx=0.12 * side * weight,
        root_dy=-0.02 * weight,
        mouth=0.25 * weight,
        expression="surprise" if weight > 0.5 else "",
    )


def _look_around(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = math.sin(math.pi * clamp(u))
    swing = math.sin(TAU * clamp(u))
    return ActionState(
        angles={
            "torso": 0.02 * weight,
            "head": 0.12 * swing * weight,
            "arm_l": 0.10,
            "arm_r": 0.10,
            "forearm_l": -0.10,
            "forearm_r": -0.10,
        },
        root_x=base_x,
        gaze=0.9 * swing * weight,
        expression="confused" if weight > 0.6 else "",
    )


def _clap(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 3.0)) * (
        1.0 - smoothstep(max(0.0, (u - 0.85) / 0.15))
    )
    beat = abs(math.sin(TAU * 3.0 * clamp(u)))
    angles = pose_angles("clap", weight)
    angles["arm_l"] = angles.get("arm_l", 0.0) + 0.18 * beat * weight
    angles["arm_r"] = angles.get("arm_r", 0.0) + 0.18 * beat * weight
    angles["forearm_l"] = angles.get("forearm_l", 0.0) + 0.22 * beat * weight
    angles["forearm_r"] = angles.get("forearm_r", 0.0) + 0.22 * beat * weight
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dy=0.006 * beat * weight,
        mouth=0.25 * weight,
        expression="joy" if weight > 0.4 else "",
    )


def _dance(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 3.0)) * (
        1.0 - smoothstep(max(0.0, (u - 0.85) / 0.15))
    )
    beat = math.sin(TAU * 2.0 * clamp(u))
    angles = pose_angles("dance", weight)
    angles["hips"] = 0.18 * beat * weight
    angles["torso"] = angles.get("torso", 0.0) + 0.10 * beat * weight
    angles["head"] = angles.get("head", 0.0) - 0.10 * beat * weight
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dx=0.035 * beat * weight,
        root_dy=0.012 * abs(beat) * weight,
        mouth=0.3 * weight,
        expression="joy" if weight > 0.35 else "",
    )


def _fight(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 3.0)) * (
        1.0 - smoothstep(max(0.0, (u - 0.85) / 0.15))
    )
    jab = max(0.0, math.sin(TAU * 2.0 * clamp(u))) * weight
    angles = pose_angles("fight", weight)
    angles["arm_r"] = angles.get("arm_r", 0.0) + 0.42 * jab
    angles["forearm_r"] = angles.get("forearm_r", 0.0) - 0.55 * jab
    return ActionState(
        angles=angles,
        root_x=base_x,
        root_dy=-0.02 * weight,
        mouth=0.35 * weight,
        expression="determined" if weight > 0.4 else "",
    )


def _sit(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = smoothstep(min(1.0, u * 2.6)) * (
        1.0 - smoothstep(max(0.0, (u - 0.9) / 0.1))
    )
    return ActionState(
        angles=pose_angles("sit", weight),
        root_x=base_x,
        root_dy=-0.20 * weight,
        expression="",
    )


def _stand(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = 1.0 - smoothstep(min(1.0, u * 2.4))
    return ActionState(angles=pose_angles("sit", weight), root_x=base_x, root_dy=-0.20 * weight)


def _look(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    weight = math.sin(math.pi * clamp(u))
    return ActionState(
        angles={
            "torso": 0.02 * weight,
            "head": 0.14 * math.sin(TAU * clamp(u)),
            "arm_l": 0.12,
            "arm_r": 0.12,
            "forearm_l": -0.15,
            "forearm_r": -0.15,
        },
        root_x=base_x,
        gaze=float(params.get("gaze", 0.0)) * weight,
        expression="confused" if weight > 0.6 else "",
        mouth=0.10 * weight,
    )


_pickup = _pose_action("pickup", ramp_in=2.4, ramp_out=0.24, root_dy=-0.08)
_putdown = _pose_action("pickup", ramp_in=2.4, ramp_out=0.30, root_dy=-0.08)
_shrug = _pose_action("shrug", ramp_in=2.4, ramp_out=0.22, expression="confused")
_crouch = _pose_action("crouch", ramp_in=2.6, ramp_out=0.22, root_dy=-0.10)
_enter = _walk
_exit = _walk


def _react(u: float, params: dict[str, Any], base_x: float) -> ActionState:
    hop = math.sin(math.pi * clamp(u))
    state = _surprise(u, params, base_x)
    state.root_dy += 0.055 * hop
    state.root_x = base_x
    state.mouth = max(state.mouth, 0.9 * (hop ** 0.6))
    return state


ACTIONS: dict[str, Callable[[float, dict[str, Any], float], ActionState]] = {
    "idle": _idle,
    "walk": _walk,
    "run": _run,
    "enter": _enter,
    "exit": _exit,
    "stop": _stop,
    "turn": _turn,
    "wave": _wave,
    "reach": _reach,
    "offer": _offer,
    "carry": _carry,
    "point": _point,
    "look": _look,
    "react": _react,
    "surprise": _surprise,
    "joy": _joy,
    "confused": _confused,
    "annoyed": _annoyed,
    "jump": _jump,
    "land": _land,
    "crouch": _crouch,
    "sit": _sit,
    "stand": _stand,
    "pickup": _pickup,
    "putdown": _putdown,
    "look_around": _look_around,
    "shrug": _shrug,
    "clap": _clap,
    "laugh": _laugh,
    "cry": _cry,
    "stumble": _stumble,
    "dodge": _dodge,
    "dance": _dance,
    "fight": _fight,
}

#: Actions that change the character's horizontal position, in stage units.
MOVING_ACTIONS = frozenset({"walk", "run", "enter", "exit"})


def evaluate(action: str, u: float, params: dict[str, Any], base_x: float) -> ActionState:
    """Evaluate a single action; unknown actions are rejected by the scene loader."""
    fn = ACTIONS.get(action)
    if fn is None:
        raise KeyError(action)
    return fn(clamp(u), params or {}, base_x)


# -------------------------------------------------------------------- overlays


def blink_openness(t: float, times: list[float], duration: float = 0.16) -> float:
    """1.0 = eyes open, dips to 0 during each blink window (deterministic)."""
    for start in times:
        if start <= t < start + duration:
            u = (t - start) / duration
            return max(0.06, abs(math.cos(math.pi * u)))
    return 1.0


def blend_poses(states: list[tuple[ActionState, float]]) -> ActionState:
    """Weighted blend of the angle channels of several actions."""
    total = sum(weight for _, weight in states)
    if total <= 0:
        return ActionState()
    angles: dict[str, float] = {}
    mouth = 0.0
    blink = 1.0
    root_dx = 0.0
    root_dy = 0.0
    gaze = 0.0
    expression = ""
    best_weight = 0.0
    for state, weight in states:
        for name, value in state.angles.items():
            angles[name] = angles.get(name, 0.0) + value * weight
        mouth = max(mouth, state.mouth)
        blink = min(blink, state.blink)
        root_dx += state.root_dx * weight
        root_dy += state.root_dy * weight
        gaze += state.gaze * weight
        if state.expression and weight > best_weight:
            expression = state.expression
            best_weight = weight
    return ActionState(
        angles={name: value / total for name, value in angles.items() if name != "root_dy"},
        root_dx=root_dx / total,
        root_dy=root_dy / total,
        mouth=mouth,
        blink=blink,
        expression=expression,
        gaze=gaze / total,
    )
