"""Visual style presets for generative video (Mission 3 §6).

Each preset is a real backend configuration: art direction, light, color,
materials, environment, camera motion, visual density, and limits. These are
generic style directions — they deliberately do NOT copy any protected
property, brand, or studio aesthetic.
"""

from __future__ import annotations

from .models import StylePreset

DEFAULT_NEGATIVE = (
    "distorted anatomy, extra fingers, warped faces, flicker, shakiness, "
    "watermark, logo, text overlay, low quality, blurry, oversaturated"
)


def build_presets() -> dict[str, StylePreset]:
    presets: dict[str, StylePreset] = {}

    presets["cinematic3d"] = StylePreset(
        id="cinematic3d",
        name="Cinematic 3D Animation",
        art_direction="high-end 3D animated film, soft global illumination",
        lighting="three-point cinematic lighting, gentle rim light",
        color_palette="color-graded, teal-and-amber accents, deep contrast",
        materials="subsurface skin, fabric with fiber detail, glossy eyes",
        environment="depth-of-field background, atmospheric haze",
        camera_motion="smooth slow push-in and gentle tracking",
        visual_density="rich but clean, moderate detail",
        negative_prompt=DEFAULT_NEGATIVE,
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    presets["stylized_film"] = StylePreset(
        id="stylized_film",
        name="Stylized Animated Film",
        art_direction="hand-touched stylized animation, expressive shapes",
        lighting="soft diffused key, warm practical lights",
        color_palette="harmonious limited palette, bold accents",
        materials="cartoon-shaded surfaces, loose linework",
        environment="simple stylized backdrops",
        camera_motion="dynamic but steady, occasional whip-pan",
        visual_density="minimal, readable",
        negative_prompt=DEFAULT_NEGATIVE,
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    presets["premium2d"] = StylePreset(
        id="premium2d",
        name="Premium 2D Animation",
        art_direction="premium 2D animation, painterly backgrounds",
        lighting="clean studio lighting with soft shadows",
        color_palette="elegant, saturated, cohesive",
        materials="smooth vector-like fills, subtle texture",
        environment="rich illustrative backgrounds",
        camera_motion="subtle parallax and gentle pans",
        visual_density="balanced, curated detail",
        negative_prompt=DEFAULT_NEGATIVE,
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    presets["realistic_env"] = StylePreset(
        id="realistic_env",
        name="Realistic Environments",
        art_direction="photorealistic cinematic environments",
        lighting="natural lighting, realistic shadows, ambient occlusion",
        color_palette="natural, faithful color grade",
        materials="physically-based materials, fine texture",
        environment="realistic locations with atmosphere",
        camera_motion="purposeful slow camera moves",
        visual_density="high detail, documentary-feel",
        negative_prompt="CGI look, toy-like, plastic, low quality, flicker",
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    presets["painterly_fantasy"] = StylePreset(
        id="painterly_fantasy",
        name="Painterly Fantasy",
        art_direction="painterly fantasy concept-art style",
        lighting="dramatic moody lighting, magical glow",
        color_palette="rich fantasy palette, iridescent tones",
        materials="brushstroke texture, soft edges",
        environment="fantastical architecture and landscapes",
        camera_motion="epic slow dolly, gentle orbit",
        visual_density="dense, atmospheric",
        negative_prompt=DEFAULT_NEGATIVE,
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    presets["documentary"] = StylePreset(
        id="documentary",
        name="Documentary-Inspired",
        art_direction="naturalistic documentary film look",
        lighting="available light, candid realism",
        color_palette="neutral, muted, honest color",
        materials="real-world texture, slight grain",
        environment="real locations, people at work",
        camera_motion="handheld realism, stable interviews",
        visual_density="informative, grounded",
        negative_prompt="futuristic, fantasy, heavy VFX, oversaturated",
        limits={"max_duration_s": 8, "aspects": ["9:16"], "resolutions": ["720p", "1080p"]},
    )

    return presets


def get_preset(preset_id: str | None) -> StylePreset:
    presets = build_presets()
    key = preset_id or "cinematic3d"
    if key not in presets:
        raise KeyError(
            f"Неизвестный пресет «{key}». Доступны: {', '.join(sorted(presets))}"
        )
    return presets[key]
