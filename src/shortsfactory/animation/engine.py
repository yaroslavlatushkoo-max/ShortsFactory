"""Animation Engine v4 orchestration: scene -> frames -> MP4 -> evidence."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..logging_setup import get_logger
from ..util import atomic_write_json, atomic_write_text, ffmpeg_bin, ffprobe_json, now_iso, run
from . import rig
from .looks import look_art, resolve_look
from .motion import MOVING_ACTIONS, UNSUPPORTED
from .renderer import STAGE_OVERSIZE, AnimationRenderer, RenderError, build_contact_sheet
from .scene import AnimationScene, load_scene
from .voice import VoiceTrack, build_voice_track

#: Limb travel (output pixels) that counts as real animated movement.
LIMB_TRAVEL_THRESHOLD = 8.0

#: Root travel (output pixels) that counts as the character walking across the stage.
ROOT_TRAVEL_THRESHOLD = 30.0


@dataclass
class AnimationResult:
    scene_id: str
    video: str
    duration: float
    fps: int
    width: int
    height: int
    frames: int
    size_bytes: int
    audio: str = ""
    contact_sheet: str = ""
    report: str = ""
    report_json: str = ""
    characters: str = ""
    scene_copy: str = ""
    scene_source: str = ""
    decode_ok: bool = False
    codecs: dict[str, str] = field(default_factory=dict)
    frame_hashes: list[str] = field(default_factory=list)
    motion_checks: dict[str, Any] = field(default_factory=dict)
    attachment_checks: dict[str, Any] = field(default_factory=dict)
    lip_sync: dict[str, Any] = field(default_factory=dict)
    voice_track: str = ""
    implemented_movements: list[str] = field(default_factory=list)
    unsupported_movements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["generated_at"] = self.generated_at or now_iso()
        return data


class AnimationEngine:
    """Renders an :class:`AnimationScene` and produces verifiable evidence."""

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self.logger = get_logger()

    # ---------------------------------------------------------------- evidence
    def _frame_diff(self, scene: AnimationScene, t_a: float, t_b: float, *, camera: bool) -> float:
        """Mean absolute pixel difference between two frames; camera may be frozen."""
        renderer = AnimationRenderer(scene, self.config)
        a = np.asarray(renderer.frame(t_a, camera=camera), dtype=np.int16)
        b = np.asarray(renderer.frame(t_b, camera=camera), dtype=np.int16)
        return float(np.abs(a - b).mean())

    def verify_motion(self, scene: AnimationScene) -> dict[str, Any]:
        """Prove that the picture changes even when the camera does not move.

        Two independent measurements are used: raw pixel difference between
        frames rendered with a frozen camera, and the actual travel of rig joints
        (hands, feet, head) in stage pixels. The second one cannot be caused by
        camera movement at all, because it is computed before the camera crop.
        """
        duration = scene.duration
        points = [duration * ratio for ratio in (0.12, 0.32, 0.52, 0.72, 0.90)]
        frozen: list[float] = []
        for first, second in zip(points, points[1:]):
            frozen.append(round(self._frame_diff(scene, first, second, camera=False), 3))
        moving = round(self._frame_diff(scene, points[0], points[-1], camera=True), 3)
        pixel_ok = all(value > 1.0 for value in frozen)

        renderer = AnimationRenderer(scene, self.config)
        times = [duration * ratio for ratio in (0.05, 0.20, 0.35, 0.50, 0.65, 0.80, 0.95)]
        characters: list[dict[str, Any]] = []
        for character in scene.characters:
            tracks: dict[str, list[tuple[float, float]]] = {
                name: [] for name in ("hand_l", "hand_r", "foot_l", "foot_r", "head")
            }
            roots: list[float] = []
            for t in times:
                parts, _face, x, _facing = renderer.character_parts(character, t)
                roots.append(x * renderer.sw)
                for name in tracks:
                    tracks[name].append(parts[name].tip)
            travel = {
                name: round(_span(positions) / STAGE_OVERSIZE, 2) for name, positions in tracks.items()
            }
            limb_max = max(travel.values())
            characters.append(
                {
                    "id": character.id,
                    "root_travel_px": round((max(roots) - min(roots)) / STAGE_OVERSIZE, 2),
                    "limb_travel_px": travel,
                    "max_limb_travel_px": limb_max,
                    "moving_limbs": sorted(
                        name for name, value in travel.items() if value >= LIMB_TRAVEL_THRESHOLD
                    ),
                }
            )

        objects_moved: dict[str, float] = {}
        for scene_object in scene.objects:
            positions = []
            for t in times:
                state = renderer.object_transform(scene_object, t)
                positions.append((state["x"] * renderer.sw, state["y"] * renderer.sh))
            objects_moved[scene_object.id] = round(_span(positions) / STAGE_OVERSIZE, 2)

        limbs_ok = pixel_ok and any(
            item["max_limb_travel_px"] >= LIMB_TRAVEL_THRESHOLD for item in characters
        )
        walking = any(item["root_travel_px"] >= ROOT_TRAVEL_THRESHOLD for item in characters)
        if limbs_ok and walking:
            note = (
                "Кадры различаются при неподвижной камере, суставы рук/ног/головы смещаются "
                "на десятки пикселей, а минимум один персонаж проходит по сцене. Это позы и "
                "перемещение персонажей, а не движение камеры."
            )
        elif limbs_ok:
            note = "Суставы двигаются при неподвижной камере, но никто не проходит по сцене."
        else:
            note = "Движение частей тела не подтверждено: различия кадров слишком малы."
        return {
            "frozen_camera_diffs": frozen,
            "camera_on_diff": moving,
            "frozen_camera_threshold": 1.0,
            "limb_travel_threshold_px": LIMB_TRAVEL_THRESHOLD,
            "root_travel_threshold_px": ROOT_TRAVEL_THRESHOLD,
            "limbs_move_without_camera": limbs_ok,
            "character_walks": walking,
            "characters": characters,
            "objects_moved_px": objects_moved,
            "note": note,
        }

    def verify_attachment(self, scene: AnimationScene) -> dict[str, Any]:
        """Prove that held objects really follow the hand and never teleport.

        For every hold interval the object centre is compared with the rig
        attachment point at several moments: the distance must stay constant
        (otherwise the object would slide relative to the hand). Transfers and
        releases are checked for a visible jump.
        """
        if not scene.events:
            return {"enabled": False, "note": "В сцене нет attachment-событий."}
        renderer = AnimationRenderer(scene, self.config)
        objects: list[dict[str, Any]] = []
        all_ok = True
        for scene_object in scene.objects:
            events = scene.events_for(scene_object.id)
            if not events:
                continue
            intervals: list[tuple[Any, str, float, float]] = []
            current: tuple[Any, str, float] | None = None
            for event in events:
                if event.kind == "attach":
                    current = (event, event.character, event.t)
                elif event.kind == "transfer":
                    if current is not None:
                        intervals.append((current[0], current[1], current[2], event.t))
                    current = (event, event.target, event.t)
                else:
                    if current is not None:
                        intervals.append((current[0], current[1], current[2], event.t))
                    current = None
            if current is not None:
                intervals.append((current[0], current[1], current[2], scene.duration))

            holds: list[dict[str, Any]] = []
            object_ok = True
            for event, holder_id, start, end in intervals:
                character = scene.character(holder_id)
                if character is None:  # pragma: no cover - validation prevents this
                    continue
                distances: list[float] = []
                span = max(1e-6, end - start)
                for index in range(6):
                    moment = start + span * (index + 0.5) / 6
                    parts, _face, _x, _facing = renderer.character_parts(character, moment)
                    look = renderer.look_for(character)
                    head_scale = float((look.get("proportions") or {}).get("head", 1.0))
                    tip, _angle = rig.attachment_transform(
                        parts, event.point, renderer.character_height_px(character), head_scale
                    )
                    state = renderer.object_transform(scene_object, moment)
                    distances.append(
                        math.hypot(state["x"] * renderer.sw - tip[0], state["y"] * renderer.sh - tip[1])
                        / STAGE_OVERSIZE
                    )
                spread = max(distances) - min(distances)
                expected = (
                    math.hypot(event.offset[0], event.offset[1])
                    * renderer.character_height_px(character)
                    / STAGE_OVERSIZE
                )
                deviation = abs(distances[0] - expected)
                follows = spread <= 3.0 and deviation <= 3.0
                object_ok = object_ok and follows
                holds.append(
                    {
                        "character": holder_id,
                        "point": event.point,
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "distance_px": round(sum(distances) / len(distances), 2),
                        "distance_spread_px": round(spread, 2),
                        "offset_expected_px": round(expected, 2),
                        "offset_deviation_px": round(deviation, 2),
                        "follows_hand": follows,
                    }
                )

            transfer_jump = None
            for event in events:
                if event.kind != "transfer":
                    continue
                before = renderer.object_transform(scene_object, event.t - 1e-4)
                after = renderer.object_transform(scene_object, event.t + 1e-4)
                transfer_jump = round(
                    math.hypot((after["x"] - before["x"]) * renderer.sw,
                               (after["y"] - before["y"]) * renderer.sh) / STAGE_OVERSIZE,
                    2,
                )
                object_ok = object_ok and transfer_jump <= 160.0

            detach_jump = None
            for event in events:
                if event.kind != "detach":
                    continue
                before = renderer.object_transform(scene_object, event.t - 1.0 / scene.fps)
                after = renderer.object_transform(scene_object, event.t + 1.0 / scene.fps)
                detach_jump = round(
                    math.hypot((after["x"] - before["x"]) * renderer.sw,
                               (after["y"] - before["y"]) * renderer.sh) / STAGE_OVERSIZE,
                    2,
                )
                object_ok = object_ok and detach_jump <= 40.0

            all_ok = all_ok and object_ok and bool(holds)
            objects.append(
                {
                    "id": scene_object.id,
                    "holds": holds,
                    "transfers": sum(1 for event in events if event.kind == "transfer"),
                    "transfer_jump_px": transfer_jump,
                    "detach_jump_px": detach_jump,
                    "verified": object_ok and bool(holds),
                }
            )
        return {
            "enabled": True,
            "verified": all_ok and bool(objects),
            "objects": objects,
            "note": (
                "Объект удерживается рукой: расстояние до точки рига постоянно, "
                "передача и отпускание проверены на скачок."
                if all_ok and objects
                else "Привязка объекта проверена не для всех событий."
            ),
        }

    def verify_lip_sync(self, scene: AnimationScene, voice: VoiceTrack | None) -> dict[str, Any]:
        """Check that the mouth is driven by the synthesized speech envelope."""
        if voice is None or not voice.lines:
            return {"enabled": False, "note": "В сцене нет озвученных реплик."}
        lines: list[dict[str, Any]] = []
        verified = True
        for line in voice.lines:
            start = line.start + min(0.04, line.duration * 0.1)
            stop = line.start + max(line.duration - 0.04, start + 0.01)
            samples = [voice.mouth_at(line.character, t)[0] for t in np.linspace(start, stop, 40)]
            after = voice.mouth_at(line.character, line.start + line.duration + 0.05)[0]
            opened = max(samples) if samples else 0.0
            varied = (max(samples) - min(samples)) if samples else 0.0
            ok = bool(samples) and opened > 0.2 and varied > 0.05 and after < 0.2
            verified = verified and ok
            lines.append(
                {
                    "character": line.character,
                    "text": line.text,
                    "start": round(line.start, 3),
                    "duration": round(line.duration, 3),
                    "mouth_max": round(opened, 3),
                    "mouth_variation": round(varied, 3),
                    "mouth_after_line": round(after, 3),
                    "follows_audio": ok,
                }
            )
        return {
            "enabled": True,
            "verified": verified,
            "lines": lines,
            "note": (
                "Раскрытие рта считается по огибающей громкости реального WAV от Piper; "
                "после конца реплики рот закрывается."
                if verified
                else "Связь рта с озвучкой проверена не для всех реплик."
            ),
        }

    def determinism_check(self, scene: AnimationScene, times: list[float] | None = None) -> list[dict[str, Any]]:
        """Render the same timestamps twice and compare raw pixels."""
        renderer_a = AnimationRenderer(scene, self.config)
        renderer_b = AnimationRenderer(scene, self.config)
        checks = []
        for t in (times or [scene.duration * 0.25, scene.duration * 0.5, scene.duration * 0.75]):
            bytes_a = renderer_a.frame(t).tobytes()
            bytes_b = renderer_b.frame(t).tobytes()
            checks.append({"t": round(t, 3), "identical": bytes_a == bytes_b})
        return checks

    def verify_framing(self, scene: AnimationScene) -> dict[str, Any]:
        """Check that heads, hands and feet stay inside the 9:16 frame.

        Sampling is dense (each frame of the whole timeline), and hands get a
        small tolerance that matches their drawn radius, so a hand that only
        grazes the crop edge is not reported as a defect.
        """
        renderer = AnimationRenderer(scene, self.config)
        issues: list[str] = []
        samples = 0
        frames = max(8, scene.frame_count())
        hand_margin_px = max(12, round(renderer.sh * 0.02))
        for index in range(frames):
            t = scene.duration * index / max(1, frames - 1)
            camera = renderer.camera_at(t)
            box = renderer._crop_box(camera)
            scale_x = scene.width / max(1, box[2] - box[0])
            scale_y = scene.height / max(1, box[3] - box[1])
            for character in scene.characters:
                if t < character.appear_at:
                    continue
                parts, _face, _x, _facing = renderer.character_parts(character, t)
                samples += 1
                for key in ("head", "hand_l", "hand_r", "foot_l", "foot_r"):
                    part = parts.get(key)
                    if part is None:
                        continue
                    px, py = part.tip
                    sx = (px - box[0]) * scale_x
                    sy = (py - box[1]) * scale_y
                    margin = hand_margin_px if key.startswith("hand") else hand_margin_px * 0.5
                    if sx < -margin or sx > scene.width + margin or sy < -margin or sy > scene.height + margin:
                        issues.append(
                            f"{character.id}: {key} вне кадра в {t:.2f} с "
                            f"({sx:.0f}, {sy:.0f})"
                        )
        return {"ok": not issues, "samples": samples, "issues": issues}

    def verify_pose_quality(self, scene: AnimationScene) -> dict[str, Any]:
        """Flag suspicious limb crossings (arms folded across the torso).

        A forearm is reported only when the hand clearly reaches *past* the
        torso midline to the opposite side while staying inside the torso band
        — this is the defect that made Milo's arm clip through his chest in v3.
        Neutral hanging arms, raised arms (joy/surprise) and crossed-at-the-hip
        postures are deliberately not reported.
        """
        renderer = AnimationRenderer(scene, self.config)
        issues: list[str] = []
        samples = 0
        frames = max(16, scene.frame_count())
        for index in range(frames):
            t = scene.duration * index / max(1, frames - 1)
            for character in scene.characters:
                if t < character.appear_at:
                    continue
                parts, _face, _x, _facing = renderer.character_parts(character, t)
                samples += 1
                # Swinging arms during locomotion are natural and are not the
                # defect we look for; skip posture-cross checks while moving.
                if any(
                    item.action in MOVING_ACTIONS and item.start <= t <= item.start + item.duration
                    for item in scene.actions_for(character.id)
                ):
                    continue
                torso0 = parts["torso"].joint
                torso1 = parts["torso"].tip
                height_px = renderer.character_height_px(character)
                # Vertical band of the torso core plus a little slack at the neck.
                top = min(torso0[1], torso1[1]) - height_px * 0.04
                bottom = max(torso0[1], torso1[1]) + height_px * 0.05
                core_half = height_px * 0.07
                for side in ("l", "r"):
                    forearm = parts[f"forearm_{side}"]
                    hand = parts[f"hand_{side}"]
                    elbow = forearm.joint
                    wrist = hand.joint
                    # A folded-across gesture: the elbow stays clearly on its own
                    # side while the wrist reaches past the torso midline and
                    # stays inside the torso band. Swinging limbs (walk/run) keep
                    # both ends on one side, so they are not reported.
                    x_mid = torso0[0]
                    elbow_outside = abs(elbow[0] - x_mid) > core_half
                    crossed = (wrist[0] - x_mid) * (elbow[0] - x_mid) < 0
                    inside_band = abs(wrist[0] - x_mid) < core_half and bottom >= wrist[1] >= top
                    if elbow_outside and crossed and inside_band:
                        issues.append(
                            f"{character.id}: кисть {side} перекрещена через корпус "
                            f"в {t:.2f} с"
                        )
        return {"enabled": True, "samples": samples, "issues": issues}

    def verify_silhouette_intersections(
        self,
        scene: AnimationScene,
        renderer: AnimationRenderer | None = None,
        diagnostics_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Measure silhouette overlap on real alpha masks, frame by frame.

        Character masks are rendered from the same SVG rig used for the MP4;
        object masks use the same object drawing path.  Character-character
        overlap is a defect, except for small hand/arm contact around an
        interaction event (reported as a warning with its timestamp).  An object
        overlapping a non-holder is a defect; contact with its holder is
        expected and not reported.
        """
        renderer = renderer or AnimationRenderer(scene, self.config)
        diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir else None
        if diagnostics_dir is not None:
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
        stage_area = max(1, renderer.sw * renderer.sh)
        char_threshold = max(80, int(stage_area * 0.00006))
        body_fail_threshold = max(9000, int(stage_area * 0.004))
        object_threshold = max(50, int(stage_area * 0.00004))
        events = sorted(
            event.t for event in scene.events
        ) or [scene.duration * 0.5]
        contact_windows = [
            (item.start, item.start + item.duration)
            for item in scene.actions
            if item.action in {
                "take", "give", "offer", "reach", "drop", "carry", "pickup", "putdown"
            }
        ]

        def in_contact_window(time: float) -> bool:
            if min(abs(time - event) for event in events) <= 0.35:
                return True
            return any(
                start - 0.35 <= time <= end + 0.35
                for start, end in contact_windows
            )
        step = max(1, scene.fps // 6)
        frame_indices = list(range(0, scene.frame_count(), step))
        issues: list[dict[str, Any]] = []
        samples = 0
        saved = 0
        for index in frame_indices:
            t = index / max(1, scene.fps)
            char_masks: dict[str, Any] = {}
            for character in scene.characters:
                if t < character.appear_at:
                    continue
                blank = Image.new("RGBA", (renderer.sw, renderer.sh), (0, 0, 0, 0))
                renderer._paste_character(blank, character, t, mask=True)
                alpha = np.asarray(blank)[:, :, 3]
                char_masks[character.id] = alpha > 16
            samples += len(char_masks)
            ids = list(char_masks)
            for left_index, left_id in enumerate(ids):
                for right_id in ids[left_index + 1:]:
                    overlap = int(np.count_nonzero(char_masks[left_id] & char_masks[right_id]))
                    if overlap <= char_threshold:
                        continue
                    near_contact = in_contact_window(t)
                    if overlap >= body_fail_threshold:
                        severity = "WARNING" if near_contact else "FAIL"
                    else:
                        severity = "WARNING"
                    issues.append(
                        {
                            "kind": "character_character",
                            "characters": [left_id, right_id],
                            "timestamp": round(t, 3),
                            "area_px": overlap,
                            "threshold_px": char_threshold,
                            "context": "interaction_contact" if near_contact else "body_overlap",
                            "severity": severity,
                        }
                    )
                    if diagnostics_dir is not None and saved < 20:
                        path = diagnostics_dir / f"silhouette_{saved:03d}_{t:.2f}s.png"
                        renderer.frame(t).convert("RGB").save(path)
                        issues[-1]["diagnostic"] = str(path)
                        saved += 1
            for scene_object in scene.objects:
                blank = Image.new("RGBA", (renderer.sw, renderer.sh), (0, 0, 0, 0))
                renderer._draw_object(blank, scene_object, t)
                object_alpha = np.asarray(blank)[:, :, 3] > 16
                if not object_alpha.any():
                    continue
                holder = renderer.holder_at(scene_object, t)
                char_by_id = {character.id: character for character in scene.characters}
                for character_id, mask in char_masks.items():
                    if character_id == holder:
                        continue
                    character = char_by_id[character_id]
                    if scene_object.z < character.z:
                        # The object is behind this character and is therefore
                        # occluded, not visibly intersecting the silhouette.
                        continue
                    overlap = int(np.count_nonzero(mask & object_alpha))
                    if overlap <= object_threshold:
                        continue
                    near_contact = in_contact_window(t)
                    severity = "WARNING" if near_contact else "FAIL"
                    issues.append(
                        {
                            "kind": "object_non_holder",
                            "characters": [character_id],
                            "object": scene_object.id,
                            "timestamp": round(t, 3),
                            "area_px": overlap,
                            "threshold_px": object_threshold,
                            "context": "transfer_window" if near_contact else "object_through_character",
                            "severity": severity,
                        }
                    )
                    if diagnostics_dir is not None and saved < 20:
                        path = diagnostics_dir / f"silhouette_{saved:03d}_{t:.2f}s.png"
                        renderer.frame(t).convert("RGB").save(path)
                        issues[-1]["diagnostic"] = str(path)
                        saved += 1
        failures = [issue for issue in issues if issue["severity"] == "FAIL"]
        warnings = [issue for issue in issues if issue["severity"] == "WARNING"]
        status = "FAIL" if failures else ("WARNING" if warnings else "PASS")
        return {
            "enabled": True,
            "ok": status == "PASS",
            "status": status,
            "samples": samples,
            "checked_frames": len(frame_indices),
            "issues": issues,
            "failure_count": len(failures),
            "warning_count": len(warnings),
            "diagnostics": str(diagnostics_dir) if diagnostics_dir else "",
            "note": (
                "Проверка по альфа-маскам реальных SVG-кадров. Контакт руки/предмета "
                "в окне события — WARNING; устойчивое пересечение тел/предмета — FAIL."
            ),
        }

    def asset_report(self, scene: AnimationScene) -> dict[str, Any]:
        """Describe the external vector assets used by every character."""
        entries = []
        for character in scene.characters:
            look = resolve_look(character)
            art = look_art(look)
            if art is None:
                entries.append(
                    {
                        "id": character.id,
                        "look": character.look,
                        "asset_system": "legacy_primitives",
                        "warning": "внешние SVG-ассеты не подключены",
                    }
                )
                continue
            entries.append(
                {
                    "id": character.id,
                    "look": character.look,
                    "source": art.source,
                    "style": art.style,
                    "parts": sorted(art.parts),
                    "face": sorted(art.face),
                    "expressions": sorted(art.expressions),
                    "layers": list(art.layers),
                    "angles": list(art.angles),
                    "rig_limits": dict(art.rig_limits),
                }
            )
        return {"characters": entries}

    def camera_shots(self, scene: AnimationScene) -> list[dict[str, Any]]:
        """Label the authored camera keys as wide / medium / close shots."""
        shots = []
        for key in scene.camera:
            if key.zoom >= 1.22:
                kind = "close"
            elif key.zoom >= 1.08:
                kind = "medium"
            else:
                kind = "wide"
            shots.append({"t": key.t, "x": key.x, "y": key.y, "zoom": key.zoom, "shot": kind})
        return shots

    # ------------------------------------------------------------------- render
    def render(self, scene_path: str | Path, out_dir: str | Path) -> AnimationResult:
        scene = load_scene(scene_path)
        output_dir = Path(out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if scene.voice_lines and self.config is None:
            raise RenderError("Для озвученных реплик нужен Config с моделями Piper.")
        voice = build_voice_track(scene, self.config, output_dir / "audio")
        renderer = AnimationRenderer(scene, self.config, voice=voice)

        audio = renderer.procedural_audio(output_dir / "audio" / "pilot_audio.wav", voice)
        video = output_dir / "pilot.mp4"
        self.logger.info("Рендер сцены %s: %d кадров при %d fps", scene.id, scene.frame_count(), scene.fps)
        info = renderer.encode(video, audio=audio)

        probe = ffprobe_json(video)
        decode = run([ffmpeg_bin(), "-v", "error", "-i", str(video), "-f", "null", "-"],
                     timeout=600)
        decode_ok = decode.returncode == 0 and not decode.stderr.strip()
        codecs = {}
        for stream in probe.get("streams", []):
            codecs[str(stream.get("codec_type"))] = str(stream.get("codec_name"))

        motion = self.verify_motion(scene)
        attachment = self.verify_attachment(scene)
        lip_sync = self.verify_lip_sync(scene, voice)
        determinism = self.determinism_check(scene)
        framing = self.verify_framing(scene)
        pose_quality = self.verify_pose_quality(scene)
        silhouette = self.verify_silhouette_intersections(
            scene, renderer, output_dir / "diagnostics"
        )
        assets = self.asset_report(scene)
        shots = self.camera_shots(scene)
        frame_hashes = []
        for t in (0.0, scene.duration * 0.4, scene.duration * 0.8):
            image = renderer.frame(t, camera=False)
            frame_hashes.append(_hash_image(image))

        contact = build_contact_sheet(
            video,
            output_dir / "contact_sheet.jpg",
            scene.duration,
            font_dir=self.config.fonts_dir if self.config else None,
            times=scene.contact_times,
        )

        scene_copy = output_dir / "pilot_scene.json"
        atomic_write_text(scene_copy, json.dumps(scene.to_dict(), ensure_ascii=False, indent=2) + "\n")
        characters_path = output_dir / "characters.json"
        atomic_write_json(characters_path, self._characters_document(scene))

        result = AnimationResult(
            scene_id=scene.id,
            video=video.as_posix(),
            duration=round(scene.duration, 3),
            fps=scene.fps,
            width=scene.width,
            height=scene.height,
            frames=int(info.get("frames", scene.frame_count())),
            size_bytes=video.stat().st_size,
            audio=audio.as_posix(),
            contact_sheet=contact.as_posix(),
            characters=characters_path.as_posix(),
            scene_copy=scene_copy.as_posix(),
            scene_source=Path(scene_path).as_posix(),
            decode_ok=decode_ok,
            codecs=codecs,
            frame_hashes=frame_hashes,
            motion_checks=motion,
            attachment_checks=attachment,
            lip_sync=lip_sync,
            voice_track=voice.path.as_posix() if voice else "",
            implemented_movements=sorted({item.action for item in scene.actions}),
            unsupported_movements=[f"{name}: {reason}" for name, reason in UNSUPPORTED.items()],
            warnings=list(voice.warnings) if voice else [],
        )
        if framing["issues"]:
            result.warnings.extend(framing["issues"])
        if pose_quality["issues"]:
            result.warnings.extend(pose_quality["issues"])
        if silhouette["issues"]:
            result.warnings.extend(
                f"{issue['kind']} at {issue['timestamp']}s: {issue['area_px']}px"
                for issue in silhouette["issues"]
            )
        result.report_json = (output_dir / "report.json").as_posix()
        result.report = (output_dir / "report.md").as_posix()
        result.generated_at = now_iso()

        report_doc = {
            "engine": "animation-engine-v4",
            "generated_at": result.generated_at,
            "environment": self._environment(),
            "ffprobe": probe,
            "verified": {
                "decode_full": decode_ok,
                "decode_output": decode.stderr.strip()[:500],
                "determinism": determinism,
                "motion": motion,
                "attachment": attachment,
                "lip_sync": lip_sync,
                "framing": framing,
                "pose_quality": pose_quality,
                "silhouette_intersections": silhouette,
            },
            "assets": assets,
            "camera_shots": shots,
            "scene_environment": {
                "name": scene.environment,
                "layers": [layer.id for layer in renderer._layers],
            },
            "voice": voice.to_dict() if voice else None,
            "result": result.to_dict(),
        }
        atomic_write_json(output_dir / "report.json", report_doc)
        atomic_write_text(output_dir / "report.md", self._report_markdown(
            scene, result, probe, determinism,
            {"framing": framing, "poses": pose_quality, "assets": assets, "shots": shots, "silhouette": silhouette},
        ))
        return result

    # ------------------------------------------------------------------- helpers
    def _characters_document(self, scene: AnimationScene) -> dict[str, Any]:
        document = {
            "rig": rig.describe_rig(),
            "characters": [],
        }
        for character in scene.characters:
            look = resolve_look(character)
            document["characters"].append(
                {
                    "id": character.id,
                    "name": character.name or character.id,
                    "appearance": {
                        "look": look.get("id"),
                        "asset": look.get("source", ""),
                        "palette": look["palette"],
                        "proportions": look.get("proportions", {}),
                        "hair": look.get("hair", {}),
                        "face": look.get("face", {}),
                        "outfit": look.get("outfit", {}),
                        "accessories": look.get("accessories", []),
                        "scale": character.scale,
                        "facing": character.facing,
                    },
                    "placement": {"x": character.x, "y": character.y, "z": character.z,
                                  "appear_at": character.appear_at},
                }
            )
        return document

    @staticmethod
    def _environment() -> dict[str, Any]:
        versions = {"python": sys.version.split()[0]}
        for name in ("pillow", "numpy"):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:  # pragma: no cover
                versions[name] = "unknown"
        try:
            proc = run([ffmpeg_bin(), "-version"], timeout=30)
            versions["ffmpeg"] = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        except Exception:  # pragma: no cover - defensive
            versions["ffmpeg"] = "unknown"
        return versions

    def _report_markdown(
        self,
        scene: AnimationScene,
        result: AnimationResult,
        probe: dict[str, Any],
        determinism: list[dict[str, Any]],
        visual_qc: dict[str, Any] | None = None,
    ) -> str:
        stream_lines = []
        for stream in probe.get("streams", []):
            size = (
                f"{stream.get('width')}x{stream.get('height')}"
                if stream.get("width")
                else f"{stream.get('sample_rate', '-')} Hz, {stream.get('channels', '-')} ch"
            )
            stream_lines.append(
                f"- {stream.get('codec_type')}: `{stream.get('codec_name')}` "
                f"({size}, {stream.get('r_frame_rate', '-')} fps)"
            )
        lines = [
            f"# Animation Engine v4 - технический отчёт",
            "",
            f"- Сцена: `{scene.id}` — {scene.title or 'без названия'}",
            f"- Версия схемы: {scene.version}",
            f"- Итоговое видео: `{result.video}`",
            f"- Контактный лист: `{result.contact_sheet}`",
            f"- Описание персонажей: `{result.characters}`",
            f"- Копия сцены: `{result.scene_copy}`",
            f"- Озвучка: `{result.voice_track or 'нет'}`",
            f"- Сгенерировано: {result.generated_at}",
            "",
            "## Параметры",
            "",
            f"- Длительность: {result.duration:.2f} с",
            f"- FPS: {result.fps}",
            f"- Разрешение: {result.width}×{result.height}",
            f"- Кадров: {result.frames}",
            f"- Размер файла: {result.size_bytes / 1024 / 1024:.2f} МБ",
            "",
            "## Потоки (FFprobe)",
            "",
            *stream_lines,
            "",
            "## Проверки",
            "",
            f"- Полное декодирование FFmpeg: {'успешно' if result.decode_ok else 'ОШИБКИ'}",
            f"- Разница кадров при неподвижной камере: {result.motion_checks.get('frozen_camera_diffs')}",
            f"- Порог движения без камеры: > {result.motion_checks.get('frozen_camera_threshold')}",
            f"- Движение частей тела независимо от камеры: "
            f"{'подтверждено' if result.motion_checks.get('limbs_move_without_camera') else 'НЕ подтверждено'}",
            f"- Персонаж проходит по сцене: "
            f"{'да' if result.motion_checks.get('character_walks') else 'нет'}",
            f"- Детерминированность кадров: "
            f"{'подтверждена' if all(item['identical'] for item in determinism) else 'НАРУШЕНА'}",
            f"- Привязка объектов к ригу: "
            f"{'подтверждена' if result.attachment_checks.get('verified') else ('нет событий' if result.attachment_checks.get('enabled') is False else 'НЕ подтверждена')}",
            "",
            "### Смещение суставов и объектов (пиксели кадра)",
            "",
            "| Персонаж | Корпус | Рука лев. | Рука прав. | Стопа лев. | Стопа прав. | Голова |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in result.motion_checks.get("characters", []):
            travel = item.get("limb_travel_px", {})
            lines.append(
                f"| `{item['id']}` | {item.get('root_travel_px')} | {travel.get('hand_l')} | "
                f"{travel.get('hand_r')} | {travel.get('foot_l')} | {travel.get('foot_r')} | "
                f"{travel.get('head')} |"
            )
        moved_objects = result.motion_checks.get("objects_moved_px", {})
        if moved_objects:
            lines += ["", "| Объект | Смещение |", "| --- | --- |"]
            for name, value in moved_objects.items():
                lines.append(f"| `{name}` | {value} |")
        attachment = result.attachment_checks
        if attachment.get("enabled"):
            lines += [
                "",
                "### Привязка объектов",
                "",
                "| Объект | Персонаж | Точка | Интервал | Дистанция | Разброс | Следует за рукой |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for item in attachment.get("objects", []):
                for hold in item.get("holds", []):
                    lines.append(
                        f"| `{item['id']}` | `{hold['character']}` | `{hold['point']}` | "
                        f"{hold['start']}–{hold['end']} | {hold['distance_px']} | "
                        f"{hold['distance_spread_px']} | "
                        f"{'да' if hold['follows_hand'] else 'НЕТ'} |"
                    )
                if item.get("transfer_jump_px") is not None:
                    lines.append("")
                    lines.append(f"Скачок при передаче `{item['id']}`: {item['transfer_jump_px']} px.")
                if item.get("detach_jump_px") is not None:
                    lines.append(f"Скачок при отпускании `{item['id']}`: {item['detach_jump_px']} px.")
            lines += ["", attachment.get("note", "")]
        lines += [""]
        lip = result.lip_sync
        if lip.get("enabled"):
            lines += [
                "### Синхронизация рта с озвучкой",
                "",
                "| Персонаж | Реплика | Начало | Длит. | Рот макс. | Разброс | После реплики |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for item in lip.get("lines", []):
                lines.append(
                    f"| `{item['character']}` | {item['text']} | {item['start']} | "
                    f"{item['duration']} | {item['mouth_max']} | {item['mouth_variation']} | "
                    f"{item['mouth_after_line']} |"
                )
            lines += ["", lip.get("note", "")]
        else:
            lines += [f"Синхронизация рта: {lip.get('note', 'выключена')}"]
        lines += ["", "## Реализованные движения", ""]
        for action in sorted({item.action for item in scene.actions}):
            lines.append(f"- `{action}`")
        expressions = sorted({str(item.params.get("expression", "")) for item in scene.actions if item.params.get("expression")})
        if expressions:
            lines += ["", "Выражения лица: " + ", ".join(f"`{name}`" for name in expressions) + "."]
        if scene.events:
            lines += ["", "## События сцены", ""]
            for event in sorted(scene.events, key=lambda item: item.t):
                detail = f"- `{event.kind}` `{event.object}` в {event.t:.2f} с"
                if event.character:
                    detail += f", персонаж `{event.character}`"
                if event.target:
                    detail += f", получатель `{event.target}`"
                detail += f", точка `{event.point}`"
                lines.append(detail)
        lines += ["", "## Визуальный контроль v4", ""]
        if visual_qc:
            assets = visual_qc.get("assets", {}).get("characters", [])
            for entry in assets:
                lines.append(
                    f"- `{entry['id']}`: {entry.get('asset_system', entry.get('style', '?'))}, "
                    f"частей {len(entry.get('parts', []))}, выражений {len(entry.get('expressions', []))}, "
                    f"слоёв {len(entry.get('layers', []))}, ракурсов {len(entry.get('angles', []))}."
                )
            shots = visual_qc.get("shots", [])
            if shots:
                lines += ["", "| Время | План | Zoom |", "| --- | --- | --- |"]
                for shot in shots:
                    lines.append(f"| {shot['t']:.1f} с | {shot['shot']} | {shot['zoom']:.2f} |")
            framing = visual_qc.get("framing", {})
            lines += ["", f"Кадрирование: {'OK' if framing.get('ok') else 'ЕСТЬ ПРОБЛЕМЫ'}, "
                          f"проверок {framing.get('samples', 0)}."]
            for issue in framing.get("issues", []):
                lines.append(f"- {issue}")
            poses = visual_qc.get("poses", {})
            if poses.get("enabled"):
                lines += [
                    "",
                    f"Контроль поз: проверок {poses.get('samples', 0)}, "
                    f"{'нет подозрительных пересечений' if not poses.get('issues') else 'ЕСТЬ ПРОБЛЕМЫ'}.",
                ]
                for issue in poses.get("issues", []):
                    lines.append(f"- {issue}")
            silhouette = visual_qc.get("silhouette", {})
            if silhouette.get("enabled"):
                lines += [
                    "",
                    f"Пересечения силуэтов: {silhouette.get('status', '?')}, "
                    f"кадров {silhouette.get('checked_frames', 0)}, "
                    f"FAIL {silhouette.get('failure_count', 0)}, "
                    f"WARNING {silhouette.get('warning_count', 0)}.",
                ]
                for issue in silhouette.get("issues", [])[:12]:
                    lines.append(
                        f"- {issue.get('severity')} `{issue.get('kind')}` в "
                        f"{issue.get('timestamp')} с, {issue.get('area_px')} px"
                        + (f", diagnostic: `{issue['diagnostic']}`" if issue.get("diagnostic") else "")
                    )
        lines += ["", "## Не поддерживается в v4", ""]
        for name, reason in UNSUPPORTED.items():
            lines.append(f"- `{name}` — {reason}")
        if result.warnings:
            lines += ["", "## Предупреждения", ""]
            for warning in result.warnings:
                lines.append(f"- {warning}")
        lines += [
            "",
            "## Воспроизведение",
            "",
            "```powershell",
            f'.\\run.ps1 animate --scene "{result.scene_source}"',
            "```",
            "",
            "Движение рисуется процедурно (Pillow + numpy) и не использует ни одной",
            "генеративной модели; одинаковый вход даёт одинаковые кадры.",
            "",
        ]
        return "\n".join(lines)


def _orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    """Twice the signed area of triangle abc; sign tells left/right turn."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    eps: float = 1e-9,
) -> bool:
    """True when c lies on segment ab (inclusive)."""
    return (
        min(a[0], b[0]) - eps <= c[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= c[1] <= max(a[1], b[1]) + eps
    )


def _segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """Classic orientation-based segment intersection test (inclusive)."""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    eps = 1e-9
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    ):
        return True
    if abs(d1) < eps and _on_segment(p3, p4, p1):
        return True
    if abs(d2) < eps and _on_segment(p3, p4, p2):
        return True
    if abs(d3) < eps and _on_segment(p1, p2, p3):
        return True
    if abs(d4) < eps and _on_segment(p1, p2, p4):
        return True
    return False


def _span(points: list[tuple[float, float]]) -> float:
    """Largest distance between the sampled positions of a tracked point."""
    if len(points) < 2:
        return 0.0
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return float(np.linalg.norm([max(xs) - min(xs), max(ys) - min(ys)]))


def _hash_image(image: Image.Image, size: int = 16) -> str:
    small = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    return hashlib.sha256(small.tobytes()).hexdigest()[:16]
