"""Frame compositor and FFmpeg encoder for Animation Engine v4.

The renderer draws every frame with Pillow, streams raw RGB frames into FFmpeg
and muxes a deterministic procedural audio bed. Layers are painted by explicit
z-order: background/environment, objects, characters, foreground. Camera motion
(pan/zoom) is applied *after* the world is drawn, which makes it possible to
prove that limb animation is independent of the camera.

Objects can be parented to a rig point (``attach``/``transfer``) or released
with a constant velocity (``detach``). While held, an object's transform is
derived from the character's hand every frame, so it cannot drift away from
the body.
"""

from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..logging_setup import get_logger
from ..util import ffmpeg_bin, now_iso, run
from . import rig
from .environments import build_layers
from .looks import look_art, resolve_look
from .motion import MOVING_ACTIONS, blend_poses, blink_openness, ease, evaluate, lerp
from .paths import path_lift, position_at
from .scene import AnimationScene, AttachmentEvent, Character, SceneObject, sample_object
from .voice import VoiceTrack

#: Character height as a fraction of the stage height at ``scale = 1``.
CHARACTER_HEIGHT_RATIO = 0.42

#: Extra canvas around the frame so the camera can pan/zoom without borders.
STAGE_OVERSIZE = 1.25

#: Character ``y = 1.0`` maps to this fraction of the stage height.  The stage
#: is 25% taller than the frame, so anchoring characters at the very bottom
#: would crop their shoes.
FLOOR_RATIO = 0.90
TAU = math.tau

DEFAULT_BACKGROUND: dict[str, Any] = {
    "wall_top": "#d8ecf6",
    "wall_bottom": "#9dc4dd",
    "floor": "#c69a63",
    "floor_line": "#b08450",
    "skirting": "#8a6a45",
    "rug": "#d96b52",
    "rug_border": "#f0d7a8",
    "window": "#a8dcf0",
    "window_frame": "#7a5a3a",
    "frame": "#6b4a2f",
    "accent": "#f2d06b",
    "table": "#7a4a2c",
    "table_top": "#c88f5c",
    "shelf": "#8a6a45",
    "plant": "#4f8f5a",
    "plant_pot": "#c07a52",
}


class RenderError(RuntimeError):
    pass


@dataclass
class CameraState:
    x: float
    y: float
    zoom: float


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (200, 200, 200)


class AnimationRenderer:
    def __init__(
        self,
        scene: AnimationScene,
        config: Any = None,
        *,
        voice: VoiceTrack | None = None,
    ) -> None:
        if scene.width % 2 or scene.height % 2:
            raise RenderError("Ширина и высота должны быть чётными для H.264/yuv420p.")
        self.scene = scene
        self.config = config
        self.voice = voice
        self.logger = get_logger()
        self.sw = round(scene.width * STAGE_OVERSIZE)
        self.sh = round(scene.height * STAGE_OVERSIZE)
        self._looks: dict[str, dict[str, Any]] = {}
        self._layers = []
        self._back_layers = []
        self._front_layers = []
        if scene.environment:
            self._layers = build_layers(scene.environment, self.sw, self.sh, scene.background)
            self._back_layers = [layer for layer in self._layers if layer.depth <= 1.0]
            self._front_layers = [layer for layer in self._layers if layer.depth > 1.0]
            self._background = None
            self._foreground = None
        else:
            self._background = self._build_background()
            self._foreground = self._build_foreground()

    # ------------------------------------------------------------------ geometry
    def character_height_px(self, character: Character) -> float:
        return character.scale * self.sh * CHARACTER_HEIGHT_RATIO

    def look_for(self, character: Character) -> dict[str, Any]:
        """Resolved look asset for a character (cached, deterministic)."""
        if character.id not in self._looks:
            self._looks[character.id] = resolve_look(character)
        return self._looks[character.id]

    def camera_at(self, t: float) -> CameraState:
        keys = self.scene.camera
        if not keys:
            return CameraState(0.5, 0.5, 1.0)
        if t <= keys[0].t:
            return CameraState(keys[0].x, keys[0].y, keys[0].zoom)
        if t >= keys[-1].t:
            return CameraState(keys[-1].x, keys[-1].y, keys[-1].zoom)
        for index in range(len(keys) - 1):
            a, b = keys[index], keys[index + 1]
            if a.t <= t <= b.t:
                span = b.t - a.t or 1.0
                u = ease((t - a.t) / span, "smooth")
                return CameraState(
                    a.x + (b.x - a.x) * u,
                    a.y + (b.y - a.y) * u,
                    a.zoom + (b.zoom - a.zoom) * u,
                )
        last = keys[-1]
        return CameraState(last.x, last.y, last.zoom)

    def _crop_box(self, camera: CameraState) -> tuple[int, int, int, int]:
        zoom = max(1.0, camera.zoom)
        crop_w = min(float(self.sw), self.sw / zoom)
        crop_h = min(float(self.sh), self.sh / zoom)
        # Stage margin keeps the lens away from the stage border so pans have room.
        # It shrinks to half of the available slack (and never below zero) so the
        # crop always stays inside the stage -- otherwise the frame would show a
        # black band on the clamped side.
        MARGIN = 0.04
        max_left = self.sw - crop_w
        max_top = self.sh - crop_h
        inset_x = min(MARGIN * self.sw, max_left / 2)
        inset_y = min(MARGIN * self.sh, max_top / 2)
        left = camera.x * self.sw - crop_w / 2
        top = camera.y * self.sh - crop_h / 2
        left = min(max(inset_x, left), max_left - inset_x)
        top = min(max(inset_y, top), max_top - inset_y)
        return (round(left), round(top), round(left + crop_w), round(top + crop_h))

    def _layer_box(self, camera: CameraState, depth: float) -> tuple[int, int, int, int]:
        """Camera crop for a parallax layer: far layers move less, near more."""
        depth = max(0.05, depth)
        zoom = max(1.0, 1.0 + (max(1.0, camera.zoom) - 1.0) * depth)
        crop_w = self.sw / zoom
        crop_h = self.sh / zoom
        cx = (0.5 + (camera.x - 0.5) * depth) * self.sw
        cy = (0.5 + (camera.y - 0.5) * depth) * self.sh
        left = min(max(0.0, cx - crop_w / 2), self.sw - crop_w)
        top = min(max(0.0, cy - crop_h / 2), self.sh - crop_h)
        return (round(left), round(top), round(left + crop_w), round(top + crop_h))

    # ------------------------------------------------------------------- poses
    def _position_chain(self, character: Character) -> list[tuple[Any, float, float]]:
        """Resolve each action's start/end horizontal position (chained)."""
        chain_x = character.x
        result: list[tuple[Any, float, float]] = []
        for item in self.scene.actions_for(character.id):
            from_x = chain_x
            to_x = float(item.params.get("to_x", chain_x)) if item.action in MOVING_ACTIONS else chain_x
            result.append((item, from_x, to_x))
            chain_x = to_x
        return result

    def character_pose(
        self, character: Character, t: float
    ) -> tuple[dict[str, float], float, float, int, dict[str, float]]:
        """Return (angles, x, root_dy, facing, face) for a character at time ``t``."""
        chain = self._position_chain(character)
        blend = max(0.0, self.scene.blend)
        # Position left by the last action that already started. Without this a
        # character would snap back to its initial x as soon as it goes idle.
        base_x = character.x
        for item, from_x, to_x in chain:
            if item.start <= t + 1e-9:
                base_x = to_x if item.action in MOVING_ACTIONS else from_x
        active: list[tuple[Any, float, float]] = []
        for item, from_x, to_x in chain:
            if item.start - blend <= t <= item.start + item.duration + blend:
                active.append((item, from_x, to_x))

        if not active:
            return {}, base_x, 0.0, character.facing, {
                "blink": 1.0, "mouth": 0.0, "expression": "neutral", "gaze": 0.0,
            }

        weighted: list[tuple[Any, float]] = []
        for item, from_x, to_x in active:
            u = (t - item.start) / item.duration
            weight = 1.0
            if u < 0:
                weight = max(0.0, (u + blend / item.duration) / max(1e-6, blend / item.duration))
            elif u > 1:
                weight = max(0.0, (1.0 + blend / item.duration - u) / max(1e-6, blend / item.duration))
            weighted.append((evaluate(item.action, u, item.params, from_x), weight))

        blended = blend_poses(weighted)

        # Horizontal position and vertical hip offset come from the latest started action.
        latest = max(active, key=lambda entry: entry[0].start)
        item, from_x, to_x = latest
        u = min(1.0, max(0.0, (t - item.start) / item.duration))
        pos_state = evaluate(item.action, u, item.params, from_x)
        path_dy = 0.0
        path_point = position_at(item.params.get("path"), u)
        if path_point is not None:
            x = path_point["x"]
            path_dy = path_lift(path_point["y"])
        elif item.action in MOVING_ACTIONS and from_x != to_x:
            x = lerp(from_x, to_x, ease(u))
        else:
            x = from_x if pos_state.root_x is None else pos_state.root_x
        x += blended.root_dx
        root_dy = blended.root_dy + path_dy

        facing = character.facing
        for turn_item, _fx, _tx in chain:
            if turn_item.action == "turn" and turn_item.start + 0.5 * turn_item.duration <= t:
                facing = int(turn_item.params.get("to_facing", -facing))

        blink = blink_openness(t, self.scene.blink_times) * min(
            [state.blink for state, _ in weighted] or [1.0]
        )
        mouth = 0.0
        for state, _ in weighted:
            mouth = max(mouth, state.mouth)
        if self.voice is not None:
            voice_mouth, speaking = self.voice.mouth_at(character.id, t)
            if speaking:
                mouth = max(mouth, voice_mouth)
        face = {
            "blink": blink,
            "mouth": mouth,
            "expression": blended.expression or "neutral",
            "gaze": blended.gaze,
        }
        return blended.angles, x, root_dy, facing, face

    def character_parts(
        self, character: Character, t: float
    ) -> tuple[dict[str, Any], dict[str, float], float, int]:
        """Resolve the rig at ``t``; shared by drawing and motion evidence."""
        angles, x, root_dy, facing, face = self.character_pose(character, t)
        height_px = self.character_height_px(character)
        ground_y = character.y * self.sh * FLOOR_RATIO
        hip_px = (
            x * self.sw,
            ground_y - (rig.HIP_HEIGHT + rig.GROUND_CLEARANCE) * height_px + root_dy * height_px,
        )
        return rig.resolve(angles, facing, height_px, hip_px), face, x, facing

    def _draw_character(self, draw: Any, character: Character, t: float) -> None:
        if t < character.appear_at:
            return
        parts, face, _x, _facing = self.character_parts(character, t)
        rig.draw_character(draw, parts, self.look_for(character), self.character_height_px(character), face)

    def _paste_character(self, stage: Image.Image, character: Character, t: float, *, mask: bool = False) -> None:
        """Draw a character: v3 SVG sprites when available, legacy shapes otherwise."""
        if t < character.appear_at:
            return
        look = self.look_for(character)
        height_px = self.character_height_px(character)
        angles, x, root_dy, facing, face = self.character_pose(character, t)
        ground_y = character.y * self.sh * FLOOR_RATIO
        hip_px = (
            x * self.sw,
            ground_y - (rig.HIP_HEIGHT + rig.GROUND_CLEARANCE) * height_px + root_dy * height_px,
        )
        art = look_art(look)
        if art is None:
            draw = ImageDraw.Draw(stage)
            parts = rig.resolve(angles, facing, height_px, hip_px)
            rig.draw_character(draw, parts, look, height_px, face)
            return

        # soft contact shadow keeps the character grounded
        shadow_w = height_px * 0.46
        shadow_h = height_px * 0.062
        if not mask:
            overlay = Image.new("RGBA", (self.sw, self.sh), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(overlay)
            shadow_draw.ellipse(
                (x * self.sw - shadow_w / 2, ground_y - shadow_h * 0.55,
                 x * self.sw + shadow_w / 2, ground_y + shadow_h * 0.55),
                fill=(24, 28, 38, 64),
            )
            stage.paste(overlay.convert("RGB"), (0, 0), overlay.split()[3])

        image, origin = rig.compose_character(art, look, angles, height_px, face, facing)
        stage.paste(
            image,
            (int(round(hip_px[0] - origin[0])), int(round(hip_px[1] - origin[1]))),
            image,
        )

    # -------------------------------------------------------------- attachments
    def _hold_at(self, scene_object: SceneObject, t: float) -> tuple[AttachmentEvent, Character] | None:
        """The attachment event that owns the object at ``t`` (or ``None``)."""
        holder: tuple[AttachmentEvent, Character] | None = None
        for event in self.scene.events_for(scene_object.id):
            if event.t > t + 1e-9:
                break
            if event.kind == "attach":
                character = self.scene.character(event.character)
                if character is not None:
                    holder = (event, character)
            elif event.kind == "transfer":
                character = self.scene.character(event.target)
                if character is not None:
                    holder = (event, character)
            else:
                holder = None
        return holder

    def _last_detach(self, scene_object: SceneObject, t: float) -> AttachmentEvent | None:
        result: AttachmentEvent | None = None
        for event in self.scene.events_for(scene_object.id):
            if event.t > t + 1e-9:
                break
            if event.kind == "detach":
                result = event
        return result

    def _hold_transform(self, scene_object: SceneObject, event: AttachmentEvent, character: Character, t: float) -> dict[str, float]:
        parts, _face, _x, facing = self.character_parts(character, t)
        look = self.look_for(character)
        head_scale = float((look.get("proportions") or {}).get("head", 1.0))
        height_px = self.character_height_px(character)
        try:
            tip, angle = rig.attachment_transform(parts, event.point, height_px, head_scale)
        except KeyError:
            # Validation rejects unknown points; keep the object at its last pose.
            return sample_object(scene_object, t)

        dx, dy = event.offset
        base = (facing * dx * height_px, -dy * height_px)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        ox = base[0] * cos_a - base[1] * sin_a
        oy = base[0] * sin_a + base[1] * cos_a
        x = (tip[0] + ox) / self.sw
        y = (tip[1] + oy) / self.sh
        base_scale = event.scale if event.scale > 0 else sample_object(scene_object, event.t)["scale"]
        return {
            "x": x,
            "y": y,
            "scale": base_scale,
            "rotation": math.degrees(angle) + event.rotation,
            "visible": sample_object(scene_object, t).get("visible", 1.0),
        }

    def object_transform(self, scene_object: SceneObject, t: float) -> dict[str, float]:
        """Object transform at ``t``: keyframes, hand attachment or detached drift.

        The result is fully deterministic: no physics solver is involved, a
        released object simply continues with the velocity written in the
        ``detach`` event.
        """
        hold = self._hold_at(scene_object, t)
        if hold is not None:
            event, character = hold
            return self._hold_transform(scene_object, event, character, t)

        detach = self._last_detach(scene_object, t)
        if detach is not None:
            source = self._hold_at(scene_object, detach.t - 1e-6)
            if source is not None:
                event, character = source
                base = self._hold_transform(scene_object, event, character, detach.t)
            else:  # pragma: no cover - validation prevents this
                base = sample_object(scene_object, detach.t)
            dt = max(0.0, t - detach.t)
            keyed = sample_object(scene_object, t)
            return {
                "x": base["x"] + detach.velocity[0] * dt,
                "y": base["y"] + detach.velocity[1] * dt,
                "scale": base["scale"] * float(keyed.get("scale", 1.0)),
                "rotation": base["rotation"] + detach.spin * dt,
                "visible": base.get("visible", 1.0),
            }
        return sample_object(scene_object, t)

    def holder_at(self, scene_object: SceneObject, t: float) -> str:
        hold = self._hold_at(scene_object, t)
        return hold[1].id if hold else ""

    # ------------------------------------------------------------------ objects
    def _draw_object(self, stage: Image.Image, scene_object: SceneObject, t: float) -> None:
        state = self.object_transform(scene_object, t)
        if state.get("visible", 0.0) < 0.5:
            return
        rotation = float(state.get("rotation", 0.0))
        if abs(rotation) >= 0.5:
            overlay = Image.new("RGBA", (self.sw, self.sh), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            self._draw_object_shape(overlay_draw, scene_object, state)
            overlay = overlay.rotate(
                rotation, resample=Image.BICUBIC,
                center=(state["x"] * self.sw, state["y"] * self.sh),
            )
            alpha = overlay.split()[3]
            stage.paste(overlay.convert("RGB"), (0, 0), alpha)
            return
        draw = ImageDraw.Draw(stage)
        self._draw_object_shape(draw, scene_object, state)

    def _draw_object_shape(self, draw: Any, scene_object: SceneObject, state: dict[str, float]) -> None:
        cx = state["x"] * self.sw
        cy = state["y"] * self.sh
        size = 0.024 * self.sh * state["scale"]
        color = _hex_to_rgb(scene_object.color)
        kind = scene_object.kind

        if kind == "magnet":
            glow = tuple(min(255, int(channel * 0.55 + 255 * 0.45)) for channel in color)
            for sign in (-1, 1):
                draw.ellipse((cx - size * 1.0, cy - size * 1.45, cx + sign * size * 1.0, cy - size * 0.75),
                             fill=color)
                draw.line((cx - size * 0.85, cy - size * 0.60, cx + size * 0.85, cy - size * 0.60),
                          fill=(200, 220, 240), width=max(2, int(size)))
            draw.ellipse((cx - size * 0.25, cy - size * 0.9, cx + size * 0.25, cy - size * 0.3), fill=glow)

        elif kind == "snake":
            body_len = size * 2.6
            dx = body_len / 3
            dy = -size * 0.25
            prev = (cx, cy)
            segments = [(p) for p in [(cx, cy)]]
            points = []
            for i in range(6):
                px = cx + dx * i
                py = cy + dy * i + math.sin(TAU * i / 4.0) * size * 0.30
                tail = body_len * (1 - i / 7.0)
                pts = [(prev[0], prev[1]), (px - dx * tail * 0.18 * (-1 if i % 2 else 1), py)]
                segments.append(pts)
                prev = (px, py)
                points.append((px, py))
            for seg in segments:
                if isinstance(seg, tuple):
                    nex, nyp = seg[0], seg[1]
                else:
                    nex, nyp = seg
                draw.line((prev[0], prev[1], nex, nyp), fill=color, width=max(2, int(size * 0.3)))
                prev = (nex, nyp)
            r = max(2.0, size * 0.18)
            for off in (-0.40, 0.40):
                ex = points[-1][0] + dx * 0.30 * (-1 if off < 0 else 1)
                ey = points[-1][1] + dy * 0.30 * (-1 if off < 0 else 1)
                draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=color)
                draw.ellipse((ex - r * 0.4, ey - r * 0.4, ex + r * 0.0, ey + r * 0.0), fill="#ffffff")

        elif kind == "feather":
            spine = (cx, cy - size * 1.8)
            blades = [(cx, cy - size * 1.2), (cx - size * 0.5, cy - size * 0.9),
                      (cx + size * 0.5, cy - size * 0.9)]
            head = (cx, cy - size * 1.95)
            draw.line((head[0], head[1], spine[0], spine[1]), fill=color, width=max(2, int(size * 0.12)))
            for bx, by in blades:
                pts = [(head[0], head[1]), (bx - size * 0.4, by), (bx + size * 0.4, by)]
                draw.polygon(pts, fill=color, outline=color)
                draw.line((head[0], head[1], bx, by), fill=color, width=max(1, int(size * 0.06)))

        elif kind == "clock":
            draw.rectangle((cx - size * 0.95, cy - size * 1.2, cx + size * 0.95, cy + size * 1.2),
                            fill=color, outline="#2a2a2a", width=max(2, int(size * 0.06)))
            draw.ellipse((cx - size * 0.28, cy - size * 0.28, cx + size * 0.28, cy + size * 0.28), fill="#f7f0e4")
            rot = state["rotation"] if abs(state["rotation"]) > 1e-6 else 0.0
            h = 60 * (9 + min(4, int(abs(rot) * 360 / 3.14159)) if abs(rot) > 0 else 9)
            m = 60 * (12 + min(4, int(abs(rot) * 360 / 3.14159)) if abs(rot) > 0 else 0)
            draw.line((cx, cy, cx + size * 0.08 * math.cos(math.radians(h)),
                      cy + size * 0.08 * math.sin(math.radians(h))),
                      fill="#2a2a2a", width=max(1, int(size * 0.04)))
            draw.line((cx, cy, cx + size * 0.10 * math.cos(math.radians(m)),
                      cy + size * 0.10 * math.sin(math.radians(m))),
                      fill="#2a2a2a", width=max(1, int(size * 0.05)))

        elif kind == "orb":
            glow = tuple(min(255, int(channel * 0.55 + 255 * 0.45)) for channel in color)
            draw.ellipse((cx - size * 1.2, cy - size * 1.2, cx + size * 1.2, cy + size * 1.2), fill=glow)
            draw.ellipse((cx - size, cy - size, cx + size, cy + size), fill=color, outline="#5a3b00", width=4)
            draw.ellipse((cx - size * 0.45, cy - size * 0.55, cx - size * 0.05, cy - size * 0.15), fill="#ffffff")
        elif kind == "crate":
            draw.rectangle((cx - size, cy - size, cx + size, cy + size), fill=color, outline="#4a3320", width=4)
            draw.line((cx - size, cy - size, cx + size, cy + size), fill="#4a3320", width=3)
        elif kind == "book":
            draw.rectangle((cx - size * 1.3, cy - size * 0.8, cx + size * 1.3, cy + size * 0.8), fill=color, outline="#3a2a1a", width=4)
            draw.line((cx - size * 0.1, cy - size * 0.8, cx - size * 0.1, cy + size * 0.8), fill="#f6efdc", width=3)
        elif kind == "gem":
            draw.polygon(
                [(cx, cy - size * 1.2), (cx + size, cy - size * 0.2), (cx, cy + size * 1.2),
                 (cx - size, cy - size * 0.2)],
                fill=color, outline="#2c3a4a", width=4,
            )
            draw.line((cx - size * 0.5, cy - size * 0.2, cx, cy - size * 0.9), fill="#ffffff", width=3)
        elif kind == "cup":
            draw.rectangle((cx - size * 0.7, cy - size, cx + size * 0.7, cy + size), fill=color, outline="#4a3320", width=4)
            draw.arc((cx + size * 0.4, cy - size * 0.5, cx + size * 1.2, cy + size * 0.5), start=270, end=90, fill="#4a3320", width=4)
        elif kind == "spring":
            turns = 4
            for index in range(turns):
                y = cy - size * 1.2 + index * (size * 0.55)
                draw.arc(
                    (cx - size * 0.8, y, cx + size * 0.8, y + size * 0.55),
                    start=180, end=360, fill=color, width=max(3, int(size * 0.22)),
                )
            draw.ellipse((cx - size * 0.35, cy - size * 1.55, cx + size * 0.35, cy - size * 0.95), fill=color, outline="#3a2a1a", width=3)
            draw.ellipse((cx - size * 0.16, cy - size * 1.45, cx + size * 0.02, cy - size * 1.27), fill="#ffffff")
        elif kind == "confetti":
            palette = ("#ffd257", "#e8713c", "#7bc4a4", "#4f8fb0", "#f7f0e4", "#d96b52")
            radius = size * 2.2 * max(0.2, state["scale"])
            for index in range(14):
                angle = TAU * index / 14.0 + 0.35
                distance = radius * (0.45 + 0.55 * ((index * 7) % 11) / 10.0)
                px = cx + math.cos(angle) * distance
                py = cy + math.sin(angle) * distance * 0.85
                dot = max(2.0, size * 0.09)
                draw.rectangle((px - dot, py - dot * 0.6, px + dot, py + dot * 0.6), fill=palette[index % len(palette)])
        else:  # lamp
            draw.rectangle((cx - size * 0.2, cy - size, cx + size * 0.2, cy + size), fill="#6b5a45")
            draw.polygon(
                [(cx - size, cy - size), (cx + size, cy - size),
                 (cx + size * 0.6, cy - size * 1.7), (cx - size * 0.6, cy - size * 1.7)],
                fill=color, outline="#3a2a1a",
            )

    # --------------------------------------------------------------- background
    def _build_background(self) -> Image.Image:
        spec = dict(DEFAULT_BACKGROUND)
        spec.update(self.scene.background or {})
        top = np.array(_hex_to_rgb(spec["wall_top"]), dtype=np.float32)
        bottom = np.array(_hex_to_rgb(spec["wall_bottom"]), dtype=np.float32)
        wall_h = int(self.sh * 0.72)
        ramp = np.linspace(0.0, 1.0, wall_h, dtype=np.float32)[:, None, None]
        wall = (top[None, None, :] * (1.0 - ramp) + bottom[None, None, :] * ramp)
        wall = np.repeat(wall, self.sw, axis=1).astype(np.uint8)
        image = Image.fromarray(wall, "RGB").resize((self.sw, self.sh), Image.BILINEAR)

        draw = ImageDraw.Draw(image)
        floor_y = wall_h
        draw.rectangle((0, floor_y, self.sw, self.sh), fill=spec["floor"])
        # floor boards
        board_h = max(6, int(self.sh * 0.026))
        for index, y in enumerate(range(floor_y + board_h, self.sh, board_h)):
            draw.line((0, y, self.sw, y), fill=spec["floor_line"], width=max(1, int(self.sh * 0.0016)))
            seam = (self.sw * 0.18) * (1 if index % 2 == 0 else -1)
            draw.line((self.sw * 0.5 + seam, y - board_h, self.sw * 0.5 + seam, y),
                      fill=spec["floor_line"], width=max(1, int(self.sh * 0.0012)))
        draw.rectangle((0, floor_y - int(self.sh * 0.012), self.sw, floor_y), fill=spec["skirting"])

        # rug with a border
        rug_w, rug_h = self.sw * 0.66, self.sh * 0.17
        rcx, rcy = self.sw * 0.46, self.sh * 0.89
        draw.ellipse((rcx - rug_w / 2, rcy - rug_h / 2, rcx + rug_w / 2, rcy + rug_h / 2),
                     fill=spec["rug_border"])
        draw.ellipse((rcx - rug_w / 2 + 14, rcy - rug_h / 2 + 10, rcx + rug_w / 2 - 14, rcy + rug_h / 2 - 10),
                     fill=spec["rug"])

        # window with sky, sill and curtains
        wl, wt = self.sw * 0.055, self.sh * 0.11
        wr, wb = self.sw * 0.35, self.sh * 0.40
        frame_pad = max(2.0, self.sw * 0.012)
        bar_w = max(1, int(self.sw * 0.010))
        draw.rectangle((wl - frame_pad, wt - frame_pad, wr + frame_pad, wb + frame_pad),
                       fill=spec["window_frame"])
        draw.rectangle((wl, wt, wr, wb), fill=spec["window"])
        # sky band + distant hills
        draw.rectangle((wl, wb - (wb - wt) * 0.35, wr, wb), fill="#cfe9c2")
        draw.polygon(
            [(wl, wb - 4), (wl + (wr - wl) * 0.25, wb - (wb - wt) * 0.24),
             (wl + (wr - wl) * 0.55, wb - 4)],
            fill="#9ccb8f",
        )
        draw.polygon(
            [(wl + (wr - wl) * 0.42, wb - 4), (wl + (wr - wl) * 0.72, wb - (wb - wt) * 0.30),
             (wr, wb - 4)],
            fill="#8bbd7f",
        )
        draw.ellipse((wr - (wr - wl) * 0.32, wt + (wb - wt) * 0.10,
                      wr - (wr - wl) * 0.18, wt + (wb - wt) * 0.24), fill="#fff3b0")
        draw.line((wl, (wt + wb) / 2, wr, (wt + wb) / 2), fill=spec["window_frame"], width=bar_w)
        draw.line(((wl + wr) / 2, wt, (wl + wr) / 2, wb), fill=spec["window_frame"], width=bar_w)
        draw.rectangle((wl - frame_pad * 2, wb + frame_pad, wr + frame_pad * 2, wb + frame_pad * 3),
                       fill=spec["window_frame"])
        for side in (-1, 1):
            curtain_x = (wl if side < 0 else wr)
            draw.polygon(
                [(curtain_x, wt - frame_pad), (curtain_x + side * self.sw * 0.045, wt - frame_pad),
                 (curtain_x + side * self.sw * 0.030, wb), (curtain_x, wb)],
                fill=spec["accent"],
            )

        # framed picture on the wall
        pl, pt, pr, pb = self.sw * 0.72, self.sh * 0.12, self.sw * 0.94, self.sh * 0.29
        inset_x = (pr - pl) * 0.08
        inset_y = (pb - pt) * 0.10
        draw.rectangle((pl, pt, pr, pb), fill=spec["frame"])
        draw.rectangle((pl + inset_x, pt + inset_y, pr - inset_x, pb - inset_y), fill="#bfe4f2")
        draw.ellipse((pl + inset_x * 2, pt + inset_y * 2, pr - inset_x * 2, pb - inset_y * 2),
                     fill=spec["accent"])
        draw.polygon(
            [(pl + inset_x * 1.4, pb - inset_y * 1.4),
             (pl + (pr - pl) * 0.45, pt + (pb - pt) * 0.45),
             (pr - inset_x * 1.4, pb - inset_y * 1.4)],
            fill="#8bbd7f",
        )

        # shelf with books and a plant
        shelf_l, shelf_r = self.sw * 0.055, self.sw * 0.30
        shelf_y = self.sh * 0.60
        draw.rectangle((shelf_l, shelf_y, shelf_r, shelf_y + self.sh * 0.012), fill=spec["shelf"])
        book_colors = ("#d96b52", "#4f8fb0", "#e8c14a", "#7bc4a4")
        bx = shelf_l + self.sw * 0.012
        for index, color in enumerate(book_colors):
            bw = self.sw * 0.018
            bh = self.sh * 0.045 + (index % 2) * self.sh * 0.008
            draw.rectangle((bx, shelf_y - bh, bx + bw, shelf_y), fill=color, outline="#3a2a1a", width=2)
            bx += bw + self.sw * 0.005
        pot_x = shelf_r - self.sw * 0.035
        draw.rectangle((pot_x - self.sw * 0.014, shelf_y - self.sh * 0.028,
                        pot_x + self.sw * 0.014, shelf_y), fill=spec["plant_pot"],
                       outline="#3a2a1a", width=2)
        for dx, dy, rr in ((-0.020, -0.052, 0.020), (0.0, -0.062, 0.024), (0.020, -0.048, 0.018)):
            draw.ellipse((pot_x + self.sw * (dx - rr), shelf_y + self.sh * (dy - rr),
                          pot_x + self.sw * (dx + rr), shelf_y + self.sh * (dy + rr)),
                         fill=spec["plant"])

        # table
        tw, th = self.sw * 0.32, self.sh * 0.030
        tcx = float(spec.get("table_x", 0.52)) * self.sw
        tcy = float(spec.get("table_y", 0.735)) * self.sh
        draw.ellipse((tcx - tw * 0.55, tcy + th + self.sh * 0.085,
                      tcx + tw * 0.55, tcy + th + self.sh * 0.115), fill="#8a6a45")
        for sign in (-1, 1):
            leg_x = tcx + sign * (tw / 2 - self.sw * 0.02)
            draw.rectangle((leg_x - 12, tcy + th, leg_x + 12, tcy + th + self.sh * 0.08), fill=spec["table"])
        draw.rectangle((tcx - tw / 2, tcy, tcx + tw / 2, tcy + th), fill=spec["table_top"])
        draw.rectangle((tcx - tw / 2, tcy + th, tcx + tw / 2, tcy + th + self.sh * 0.014),
                       fill=spec["table"])
        draw.line((tcx - tw / 2, tcy, tcx + tw / 2, tcy), fill="#f0c894", width=max(2, int(self.sh * 0.0025)))
        return image

    def _build_foreground(self) -> Image.Image:
        image = Image.new("RGBA", (self.sw, self.sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        band = self.sh * 0.045
        draw.rectangle((0, self.sh - band, self.sw, self.sh), fill=(18, 24, 34, 210))
        return image

    # ------------------------------------------------------------------- frames
    def frame(self, t: float, *, camera: bool = True) -> Image.Image:
        camera_state = self.camera_at(t)
        if self._layers:
            frame = Image.new("RGBA", (self.scene.width, self.scene.height), (226, 205, 180, 255))
            for layer in self._back_layers:
                box = self._layer_box(camera_state, layer.depth) if camera else (0, 0, self.sw, self.sh)
                frame.alpha_composite(
                    layer.image.crop(box).resize((self.scene.width, self.scene.height), Image.BILINEAR)
                )
            stage = Image.new("RGBA", (self.sw, self.sh), (0, 0, 0, 0))
        else:
            stage = self._background.copy()
            frame = None

        drawables: list[tuple[float, str, Any]] = []
        for scene_object in self.scene.objects:
            holder = self.holder_at(scene_object, t)
            z = float(scene_object.z)
            if holder:
                character = self.scene.character(holder)
                if character is not None:
                    z = max(z, character.z + 2)
            drawables.append((z, "object", scene_object))
        for character in self.scene.characters:
            drawables.append((float(character.z), "character", character))
        drawables.sort(key=lambda entry: (entry[0], 0 if entry[1] == "object" else 1))

        for _z, kind, item in drawables:
            if kind == "object":
                self._draw_object(stage, item, t)
            else:
                self._paste_character(stage, item, t)

        if self._layers:
            box = self._crop_box(camera_state) if camera else (0, 0, self.sw, self.sh)
            overlay = stage.crop(box).resize((self.scene.width, self.scene.height), Image.BILINEAR)
            frame.alpha_composite(overlay)
            for layer in self._front_layers:
                box = self._layer_box(camera_state, layer.depth) if camera else (0, 0, self.sw, self.sh)
                frame.alpha_composite(
                    layer.image.crop(box).resize((self.scene.width, self.scene.height), Image.BILINEAR)
                )
            return frame.convert("RGB")

        stage.paste(self._foreground, (0, 0), self._foreground)
        if camera:
            box = self._crop_box(camera_state)
            return stage.crop(box).resize((self.scene.width, self.scene.height), Image.BILINEAR)
        return stage.resize((self.scene.width, self.scene.height), Image.BILINEAR)

    def frames(self) -> Iterator[Image.Image]:
        count = self.scene.frame_count()
        for index in range(count):
            yield self.frame(index / self.scene.fps)

    # ------------------------------------------------------------------- encode
    def procedural_audio(self, path: Path, voice: VoiceTrack | None = None) -> Path:
        """Deterministic audio bed, optionally mixed with the synthesized voice track."""
        sample_rate = 48000
        duration = self.scene.duration
        samples = int(sample_rate * duration)
        timeline = np.arange(samples, dtype=np.float64) / sample_rate
        signal = np.zeros(samples, dtype=np.float64)
        # gentle ambient drone
        signal += 0.05 * np.sin(2 * np.pi * 110.0 * timeline)
        # one soft chime per object appear time (keeps audio aligned with the timeline)
        appear_times = sorted({item.appear_at for item in self.scene.objects})
        for appear in appear_times:
            start = int(appear * sample_rate)
            length = min(samples - start, int(1.2 * sample_rate))
            if length <= 0:
                continue
            local = np.arange(length, dtype=np.float64) / sample_rate
            envelope = np.exp(-3.5 * local)
            signal[start:start + length] += 0.22 * envelope * np.sin(2 * np.pi * 660.0 * local)
        if voice is not None and voice.samples.size:
            voice_samples = voice.samples[:samples]
            if voice_samples.size < samples:
                voice_samples = np.pad(voice_samples, (0, samples - voice_samples.size))
            signal = 0.55 * signal + float(self.scene.voice_gain) * voice_samples
        signal = np.clip(signal, -0.95, 0.95)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes((signal * 32767).astype("<i2").tobytes())
        return path

    def encode(self, output: Path, *, audio: Path | None = None) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.scene.width}x{self.scene.height}",
            "-r", str(self.scene.fps), "-i", "-",
        ]
        if audio is not None:
            command += ["-i", str(audio)]
        command += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(self.scene.fps),
        ]
        if audio is not None:
            command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(output)]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.config.root) if self.config else None,
        )
        try:
            assert process.stdin is not None
            for image in self.frames():
                process.stdin.write(image.tobytes())
            process.stdin.close()
            stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            code = process.wait(timeout=900)
        except (BrokenPipeError, OSError) as exc:
            raise RenderError(f"FFmpeg не принял кадры анимации: {exc}") from exc
        if code != 0 or not output.exists():
            raise RenderError(f"FFmpeg не собрал анимацию: {stderr[-1500:]}")
        return {
            "video": output.as_posix(),
            "frames": self.scene.frame_count(),
            "fps": self.scene.fps,
            "duration": round(self.scene.duration, 3),
            "audio": audio.as_posix() if audio else "",
            "generated_at": now_iso(),
        }


# ------------------------------------------------------------------ contact sheet


def build_contact_sheet(
    video: Path,
    out_path: Path,
    duration: float,
    font_dir: Path | None = None,
    columns: int = 3,
    rows: int = 3,
    times: list[float] | None = None,
) -> Path:
    """Extract frames across the video and tile them with time labels.

    ``times`` lets a scene point at the moments worth reviewing (contacts,
    reactions); otherwise frames are spread evenly over the whole clip.
    """
    if times:
        positions = [min(max(float(item), 0.0), max(0.0, duration - 0.05))
                     for item in times][: columns * rows]
        slots = len(positions)
    else:
        slots = columns * rows
        positions = [
            min(max(duration * (index + 0.5) / slots, 0.0), max(0.0, duration - 0.05))
            for index in range(slots)
        ]
    frame_dir = out_path.parent / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[Path, float]] = []
    for index, position in enumerate(positions):
        path = frame_dir / f"frame_{index + 1:02d}.jpg"
        result = run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{position:.3f}",
             "-i", str(video), "-frames:v", "1", "-q:v", "2", str(path)],
            timeout=120,
        )
        if result.returncode == 0 and path.exists():
            frames.append((path, position))
    if not frames:
        raise RenderError("Не удалось извлечь кадры для контактного листа.")

    thumb_w, thumb_h = 300, 533
    margin, gap, header = 24, 16, 96
    sheet = Image.new(
        "RGB",
        (thumb_w * columns + margin * 2 + gap * (columns - 1),
         header + thumb_h * rows + gap * (rows - 1) + margin),
        (10, 15, 24),
    )
    draw = ImageDraw.Draw(sheet)
    title_font = small_font = None
    if font_dir:
        try:
            title_font = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 34)
            small_font = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 20)
        except OSError:
            title_font = small_font = None
    title_font = title_font or ImageFont.load_default()
    small_font = small_font or ImageFont.load_default()
    draw.text((margin, 26), "ANIMATION ENGINE v4 · КОНТАКТНЫЙ ЛИСТ", font=title_font, fill=(255, 255, 255))
    draw.text((margin, 66), f"{video.name} · {duration:.2f} с · покадровая проверка поз и действий",
              font=small_font, fill=(176, 194, 214))
    for index, (path, position) in enumerate(frames):
        with Image.open(path) as source:
            image = source.convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)
        x = margin + (index % columns) * (thumb_w + gap)
        y = header + (index // columns) * (thumb_h + gap)
        sheet.paste(image, (x, y))
        draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline=(70, 90, 112), width=2)
        draw.rectangle((x, y + thumb_h - 34, x + thumb_w, y + thumb_h), fill=(10, 15, 24))
        draw.text((x + 10, y + thumb_h - 28), f"t = {position:4.1f} с", font=small_font, fill=(255, 220, 100))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path
