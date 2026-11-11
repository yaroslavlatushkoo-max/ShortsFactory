"""Cinematic planning: ScenePlan + preset -> CinematicShot (Mission 3 §3B).

Deterministic, validateable rules so tests and outputs are reproducible. The
choices (wide/medium/close, angle, camera motion, mood) are meaningful and
grounded in the scene's content.
"""

from __future__ import annotations

from .models import CinematicShot, ScenePlan, StylePreset

# emotion -> (angle, camera_motion, mood)
_EMOTION = {
    "joy": ("eye", "gentle push-in", "warm and uplifted"),
    "surprise": ("low", "quick tilt-up", "tense, curious"),
    "confused": ("dutch", "static with subtle drift", "disoriented"),
    "annoyed": ("eye", "static", "impatient"),
    "sad": ("high", "slow pull-back", "somber, introspective"),
    "fear": ("low", "handheld shake", "uneasy"),
    "neutral": ("eye", "static", "calm, neutral"),
}


def _shot_type(scene: ScenePlan) -> str:
    # Actions or wide visual goals -> wide; heavy dialogue -> close; else medium.
    action = (scene.action_summary or "").lower()
    goal = (scene.visual_goal or "").lower()
    if any(word in action for word in ("explore", "landscape", "city", "crowd", "establish", "wide")):
        return "wide"
    if any(word in goal for word in ("wide", "establish", "scale", "environment")):
        return "wide"
    if scene.dialogue and len(scene.dialogue) > 80:
        return "close"
    if any(word in action for word in ("whispers", "stares", "cries", "smiles", "detail")):
        return "close"
    return "medium"


def plan_cinematic(scenes: list[ScenePlan], preset: StylePreset) -> list[CinematicShot]:
    shots: list[CinematicShot] = []
    default_angle, default_motion, default_mood = _EMOTION.get(
        "neutral", ("eye", "static", "calm")
    )
    for scene in scenes:
        emotion = (scene.emotion or "neutral").strip().lower()
        angle, motion, mood = _EMOTION.get(emotion, (default_angle, default_motion, default_mood))
        shot_type = _shot_type(scene)
        # camera motion per shot type
        if shot_type == "wide":
            motion = "slow lateral pan" if motion == "static" else motion
        elif shot_type == "close":
            motion = "subtle handheld drift" if motion == "static" else motion
        # events
        events: list[str] = []
        if scene.dialogue:
            events.append("character speaks")
        if scene.characters:
            events.append(f"{scene.characters[0]} on screen")
        shots.append(
            CinematicShot(
                scene_idx=scene.idx,
                shot_type=shot_type,
                angle=angle,
                camera_position="frontal",
                camera_motion=motion,
                composition="rule-of-thirds, subject slightly right",
                depth="foreground/background separation",
                lighting=preset.lighting,
                palette=preset.color_palette,
                mood=mood,
                character_action=scene.action_summary,
                key_visual_events=events,
                transition="cut",
            )
        )
    return shots
