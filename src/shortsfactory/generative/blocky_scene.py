"""Data-driven blocky scenes: validated JSON → rendered MP4.

Why this module exists
----------------------
The first blocky short was authored as a Python function
(:func:`.blocky_hero.first_film_scene`).  This module turns the same payload
mechanism into a *data* interface: a scene is a validated JSON document, so new
cartoons are created without touching Python.

A scene document (all times in seconds, all sizes in metres)::

    {
      "title": "Потерянный алмаз",
      "duration": 10.0, "fps": 30, "width": 1080, "height": 1920, "samples": 32,
      "actors": [
        {"character": "steve", "origin": [-0.35, 0.9, 0.0],
         "actions": [{"action": "walk", "start": 0.4, "duration": 2.0}, ...],
         "hops": [{"start": 5.7, "duration": 0.45}]},
        ...
      ],
      "props": [ {"preset": "tree", "x": -1.1, "y": 2.3},
                 {"name": "Door", "center": [...], "size": [...],
                  "rot_track": [[150, [0, 0, 1.9]]], "origin_offset": [...]} ],
      "camera_cuts": [{"frame": 1, "center": [0, 0, 0.9], "height": 3.4}],
      "environment": "forest",            // preset name or explicit dict
      "sfx_events": [[5.6, "sparkle"], [6.5, "boing"]]
    }

Validation is strict and error messages name the offending field: unknown
character, unknown action verb, actions beyond the scene duration, unknown
sound effect, unsorted/out-of-range camera cuts, duplicate prop names, unknown
environment keys.  Rendering reuses :mod:`.blocky_hero` end-to-end (payload →
Blender/EEVEE → SFX mux → contact sheet).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .animation_pipeline.blocky_hero import (
    CHARACTERS,
    build_blocky_scene_script,
    mux_audio,
    render_skinned,
    synth_sfx,
)
from .animation_pipeline.skinned_hero import PLAN_ACTION_STATES, viseme_track
from .blocky_camera import (
    plan_scene_shots,
    plan_shot_list,
    shot_summary,
    validate_shot_list,
)
from .blocky_props import validate_carry
from ..subtitles import Cue, write_srt
from ..config import load_config
from .blocky_voice import (
    AMBIENCE_PRESETS,
    DEFAULT_AMBIENCE_GAIN,
    DEFAULT_SFX_GAIN,
    MixInput,
    VoiceTake,
    ambient_wav,
    apply_timings,
    mix_report,
    mix_scene_audio,
    schedule_dialogue,
    synthesize_dialogue,
    write_manifest,
)
from ..blend3d.host.sound import pcm16, render_events

#: procedurally synthesised sound effects (see blocky_hero.synth_sfx)
SFX_LENGTHS = {"sparkle": 1.2, "boing": 0.55, "pop": 0.12}

#: gain of the procedural music bed (Ч2.4).  Голос входит в микс с
#: пер-профильным volume (обычно >= 1.0), SFX — с DEFAULT_SFX_GAIN (0.45).
#: Подложка обязана оставаться под обоими, поэтому берёт ≈ четверть уровня
#: SFX (0.45 * 0.25 ≈ 0.11): достаточно, чтобы фон читался, и недостаточно,
#: чтобы маскировать реплику или звуковой эффект.
DEFAULT_MUSIC_GAIN = round(DEFAULT_SFX_GAIN * 0.25, 4)

#: средняя громкость готовой подложки, dBFS (Ч2.4).  ≈ -28: музыка
#: воспринимается как фон ещё до гейна микса.
MUSIC_TARGET_MEAN_DBFS = -28.0

#: длительность fade-in/fade-out подложки, с (Ч2.4)
MUSIC_FADE_S = 1.5


def _music_bed_events(duration: float) -> list[dict[str, Any]]:
    """События тихой музыкальной подложки: пэды по аккордам + редкие
    колокольчики (Ч2.4).  Синтез переиспользует движок
    :mod:`..blend3d.host.sound` (bell/pad через ``render_events``), ничего
    нового не дублируется.  Колокольчики не заходят в хвостовую fade-зону,
    чтобы конец файла затухал так же мягко, как начинается."""
    events: list[dict[str, Any]] = []
    chords = (
        ("C3", "E3", "G3"), ("A2", "C3", "E3"),
        ("F2", "A2", "C3"), ("G2", "B2", "D3"),
    )
    bar = 4.0
    moment, index = 0.0, 0
    while moment < duration - 1e-6:
        events.append({
            "t": round(moment, 3), "kind": "pad",
            "freqs": list(chords[index % len(chords)]),
            "dur": round(min(bar, duration - moment), 3), "gain": 0.06,
        })
        moment += bar
        index += 1
    bells = ("C5", "E5", "G5", "B4")
    moment, index = 1.0, 0
    while moment < duration - MUSIC_FADE_S - 0.1:
        events.append({
            "t": round(moment, 3), "kind": "bell",
            "freq": bells[index % len(bells)],
            "dur": 1.2, "gain": 0.10, "pan": -0.2 + 0.4 * (index % 2),
        })
        moment += 2.0
        index += 1
    return events


def _write_procedural_music(path: Path, duration: float, *,
                            sr: int = 48000) -> Path:
    """Пишет тихую процедурную музыкальную подложку в WAV (Ч2.4).

    События собирает :func:`_music_bed_events`, рендер — движок
    ``blend3d.host.sound`` (``render_events``: bell/pad + мастер-шина).
    Поверх мастер-шины накладывается длинный степенной fade-in/out
    (``MUSIC_FADE_S``); кривая ``sin^3`` выбрана так, чтобы первые и
    последние 0.5 с файла были тише середины более чем на 20 дБ.  Затем
    весь микс нормируется к средней громкости ``MUSIC_TARGET_MEAN_DBFS``
    (с потолком по пику), чтобы подложка гарантированно оставалась
    фоном под голосом и SFX.
    """
    import numpy as np

    duration = max(0.1, float(duration))
    mix = render_events(_music_bed_events(duration), duration, sr=sr, peak=0.9)
    fade = min(MUSIC_FADE_S, duration / 2.0)
    nf = int(fade * sr)
    if nf > 0:
        ramp = np.sin(np.linspace(0.0, np.pi / 2.0, nf)) ** 3
        mix[:nf] *= ramp[:, None]
        mix[-nf:] *= ramp[::-1][:, None]
    mean_abs = float(np.abs(mix).mean())
    if mean_abs > 0.0:
        mix *= (10.0 ** (MUSIC_TARGET_MEAN_DBFS / 20.0)) / mean_abs
    peak = float(np.abs(mix).max())
    if peak > 0.95:  # страховка от клиппинга после нормировки средней
        mix *= 0.95 / peak
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wav_pcm16_stereo(mix, sr))
    return path


def _wav_pcm16_stereo(mix, sr: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16(mix))
    return buf.getvalue()

#: named environments (explicit dicts from the JSON always win)
ENVIRONMENT_PRESETS: dict[str, dict[str, Any]] = {
    "forest": {
        "sky_rgb": [0.45, 0.66, 0.90], "sky_strength": 0.65,
        "ground_rgb": [0.30, 0.55, 0.24], "sun_energy": 2.1,
    },
    "meadow": {
        "sky_rgb": [0.52, 0.72, 0.94], "sky_strength": 0.75,
        "ground_rgb": [0.36, 0.60, 0.26], "sun_energy": 2.4,
    },
    "cave": {
        "sky_rgb": [0.10, 0.10, 0.14], "sky_strength": 0.25,
        "ground_rgb": [0.30, 0.30, 0.33], "sun_energy": 1.2,
    },
}


# --------------------------------------------------------------------------
# prop presets: compact JSON entries expanded into explicit box definitions
# --------------------------------------------------------------------------


def _expand_prop(prop: dict[str, Any], *, frames: int | None = None) -> list[dict[str, Any]]:
    """Expand a preset into boxes and give each box the same carry spec.

    A carried prop may consist of several boxes (an umbrella cap and handle,
    a glowing cube plus its light).  Every box keeps its own offset from the
    preset anchor, so one carry spec moves the whole object rigidly.
    """
    boxes = _expand_prop_boxes(prop, frames=frames)
    carry = prop.get("carry")
    if carry and boxes:
        anchor = boxes[0].get("center") or [0.0, 0.0, 0.0]
        for box in boxes:
            box["carry"] = dict(carry)
            centre = box.get("center") or anchor
            box["carry_offset"] = [
                round(float(centre[i]) - float(anchor[i]), 4) for i in range(3)
            ]
    return boxes


def _expand_prop_boxes(
    prop: dict[str, Any], *, frames: int | None = None
) -> list[dict[str, Any]]:
    """Expand a ``{"preset": ...}`` entry into concrete prop boxes."""
    preset = str(prop.get("preset") or "")
    x = float(prop.get("x", 0.0))
    y = float(prop.get("y", 0.0))
    if preset == "tree":
        base = prop.get("name")
        return [
            {"name": (f"{base} trunk" if base else f"Trunk_{x}_{y}"), "center": [x, y, 0.55],
             "size": [0.24, 0.24, 1.10], "rgb": [0.38, 0.26, 0.15]},
            {"name": (f"{base} leaves" if base else f"Leaves_{x}_{y}"), "center": [x, y, 1.45],
             "size": [1.10, 1.10, 0.80], "rgb": [0.20, 0.46, 0.18]},
            {"name": (f"{base} leaves top" if base else f"LeavesTop_{x}_{y}"), "center": [x, y, 2.02],
             "size": [0.75, 0.75, 0.50], "rgb": [0.24, 0.52, 0.20]},
        ]
    if preset == "rock":
        return [{"name": prop.get("name") or f"Rock_{x}_{y}", "center": [x, y, 0.14],
                 "size": [0.42, 0.36, 0.28], "rgb": [0.48, 0.48, 0.50], "roughness": 0.9}]
    if preset == "grass":
        return [{"name": prop.get("name") or f"Grass_{x}_{y}", "center": [x, y, 0.06],
                 "size": [0.12, 0.12, 0.12], "rgb": [0.30, 0.60, 0.22]}]
    if preset == "stump":
        h = float(prop.get("height", 0.80))
        return [{"name": prop.get("name") or f"Stump_{x}_{y}", "center": [x, y, h / 2.0],
                 "size": [0.55, 0.55, h], "rgb": [0.40, 0.28, 0.16], "roughness": 0.9}]
    if preset == "glowing_cube":
        size = float(prop.get("size", 0.34))
        # без явного z предмет покоится на земле, а не парит на «демочной»
        # высоте (дефект run_new: «ключ» висел в полуметре над полом)
        z = float(prop.get("z", size / 2.0))
        flash_frame = int(prop.get("flash_frame", 171))
        return [
            {"name": prop.get("name") or "GlowCube", "center": [x, y, z],
             "size": [size, size, size], "rgb": [0.30, 0.34, 0.38], "roughness": 0.4,
             "emissive": True, "emission_rgb": prop.get("emission_rgb", [0.55, 0.95, 1.00]),
             "emission_track": prop.get("emission_track", [
                 [1, 0.6], [flash_frame - 3, 1.2], [flash_frame - 1, 2.0],
                 [flash_frame + 1, 9.0], [flash_frame + 12, 2.5]]),
             **({"pos_track": prop["pos_track"]} if prop.get("pos_track") else {})},
            {"name": (prop.get("name") or "GlowCube") + "Light", "type": "light",
             "center": [x, y, z + 0.1], "rgb": prop.get("emission_rgb", [0.6, 0.95, 1.0]),
             "energy_track": prop.get("energy_track", [
                 [1, 2.0], [flash_frame - 6, 3.0], [flash_frame, 80.0],
                 [flash_frame + 15, 14.0]])},
        ]
    if preset == "door":
        # B-01/RC1: дверь статична, пока сцена явно не задаст событие открытия
        # (open_frame) или состояние "open".  Повторные дефолтные keyframes
        # запрещены — они «открывали» дверь в каждой сцене после склейки.
        state = str(prop.get("state") or "closed").strip().lower()
        open_frame = prop.get("open_frame")
        if state == "open":
            # уже открыта: статичная открытая дверь без анимации
            open_frame = None
            angle = 0.0
        else:
            angle = float(prop.get("angle", 1.9))
        if open_frame is None:
            rot_track = [[1, [0.0, 0.0, 0.0]]]
        else:
            open_frame = max(1, int(open_frame))
            end_frame = int(prop.get("end_frame", open_frame + 400))
            last_frame = max(open_frame, int(frames or (open_frame + 60)))
            end_frame = max(open_frame + 1, min(end_frame, last_frame))
            rot_track = [
                [1, [0.0, 0.0, 0.0]],
                [open_frame, [0.0, 0.0, 0.0]],
                [open_frame + 12, [0.0, 0.0, angle]],
                [end_frame, [0.0, 0.0, angle]],
            ]
        return [
            {"name": prop.get("name") or "Door", "center": [x, y, 1.0],
             "size": [0.90, 0.12, 2.00], "rgb": prop.get("rgb", [0.52, 0.34, 0.18]),
             "roughness": 0.8, "origin_offset": [-0.45, 0.0, 0.0],
             "rot_track": rot_track},
        ]
    if preset == "spring_box":
        arc = prop.get("pos_track") or []
        return [{"name": prop.get("name") or "SpringBox", "center": [x, y, 0.30],
                 "size": [0.22, 0.22, 0.22], "rgb": prop.get("rgb", [0.90, 0.45, 0.15]),
                 "roughness": 0.5, **({"pos_track": arc} if arc else {})}]
    if preset == "key":
        # маленький блочный ключ: стержень + головка, лежит на земле
        base = prop.get("name")
        return [
            {"name": (f"{base} shaft" if base else f"KeyShaft_{x}_{y}"), "center": [x, y, 0.025],
             "size": [0.06, 0.05, 0.30], "rgb": [0.72, 0.58, 0.22], "roughness": 0.45,
             "metallic": 0.6},
            {"name": (f"{base} bow" if base else f"KeyBow_{x}_{y}"), "center": [x, y, 0.20],
             "size": [0.16, 0.05, 0.16], "rgb": [0.72, 0.58, 0.22], "roughness": 0.45,
             "metallic": 0.6},
        ]
    if preset == "chest":
        # блочный сундук: корпус + тёмная крышка с полосой
        base = prop.get("name")
        return [
            {"name": (f"{base} base" if base else f"ChestBase_{x}_{y}"), "center": [x, y, 0.21],
             "size": [0.70, 0.45, 0.42], "rgb": [0.44, 0.30, 0.16], "roughness": 0.85},
            {"name": (f"{base} lid" if base else f"ChestLid_{x}_{y}"), "center": [x, y, 0.47],
             "size": [0.74, 0.47, 0.16], "rgb": [0.30, 0.20, 0.11], "roughness": 0.85},
        ]
    if preset == "umbrella":
        # two boxes (cap + handle) that move as one: identical sync, the
        # handle's track mirrors the cap's with a fixed -Z offset
        cap_z = float(prop.get("z", 2.2))
        track = prop.get("rel_track") or [[1, [x, y, cap_z]]]
        sync = prop.get("sync")
        name = prop.get("name") or "Umbrella"
        out: list[dict[str, Any]] = []
        for suffix, dz, size, rgb, rough in (
            ("Cap", 0.0, [0.72, 0.72, 0.30], prop.get("rgb", [0.75, 0.18, 0.18]), 0.5),
            ("Handle", -0.93, [0.07, 0.07, 0.96], [0.55, 0.55, 0.58], 0.4),
        ):
            item: dict[str, Any] = {"name": f"{name}{suffix}", "center": [x, y, cap_z + dz],
                                    "size": size, "rgb": rgb, "roughness": rough}
            item["pos_track"] = [[f, [px, py, pz + dz]] for f, (px, py, pz) in track]
            if sync:
                item["sync"] = dict(sync)
            out.append(item)
        return out
    known_presets = (
        "chest", "door", "glowing_cube", "grass", "key",
        "rock", "spring_box", "stump", "tree", "umbrella",
    )
    raise ValueError(
        f"Неизвестный пресет пропса «{preset}». "
        f"Доступно: {', '.join(sorted(known_presets))}."
    )


# --------------------------------------------------------------------------
# scene document
# --------------------------------------------------------------------------


def _distribute_dialogue(
    actors: list[dict[str, Any]], dialogue: list[dict[str, Any]]
) -> None:
    """Copy every spoken line into its actor's ``voice_lines``.

    The renderer only knows per-actor ``voice_lines``; keeping the scene-level
    ``dialogue`` list as the authored source means a line is written once and
    still drives the mouth of the right character.  Malformed lines are left out
    here — ``validate()`` reports them with a readable message instead.
    """
    by_character = {str(actor.get("character") or ""): actor for actor in actors}
    for line in dialogue:
        try:
            entry = {
                "start": float(line.get("start") or 0.0),
                "duration": float(line.get("duration") or 0.0),
                "text": str(line.get("text") or "").strip(),
            }
        except (TypeError, ValueError):
            continue
        actor = by_character.get(str(line.get("character") or ""))
        if actor is None or entry["duration"] <= 0 or not entry["text"]:
            continue
        # lip-sync measured from the real take travels with the line
        if line.get("wav"):
            entry["wav"] = str(line["wav"])
        if line.get("envelope"):
            entry["envelope"] = list(line["envelope"])
            entry["envelope_hz"] = float(line.get("envelope_hz") or 0.0)
        if line.get("segments"):
            entry["segments"] = [list(span) for span in line["segments"]]
        actor.setdefault("voice_lines", []).append(entry)


# --------------------------------------------------- automatic acting (B4/B5)


def _apply_mouth_skins(actors: list[dict[str, Any]], lang: str) -> int:
    """Give every speaking actor a ``mouth_skin`` so the payload gets mouth states.

    Без ``mouth_skin`` сборщик payload (``build_blocky_scene_payload``) не
    строит ``mouth_states`` — скиновое переключение closed/round/open не
    работает, и рот остаётся статичным.  Сегменты речи резолвер берёт из
    ``voice_lines`` (измерены по WAV), либо пересчитает сам WAV строки.
    Возвращает число актёров, получивших ``mouth_skin``.
    """
    added = 0
    for actor in actors:
        spoken = [
            line for line in actor.get("voice_lines") or []
            if str(line.get("text") or "").strip()
        ]
        if not spoken:
            continue
        try:
            first = min(float(line.get("start") or 0.0) for line in spoken)
            last = max(
                float(line.get("start") or 0.0) + float(line.get("duration") or 0.0)
                for line in spoken
            )
        except (TypeError, ValueError):
            continue
        if last <= first:
            continue
        actor["mouth_skin"] = {
            "text": " ".join(str(line.get("text") or "").strip() for line in spoken),
            "lang": str(lang or "ru"),
            "start": round(first, 3),
            "duration": round(last - first, 3),
        }
        added += 1
    return added


#: verbs used for the automatic listener reaction, alternating per line so a
#: long dialogue does not repeat the same nod on every sentence
LISTENER_REACTION = "listen_nod"
LISTENER_HOLD = "listen"


def _overlaps(act: dict[str, Any], start: float, duration: float, *, pad: float = 0.05) -> bool:
    """Does ``act`` share time with the ``[start, start+duration]`` window?"""
    a_start = float(act.get("start") or 0.0)
    a_end = a_start + float(act.get("duration") or 0.0)
    return a_start < start + duration + pad and start - pad < a_end


def _actor_x(actors: list[dict[str, Any]], who: str) -> float:
    """World X of an actor (0.0 when it is unknown or malformed)."""
    for actor in actors:
        if str(actor.get("character") or "") == who:
            origin = actor.get("origin") or [0.0, 0.0, 0.0]
            try:
                return float(origin[0] or 0.0)
            except (TypeError, ValueError, IndexError):
                return 0.0
    return 0.0


def _apply_line_emotions(
    actors: list[dict[str, Any]], dialogue: list[dict[str, Any]]
) -> int:
    """Move a line's ``emotion`` onto the speaker's overlapping action.

    A scene can then say "this line is angry" instead of repeating the emotion
    in the action list; an emotion the action already carries always wins.
    """
    by_character = {str(actor.get("character") or ""): actor for actor in actors}
    applied = 0
    for line in dialogue:
        emotion = str(line.get("emotion") or "").strip().lower()
        if not emotion:
            continue
        actor = by_character.get(str(line.get("character") or ""))
        if actor is None:
            continue
        start = float(line.get("start") or 0.0)
        duration = float(line.get("duration") or 0.0)
        intensity = float(line.get("emotion_intensity") or 1.0)
        actions = actor.setdefault("actions", [])
        # carve the speaker's idle so the emotion lives exactly on the line
        # instead of tinting the whole standing pose before and after it
        carved = _carve_idle(actions, start, duration, min_part=0.0)
        covered = [
            act
            for act in actions
            if float(act.get("start") or 0.0) <= start + 1e-6
            and float(act.get("start") or 0.0) + float(act.get("duration") or 0.0)
            >= start + duration - 1e-6
        ]
        if not covered and carved:
            # the speaker was only standing: give the line its own idle beat
            actions.append(
                {"action": "idle", "start": round(start, 3), "duration": round(duration, 3)}
            )
            covered = [actions[-1]]
        for act in covered:
            params = dict(act.get("params") or {})
            if str(params.get("emotion") or "").strip():
                continue
            params["emotion"] = emotion
            params["emotion_intensity"] = intensity
            act["params"] = params
            applied += 1
    return applied


#: verbs that are just standing still; a reaction may carve them up
IDLE_VERBS = frozenset({"idle", "stand", "wait", "return_idle"})


def _carve_idle(
    actions: list[dict[str, Any]],
    start: float,
    duration: float,
    *,
    min_part: float = 0.15,
) -> bool:
    """Free the ``[start, start+duration]`` window by trimming idle actions.

    A listener usually stands through the whole scene, so its ``idle`` would
    block every reaction.  Idle is split into "before" and "after" pieces and
    the window is left free; parts shorter than ``min_part`` are dropped instead
    of producing a twitch.  Returns False when an *authored* (non-idle) action
    occupies the window — the caller then leaves the listener alone.
    """
    begin, end = start, start + duration
    for act in actions:
        a_start = float(act.get("start") or 0.0)
        a_end = a_start + float(act.get("duration") or 0.0)
        if a_end <= begin + 1e-6 or a_start >= end - 1e-6:
            continue
        if str(act.get("action") or "").strip().lower() not in IDLE_VERBS:
            return False
    kept: list[dict[str, Any]] = []
    for act in actions:
        a_start = float(act.get("start") or 0.0)
        a_end = a_start + float(act.get("duration") or 0.0)
        if a_end <= begin + 1e-6 or a_start >= end - 1e-6:
            kept.append(act)
            continue
        if a_start < begin - 1e-6 and begin - a_start >= min_part:
            kept.append({**act, "start": round(a_start, 3), "duration": round(begin - a_start, 3)})
        if a_end > end + 1e-6 and a_end - end >= min_part:
            kept.append({**act, "start": round(end, 3), "duration": round(a_end - end, 3)})
    actions[:] = kept
    return True


def _apply_listener_reactions(
    actors: list[dict[str, Any]], dialogue: list[dict[str, Any]]
) -> int:
    """Make every non-speaker react to each line, facing the speaker.

    The reaction is only added where the listener has no *authored* action: the
    rig timeline cannot overlap segments, and silently shifting an authored
    beat would ruin the staging.  Standing idle in the window is carved out.
    ``params["direction"]`` mirrors the head turn so the listener really looks
    at the speaker instead of past him.
    """
    added = 0
    for index, line in enumerate(dialogue):
        speaker = str(line.get("character") or "")
        start = float(line.get("start") or 0.0)
        duration = float(line.get("duration") or 0.0)
        if duration <= 0:
            continue
        speaker_x = _actor_x(actors, speaker)
        for actor in actors:
            who = str(actor.get("character") or "")
            if who == speaker or who not in CHARACTERS:
                continue
            actions = actor.setdefault("actions", [])
            if not _carve_idle(actions, start, duration):
                continue
            direction = 1.0 if speaker_x >= _actor_x(actors, who) else -1.0
            verb = LISTENER_REACTION if index % 2 == 0 else LISTENER_HOLD
            actions.append(
                {
                    "action": verb,
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "params": {"direction": direction},
                }
            )
            actions.sort(key=lambda act: float(act.get("start") or 0.0))
            added += 1
    return added


def _apply_acting(
    actors: list[dict[str, Any]], dialogue: list[dict[str, Any]]
) -> None:
    """Apply both automatic acting layers (B4 emotions, B5 listener reactions)."""
    _apply_line_emotions(actors, dialogue)
    _apply_listener_reactions(actors, dialogue)


@dataclass
class BlockyScene:
    title: str = ""
    duration: float = 10.0
    fps: int = 30
    width: int = 1080
    height: int = 1920
    samples: int = 32
    actors: list[dict[str, Any]] = field(default_factory=list)
    props: list[dict[str, Any]] = field(default_factory=list)
    camera_cuts: list[dict[str, Any]] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    sfx_events: list[list[Any]] = field(default_factory=list)
    #: spoken lines, in scene time.  A line turns into mouth animation (visemes)
    #: for its own character and into a subtitle cue; ``duration`` is the real
    #: length of the synthesised take, not an estimate.
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    #: subtitle language, also the name of the generated ``<lang>.srt``.
    lang: str = "ru"
    #: procedural background bed: forest | cave | meadow | "" (silence)
    ambience: str = ""
    #: dialogue-driven acting: listener reactions and per-line emotions
    auto_act: bool = False
    #: director camera: build the shot list from the dialogue (C7–C9)
    auto_camera: bool = False

    # ------------------------------------------------------------ parsing
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlockyScene":
        env = data.get("environment") or {}
        if isinstance(env, str):
            if env not in ENVIRONMENT_PRESETS:
                raise ValueError(
                    f"Неизвестный пресет окружения «{env}». "
                    f"Доступно: {', '.join(sorted(ENVIRONMENT_PRESETS))}."
                )
            env = dict(ENVIRONMENT_PRESETS[env])
        props: list[dict[str, Any]] = []
        duration_raw = float(data.get("duration") or 0.0)
        fps_raw = int(data.get("fps") or 30)
        prop_frames = max(1, int(round(duration_raw * fps_raw))) if duration_raw > 0 else None
        for raw in data.get("props") or []:
            if "preset" in raw:
                logical_name = str(raw.get("name") or "").strip()
                boxes = _expand_prop(raw, frames=prop_frames)
                for box in boxes:
                    if logical_name:
                        box["prop"] = logical_name
                props.extend(boxes)
            else:
                box = dict(raw)
                box.setdefault("prop", box.get("name"))
                props.append(box)
        actors: list[dict[str, Any]] = []
        for raw in data.get("actors") or []:
            actor = dict(raw)
            actor["actions"] = [dict(a) for a in raw.get("actions") or []]
            actor["hops"] = [dict(h) for h in raw.get("hops") or []]
            actors.append(actor)
        dialogue = [dict(line) for line in data.get("dialogue") or []]
        auto_act = bool(data.get("auto_act"))
        auto_camera = bool(data.get("auto_camera"))
        _distribute_dialogue(actors, dialogue)
        if auto_act:
            _apply_acting(actors, dialogue)
        cuts = [dict(c) for c in data.get("camera_cuts") or []]
        if auto_camera and not cuts:
            cuts = plan_scene_shots(
                dialogue,
                actors,
                props,
                duration=float(data.get("duration") or 10.0),
                fps=int(data.get("fps") or 30),
            )
        return cls(
            title=str(data.get("title") or ""),
            duration=float(data.get("duration") or 10.0),
            fps=int(data.get("fps") or 30),
            width=int(data.get("width") or 1080),
            height=int(data.get("height") or 1920),
            samples=int(data.get("samples") or 32),
            actors=actors,
            props=props,
            camera_cuts=cuts,
            environment=dict(env),
            sfx_events=[list(e) for e in data.get("sfx_events") or []],
            dialogue=dialogue,
            lang=str(data.get("lang") or "ru"),
            ambience=str(data.get("ambience") or ""),
            auto_act=auto_act,
            auto_camera=auto_camera,
        )

    @classmethod
    def load(cls, path: str | Path) -> "BlockyScene":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Файл сцены не найден: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Сцена «{path}» — некорректный JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Сцена «{path}»: корень JSON должен быть объектом.")
        return cls.from_dict(data)

    # -------------------------------------------------------- validation
    def validate(self) -> None:
        """Raise ValueError with a named, human-readable reason on any defect."""
        if self.duration <= 0:
            raise ValueError(f"Некорректная длительность сцены: {self.duration}.")
        if self.fps <= 0:
            raise ValueError(f"Некорректный FPS: {self.fps}.")
        if not self.actors:
            raise ValueError("В сцене нет ни одного актёра.")
        if self.ambience and self.ambience not in AMBIENCE_PRESETS:
            raise ValueError(
                f"Неизвестный амбиент «{self.ambience}». Доступно: "
                + ", ".join(AMBIENCE_PRESETS)
                + "."
            )
        last_frame = int(round(self.duration * self.fps))

        for idx, actor in enumerate(self.actors, 1):
            key = str(actor.get("character") or "")
            if key not in CHARACTERS:
                raise ValueError(
                    f"Актёр #{idx}: неизвестный персонаж «{key}». "
                    f"Доступно: {', '.join(sorted(CHARACTERS))}."
                )
            actions = actor.get("actions") or []
            if not actions:
                raise ValueError(f"Актёр «{key}»: список действий пуст.")
            for act in actions:
                name = str(act.get("action") or "").strip().lower()
                if not name:
                    raise ValueError(f"Актёр «{key}»: действие без названия.")
                if name not in PLAN_ACTION_STATES:
                    raise ValueError(
                        f"Актёр «{key}»: неподдерживаемое действие «{name}». "
                        f"Доступно: {', '.join(sorted(PLAN_ACTION_STATES))}."
                    )
                start = float(act.get("start") or 0.0)
                dur = float(act.get("duration") or 0.0)
                if start < 0 or dur <= 0:
                    raise ValueError(
                        f"Актёр «{key}»: некорректный интервал действия «{name}» "
                        f"(start={start}, duration={dur})."
                    )
                if start + dur > self.duration + 1e-6:
                    raise ValueError(
                        f"Актёр «{key}»: действие «{name}» заканчивается в "
                        f"{start + dur:.2f} с при длительности сцены {self.duration:.2f} с."
                    )
                prop_ref = (act.get("params") or {}).get("prop")
                known_props = {
                    str(p.get("prop") or p.get("name") or "") for p in self.props
                }
                if prop_ref and prop_ref not in known_props:
                    raise ValueError(
                        f"Актёр «{key}»: действие «{name}» ссылается на несуществующий "
                        f"пропс «{prop_ref}»."
                    )
            for hop in actor.get("hops") or []:
                hs = float(hop.get("start") or 0.0)
                hd = float(hop.get("duration") or 0.45)
                if hs < 0 or hs + hd > self.duration + 1e-6:
                    raise ValueError(
                        f"Актёр «{key}»: прыжок (hop) вне сцены: start={hs}, duration={hd}."
                    )

        names: set[str] = set()
        cast = {str(a.get("character") or "") for a in self.actors}
        spans: dict[str, list[tuple[float, float, int]]] = {}
        for idx, line in enumerate(self.dialogue, 1):
            who = str(line.get("character") or "")
            if who not in cast:
                raise ValueError(
                    f"Реплика #{idx}: неизвестный персонаж «{who}». "
                    f"В сцене играют: {', '.join(sorted(cast))}."
                )
            text = str(line.get("text") or "").strip()
            if not text:
                raise ValueError(f"Реплика #{idx}: пустой текст.")
            try:
                lstart = float(line.get("start") or 0.0)
                ldur = float(line.get("duration") or 0.0)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Реплика #{idx} («{text[:24]}»): start и duration должны быть числами."
                ) from exc
            if lstart < 0 or ldur <= 0:
                raise ValueError(
                    f"Реплика #{idx} («{text[:24]}»): некорректный интервал "
                    f"(start={lstart}, duration={ldur})."
                )
            if lstart + ldur > self.duration + 1e-6:
                raise ValueError(
                    f"Реплика #{idx} («{text[:24]}») заканчивается в "
                    f"{lstart + ldur:.2f} с при длительности сцены {self.duration:.2f} с."
                )
            for s0, s1, other in spans.get(who, []):
                if lstart < s1 - 1e-6 and s0 < lstart + ldur - 1e-6:
                    raise ValueError(
                        f"Реплики #{other} и #{idx} персонажа «{who}» пересекаются "
                        f"({s0:.2f}–{s1:.2f} с и {lstart:.2f}–{lstart + ldur:.2f} с)."
                    )
            spans.setdefault(who, []).append((lstart, lstart + ldur, idx))

        for prop in self.props:
            pname = str(prop.get("name") or "")
            if not pname:
                raise ValueError("Пропс без имени (поле name).")
            if pname in names:
                raise ValueError(f"Дублирующееся имя пропса «{pname}».")
            names.add(pname)
            sync = prop.get("sync")
            if sync:
                actor = next(
                    (a for a in self.actors if a.get("character") == sync.get("actor")),
                    None,
                )
                if actor is None:
                    raise ValueError(
                        f"Пропс «{pname}»: sync ссылается на неизвестного актёра "
                        f"«{sync.get('actor')}»."
                    )
                verbs = {
                    str(a.get("action") or "").strip().lower()
                    for a in actor.get("actions") or []
                }
                if str(sync.get("action") or "").strip().lower() not in verbs:
                    raise ValueError(
                        f"Пропс «{pname}»: у актёра «{sync.get('actor')}» нет "
                        f"действия «{sync.get('action')}»."
                    )

        carry_problems = validate_carry(self.props, self.actors, self.duration)
        if carry_problems:
            raise ValueError("Предметы некорректны: " + "; ".join(carry_problems))

        prev_frame = 0
        for idx, cut in enumerate(self.camera_cuts, 1):
            frame = int(cut.get("frame") or 0)
            if frame <= prev_frame:
                raise ValueError(
                    f"Склейка камеры #{idx}: кадр {frame} не больше предыдущего "
                    f"({prev_frame}); склейки должны идти по возрастанию."
                )
            if frame < 1 or frame > last_frame:
                raise ValueError(
                    f"Склейка камеры #{idx}: кадр {frame} вне диапазона 1..{last_frame}."
                )
            center = cut.get("center") or []
            if len(center) != 3:
                raise ValueError(
                    f"Склейка камеры #{idx}: center должен содержать 3 координаты."
                )
            if float(cut.get("height") or 0.0) <= 0:
                raise ValueError(f"Склейка камеры #{idx}: некорректная высота кадра.")
            prev_frame = frame
        if self.camera_cuts and self.camera_cuts[0].get("frame") != 1:
            raise ValueError("Первая склейка камеры должна начинаться с кадра 1.")
        if self.auto_camera:
            problems = validate_shot_list(self.camera_cuts, self.duration, self.fps)
            if problems:
                raise ValueError("План камеры некорректен: " + "; ".join(problems))

        for idx, event in enumerate(self.sfx_events, 1):
            if len(event) != 2:
                raise ValueError(f"SFX #{idx}: ожидается пара [время, вид].")
            t, kind = float(event[0]), str(event[1])
            if kind not in SFX_LENGTHS:
                raise ValueError(
                    f"SFX #{idx}: неизвестный вид «{kind}». "
                    f"Доступно: {', '.join(sorted(SFX_LENGTHS))}."
                )
            if t < 0 or t > self.duration:
                raise ValueError(f"SFX #{idx}: время {t} вне сцены (0..{self.duration}).")

    # ---------------------------------------------------------- subtitles
    def subtitle_cues(self) -> list[Cue]:
        """One subtitle cue per spoken line, prefixed with the speaker's name."""
        cues: list[Cue] = []
        for line in self.dialogue:
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            who = str(line.get("character") or "")
            name = CHARACTERS[who].name if who in CHARACTERS else who
            start = float(line.get("start") or 0.0)
            cues.append(
                Cue(
                    start=start,
                    end=start + float(line.get("duration") or 0.0),
                    text=f"{name}: {text}",
                    scene_id=self._slug(),
                )
            )
        return sorted(cues, key=lambda cue: cue.start)

    def dialogue_qc(self) -> dict[str, Any]:
        """Speech QC: every line needs voice, a subtitle cue and mouth shapes.

        The check runs on the *measured* timings (``render`` rewrites the
        dialogue after TTS), so it verifies what the viewer actually gets: the
        take exists, the cue does not overlap its neighbour, the letter track
        produces more than one non-``rest`` shape and the reading speed is
        human.  ``ok`` is False as soon as one line breaks a rule; every issue
        names the line, so a failure is actionable and not just a red flag.
        """
        issues: list[str] = []
        cues = self.subtitle_cues()
        by_character = {str(actor.get("character") or ""): actor for actor in self.actors}
        lines: list[dict[str, Any]] = []
        for index, line in enumerate(self.dialogue, 1):
            text = str(line.get("text") or "").strip()
            who = str(line.get("character") or "")
            start = float(line.get("start") or 0.0)
            duration = float(line.get("duration") or 0.0)
            actor = by_character.get(who)
            track = (
                viseme_track(
                    text,
                    start=start,
                    duration=max(duration, 1e-3),
                    fps=self.fps,
                    segments=line.get("segments"),
                )
                if text
                else []
            )
            shapes = {name for _, name in track} - {"rest"}
            voices = [
                v
                for v in (actor or {}).get("voice_lines") or []
                if str(v.get("text") or "").strip() == text
            ]
            cue_ok = any(
                abs(cue.start - start) < 1e-3
                and abs(cue.end - (start + duration)) < 1e-3
                for cue in cues
            )
            cps = len(text) / duration if duration > 0 else 0.0
            entry = {
                "index": index,
                "character": who,
                "text": text,
                "start": round(start, 3),
                "duration": round(duration, 3),
                "voice": bool(voices),
                "cue": cue_ok,
                "visemes": len(shapes),
                "speech_s": round(
                    sum(
                        max(0.0, float(b) - float(a))
                        for a, b in (line.get("segments") or [])
                    ),
                    3,
                ),
                "chars_per_second": round(cps, 2),
            }
            lines.append(entry)
            if not text:
                issues.append(f"Реплика #{index}: пустой текст.")
            if who not in CHARACTERS:
                issues.append(f"Реплика #{index}: неизвестный персонаж «{who}».")
            if duration <= 0:
                issues.append(f"Реплика #{index}: нулевая длительность.")
            if start + duration > self.duration + 1e-3:
                issues.append(
                    f"Реплика #{index}: выходит за конец сцены "
                    f"({start + duration:.2f} > {self.duration:.2f} с)."
                )
            if actor is not None and (actor.get("voice_lines") or []) and not voices:
                issues.append(f"Реплика #{index}: нет voice_line у «{who}».")
            if text and len(shapes) < 2:
                issues.append(f"Реплика #{index}: слишком мало форм рта ({len(shapes)}).")
            if not cue_ok:
                issues.append(f"Реплика #{index}: субтитр не совпадает с репликой.")
            if cps > 24.0:
                issues.append(
                    f"Реплика #{index}: слишком быстро для озвучки "
                    f"({cps:.1f} симв./с)."
                )
        for previous, following in zip(cues, cues[1:]):
            if previous.end > following.start + 1e-6:
                issues.append(
                    f"Субтитры накладываются: {previous.start:.2f}–{previous.end:.2f} "
                    f"и {following.start:.2f}–{following.end:.2f}."
                )
        return {"ok": not issues, "issues": issues, "lines": lines}

    # ---------------------------------------------------------- rendering

    def _ambience_track_enabled(self) -> bool:
        """True, когда фоновая дорожка реально нужна.

        ``""`` и ``"none"`` означают «фона нет»: источник пропускается, а не
        заменяется искусственным шумом или пустым WAV.
        """
        name = str(self.ambience or "").strip().lower()
        return bool(name) and name != "none"

    def _music_enabled(self) -> bool:
        """Процедурная музыка включается флагом сцены ``music`` или конфигом
        ``audio.music`` (F6)."""
        if bool(getattr(self, "music", False)):
            return True
        try:
            return bool(load_config().get("audio", "music", False))
        except Exception:  # noqa: BLE001 - конфиг недоступен → без музыки
            return False

    def _scene_mix_inputs(
        self,
        take_inputs: list[MixInput],
        *,
        audio_dir: str | Path,
    ) -> list[MixInput]:
        """Порядок источников микса: реплики, затем SFX, затем фон/музыка.

        SFX и фон синтезируются только при наличии; ``ambience="none"`` и
        ``ambience=""`` фон не создают и не ломают микс диалогов.

        Музыкальная подложка (Ч2.4) синтезируется, когда включён конфиг
        ``audio.music`` (по умолчанию выключен — существующие «золотые»
        фильмы не меняются) или флаг сцены ``music``.  Её гейн
        (``DEFAULT_MUSIC_GAIN`` ≈ четверть уровня SFX) ставит фон строго
        под голос и SFX; сам WAV нормирован к ≈ -28 dBFS средней
        громкости с fade-in/out по 1.5 с (см. ``_write_procedural_music``).
        """
        inputs = list(take_inputs)
        audio_dir = Path(audio_dir)
        if self.sfx_events:
            sfx_path = audio_dir / "sfx.wav"
            sfx_path.write_bytes(
                synth_sfx(
                    [(float(t), str(k)) for t, k in self.sfx_events],
                    self.duration,
                )
            )
            inputs.append(MixInput(sfx_path, gain=DEFAULT_SFX_GAIN, label="sfx"))
        if self._ambience_track_enabled():
            amb_path = audio_dir / "ambience.wav"
            amb_path.write_bytes(ambient_wav(self.ambience, self.duration))
            inputs.append(
                MixInput(amb_path, gain=DEFAULT_AMBIENCE_GAIN, label="ambience")
            )
        if self._music_enabled():
            music_path = audio_dir / "music.wav"
            _write_procedural_music(music_path, self.duration)
            inputs.append(
                MixInput(music_path, gain=self._music_gain(), label="music")
            )
        return inputs

    def _music_gain(self) -> float:
        """Громкость музыки: ``config [audio] music_volume`` (F6).

        Значение из конфига используется как гейн подложки; если ключ
        отсутствует или некорректен — фолбэк на ``DEFAULT_MUSIC_GAIN``.
        """
        try:
            volume = float(load_config().get("audio", "music_volume",
                                              DEFAULT_MUSIC_GAIN))
        except Exception:  # noqa: BLE001 - конфиг недоступен → дефолт
            return DEFAULT_MUSIC_GAIN
        if volume < 0 or not (volume == volume):  # отрицательные/NaN
            return DEFAULT_MUSIC_GAIN
        return volume

    def _handoff_windows(self) -> list[tuple[float, float]]:
        """Окна активной передачи предмета (рукопожатие) для QC.

        В эти моменты силуэты актёров закономерно пересекаются — проверка
        silhouettes не трактует это как дефект.
        """
        windows: list[tuple[float, float]] = []
        for prop in (self.props or []):
            spec = (prop or {}).get("carry") or {}
            try:
                start = float(spec.get("transfer", {}).get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            if not spec.get("transfer"):
                continue
            dur = 0.3
            try:
                dur = float((spec.get("transfer") or {}).get("duration") or 0.3)
            except (TypeError, ValueError):
                pass
            windows.append((max(0.0, start - 0.5), start + dur + 1.0))
        return windows

    def render(
        self, out_dir: str | Path, *, timeout: int = 3600, voice: bool = True
    ) -> dict[str, Any]:
        """Validate → voices → payload → Blender → audio mix → SRT → report.

        The scene is voiced **before** it is rendered.  The measured length of
        each Piper take replaces the duration authored in the scene JSON, so the
        mouth animation, the subtitle cues and the audio mix all use the same
        numbers.  Set ``voice=False`` to render silently (no TTS call).
        """
        self.validate()
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        takes: list[VoiceTake] = []
        dialogue = [dict(line) for line in self.dialogue]
        if dialogue:
            # room between lines first: the authored durations are estimates,
            # and a take is normally longer than its slot
            dialogue = schedule_dialogue(dialogue)
        if dialogue and voice:
            takes = synthesize_dialogue(dialogue, out, load_config(), lang=self.lang)
            # re-schedule with the *measured* lengths, then move the takes to
            # the final starts so audio, mouth and subtitles share one timeline
            dialogue = schedule_dialogue(apply_timings(dialogue, takes))
            takes = [replace(take, start=line["start"]) for take, line in zip(takes, dialogue)]
            self.dialogue = dialogue
            last_end = max(
                (line["start"] + line["duration"] for line in dialogue), default=0.0
            )
            self.duration = max(self.duration, round(last_end + 0.35, 3))

        # every replay of the same scene object must start from the authored
        # lines, never from the voice_lines a previous render appended
        actors: list[dict[str, Any]] = []
        for actor in self.actors:
            fresh = {key: value for key, value in actor.items() if key != "voice_lines"}
            fresh["voice_lines"] = []
            actors.append(fresh)
        _distribute_dialogue(actors, dialogue)
        # A-audit/E2E: скиновая анимация рта обязана работать и в
        # production-пути — без mouth_skin сборщик payload не строит
        # mouth_states, и рот остаётся статичным closed.
        _apply_mouth_skins(actors, str(self.lang or "ru"))
        if self.auto_act:
            # same automatic acting as from_dict, but on the freshly timed lines
            _apply_acting(actors, dialogue)
        if self.auto_camera:
            # the shot list depends on the measured dialogue, so it is rebuilt
            # after TTS moved the lines, not reused from from_dict
            self.camera_cuts = plan_scene_shots(
                dialogue,
                actors,
                self.props,
                duration=self.duration,
                fps=self.fps,
            )
            validate_shot_list(self.camera_cuts, self.duration, self.fps)

        # The automatic acting may rebuild actor dicts without an explicit
        # duration; without it, timeline_from_actions collapses an actor's
        # timeline to the end of its last authored action (e.g. a short hold
        # ends early), so different actors get different frame counts.  Every
        # actor must animate across the whole scene, so force the scene
        # duration on all of them before the payload is built.
        for actor in actors:
            actor["duration"] = self.duration

        script = build_blocky_scene_script(
            out / "render",
            actors,
            props=self.props,
            camera_cuts=self.camera_cuts,
            environment=self.environment,
            scenario=self._slug(),
            fps=self.fps,
            width=self.width,
            height=self.height,
            samples=self.samples,
        )
        # 2.4: пре-рендер геометрический QC по payload — пересечения персонажей,
        # недосягаемые пропсы и обрезанные кадры останавливают рендер до Blender
        from .animation_pipeline.blocky_qc_gate import enforce_pre_render_gate
        enforce_pre_render_gate(
            out / "render", self._slug(), label=f"сцена «{self.title}»"
        )
        result = render_skinned(script, timeout=timeout)
        report: dict[str, Any] = {
            "title": self.title,
            "status": result.status,
            "error": result.error,
        }
        if result.status == "OK":
            video = Path(result.video)
            final = out / "final_audio.mp4"
            audio_dir = out / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            take_inputs: list[MixInput] = []
            for take in takes:
                take_inputs.append(
                    MixInput(
                        Path(take.wav),
                        start=take.start,
                        gain=take.profile.volume,
                        label=take.character,
                    )
                )
            inputs = self._scene_mix_inputs(take_inputs, audio_dir=audio_dir)
            if inputs:
                mix_path = audio_dir / "mix.wav"
                mix_scene_audio(mix_path, inputs, duration=self.duration)
                mux_audio(video, mix_path.read_bytes(), final)
                report["mix"] = mix_report(mix_path)
                report["audio_inputs"] = [item.to_dict() for item in inputs]
                # Микс готов только здесь: внутри render_skinned WAV ещё не
                # существует, поэтому QC лип-синка прикрепляем повторно.
                from .animation_pipeline.lip_sync_qc import attach_lip_sync_qc
                diag_path = out / "render" / f"{self._slug()}_diagnostics.json"
                if result.diagnostics is None:
                    result.diagnostics = {}
                report["lip_sync_qc"] = attach_lip_sync_qc(
                    out / "render", script, diag_path, result.diagnostics
                )
            else:
                # keep the deliverable name stable even for silent scenes
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                     "-c:v", "copy", str(final)],
                    check=True, shell=False,
                )
            report["video"] = str(video)
            report["final_audio"] = str(final)
            report["duration"] = self.duration
            report["diagnostics"] = result.diagnostics
            if takes:
                report["takes"] = [take.to_dict() for take in takes]
                report["voices_manifest"] = str(
                    write_manifest(takes, audio_dir / "voices.json")
                )
            if self.dialogue:
                subs = out / "subtitles"
                subs.mkdir(parents=True, exist_ok=True)
                report["subtitles"] = str(
                    write_srt(self.subtitle_cues(), subs / f"{self.lang}.srt")
                )
                # B6: speech QC on the measured timings, written into the report
                report["dialogue_qc"] = self.dialogue_qc()
            if self.auto_camera:
                problems = validate_shot_list(
                    self.camera_cuts, self.duration, self.fps
                )
                report["shot_list"] = shot_summary(self.camera_cuts, self.fps)
                report["camera_qc"] = {"ok": not problems, "issues": problems}
            sheet = out / "contact_sheet.jpg"
            # 12 tiles (4x3) must cover the whole film: a fixed 1.2 fps sheet
            # stopped at ~9 s of a 16 s scene and hid the last camera cuts.
            tile_cols, tile_rows = 4, 3
            rate = (tile_cols * tile_rows) / max(0.1, float(self.duration))
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(final),
                 "-vf", f"fps={rate:.6f},scale=300:-1,tile={tile_cols}x{tile_rows}",
                 "-frames:v", "1", str(sheet)],
                check=True, shell=False,
            )
            report["contact_sheet"] = str(sheet)
            # F1/B-07: QC-гейты влияют на итоговый статус сцены.  Status "OK"
            # возможен только когда все обязательные проверки пройдены.
            from .blocky_qc import PARTIAL as QC_PARTIAL, scene_qc
            payload = None
            payload_candidates = sorted((out / "render").glob("payload_*.json"))
            if payload_candidates:
                try:
                    payload = json.loads(payload_candidates[0].read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload = None
            qc = scene_qc(
                report,
                payload=payload,
                srt_path=(report.get("subtitles") or "") or None,
                handoff_windows=self._handoff_windows(),
            )
            report["qc"] = qc
            if qc["status"] == "FAILED":
                report["status"] = "FAILED"
                report["error"] = (
                    "QC-гейты провалены: " + ", ".join(qc["fails"])
                )
            elif qc["status"] == QC_PARTIAL:
                report["status"] = "PARTIAL"
        (out / "scene_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def _slug(self) -> str:
        base = self.title or "blocky_scene"
        slug = "".join(c if c.isalnum() else "_" for c in base).strip("_").lower()
        return ("scene_" + (slug or "blocky_scene"))[:60]


def load_and_render(scene_path: str | Path, out_dir: str | Path, *, timeout: int = 3600) -> dict[str, Any]:
    """One-call entry: load a scene JSON and render it end-to-end."""
    return BlockyScene.load(scene_path).render(out_dir, timeout=timeout)
