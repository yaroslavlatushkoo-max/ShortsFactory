"""Reference & continuity layer (Mission 3 §3C): character/environment/style
bibles + consistent prompt blocks + scene continuity manifest.

The character descriptions are deterministic (derived from a stable per-name
seed), so the same character reads identically across scenes. This supports
consistency but does NOT guarantee identity — that is stated explicitly in the
LIMITATIONS doc. Real identity control needs backend-provided reference images
or character conditioning.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import (
    CharacterBible,
    CinematicShot,
    EnvironmentBible,
    ScenePlan,
    StylePreset,
    VisualStyleBible,
)

_HAIR = ["dark brown", "black", "blonde", "auburn", "grey", "golden"]
_HAIRSTYLE = ["short", "shoulder-length", "curly", "straight", "tied back", "bob"]
_EYES = ["brown", "blue", "hazel", "green", "grey"]
_SKIN = ["fair", "light tan", "tan", "deep", "medium"]
_BUILD = ["slim", "average", "athletic", "stocky"]
_CLOTHING = [
    "casual jacket and jeans",
    "button shirt and trousers",
    "sweater and chinos",
    "hoodie and joggers",
    "formal blazer",
    "long coat and scarf",
]


def _seed(name: str, salt: str) -> int:
    digest = hashlib.sha256(f"{name}::{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def build_character_bibles(scenes: list[ScenePlan]) -> dict[str, CharacterBible]:
    result: dict[str, CharacterBible] = {}
    for scene in scenes:
        for name in scene.characters:
            if not name or name in result:
                continue
            seed = _seed(name, "char")
            result[name] = CharacterBible(
                id=name.lower().replace(" ", "_"),
                name=name,
                age=str(20 + seed % 45),
                appearance={
                    "hair_color": _HAIR[seed % len(_HAIR)],
                    "hair_style": _HAIRSTYLE[(seed >> 4) % len(_HAIRSTYLE)],
                    "eye_color": _EYES[(seed >> 8) % len(_EYES)],
                    "skin": _SKIN[(seed >> 12) % len(_SKIN)],
                    "build": _BUILD[(seed >> 16) % len(_BUILD)],
                },
                clothing={"outfit": _CLOTHING[(seed >> 20) % len(_CLOTHING)]},
                features=[],
                voice_hint="clear, natural",
                reference="",
            )
    return result


def build_environment_bibles(scenes: list[ScenePlan]) -> dict[str, EnvironmentBible]:
    result: dict[str, EnvironmentBible] = {}
    for scene in scenes:
        loc = scene.location or "general"
        if loc in result:
            continue
        seed = _seed(loc, "env")
        result[loc] = EnvironmentBible(
            id=loc.lower().replace(" ", "_"),
            name=loc,
            description=f"A clear, readable {loc} setting",
            palette=["cohesive", "complementary"],
            lighting="soft, natural",
            props=[],
        )
    return result


def build_style_bible(preset: StylePreset) -> VisualStyleBible:
    return VisualStyleBible(
        preset_id=preset.id,
        art_direction=preset.art_direction,
        lighting=preset.lighting,
        color_palette=preset.color_palette,
        materials=preset.materials,
        environment=preset.environment,
        camera_motion=preset.camera_motion,
        visual_density=preset.visual_density,
        negative_prompt=preset.negative_prompt,
    )


def build_prompt(
    scene: ScenePlan,
    shot: CinematicShot,
    style: VisualStyleBible,
    chars: dict[str, CharacterBible],
    envs: dict[str, EnvironmentBible],
) -> str:
    """A single, consistent positive prompt for a scene clip."""
    style_block = (
        f"{style.art_direction}; {style.lighting}; palette: {style.color_palette}; "
        f"{style.materials}; {style.environment}"
    )
    char_block = ""
    for name in scene.characters:
        ch = chars.get(name)
        if not ch:
            continue
        app = ch.appearance
        char_block += (
            f"{name}: {app.get('skin')} skin, {app.get('hair_color')} "
            f"{app.get('hair_style')} hair, {app.get('eye_color')} eyes, "
            f"{app.get('build')} build, wearing {ch.clothing.get('outfit')}. "
        )
    shot_block = (
        f"{shot.shot_type} shot, {shot.angle} angle, {shot.camera_position} camera, "
        f"camera {shot.camera_motion}, {shot.composition}, {shot.depth}, "
        f"mood: {shot.mood}"
    )
    env_name = envs.get(scene.location or "general", EnvironmentBible(id="general", name="general"))
    action = scene.action_summary or "the scene plays out"
    prompt = (
        f"{style_block}. {char_block.strip()} Setting: {env_name.name}. "
        f"Action: {action}. {shot_block}. "
        f"Format: vertical 9:16."
    )
    return prompt


def build_manifest(
    scenes: list[ScenePlan],
    chars: dict[str, CharacterBible],
    envs: dict[str, EnvironmentBible],
    style: VisualStyleBible,
    prompts: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "characters": {name: bible.to_dict() for name, bible in chars.items()},
        "environments": {name: bible.to_dict() for name, bible in envs.items()},
        "style": style.to_dict(),
        "scenes": [
            {"idx": s.idx, "characters": s.characters, "location": s.location}
            for s in scenes
        ],
        "continuity": {
            "character_reference_reuse": "same deterministic bible per character across scenes",
            "identity_guarantee": "NOT guaranteed without reference images/conditioning",
        },
        "prompts_per_scene": prompts,
    }
