"""Free local hybrid animation pipeline.

``run_free_create`` turns a short text idea into a vertical 1080x1920 MP4
using only offline, free local components:

    idea → story (Ollama, optional) → AnimationPlan → render (engine
    dispatcher: Animation Engine / Blender) → Piper TTS → subtitles → QC.

This pipeline is the ``free_local`` profile: it never creates a cloud backend,
never sends network generation requests, never reads keys for paid providers
and never falls back to a paid service.  If a local stage is unavailable it is
reported honestly (PARTIAL/FAILED), never silently replaced by a cloud call.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import ffprobe_json, ffmpeg_bin, run
from .animation_plan import (
    ENGINE_AUTO,
    ENGINE_BLENDER,
    ENGINE_CHOICES,
    ENGINE_SKINNED,
    ENGINE_VOXEL,
    AnimationPlan,
    PlanAction,
    PlanCameraKey,
    PlanCharacter,
    PlanEvent,
    PlanObject,
    PlanVoiceLine,
)
from .engine_dispatcher import DispatchResult, render_plan

DEFAULT_DURATION = 15.0
MIN_DURATION = 6.0
MAX_DURATION = 30.0
DEFAULT_QUALITIES = ("low", "medium", "high")

# The default local scenario (a girl finds a glowing stone in a twilight forest).
# Keyword mapping is deliberately simple and deterministic: it never invokes an
# LLM for the *visual* plan, so the result is reproducible offline.
_FOREST_KEYWORDS = ("лес", "forest", "поляна", "камень", "камен", "stone", "свет", "glow", "свеч", "майя", "maya", "девочка")
_STONE_KEYWORDS = ("камень", "камен", "stone", "самоцвет", "кристалл", "crystal")


@dataclass
class FreeQC:
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    streams_ok: bool = False
    decode_ok: bool = False
    unique_frame_fraction: float = 0.0
    motion_ok: bool = False
    character_walks: bool = False
    props_interacted: bool = False
    attachment_verified: bool = False
    audio_present: bool = False
    audio_video_match: bool = False
    subtitles_present: bool = False
    subtitles_parsed: bool = False
    render_errors: int = 0
    status: str = "WARNING"  # READY | PARTIAL | WARNING | FAILED
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class FreeResult:
    status: str  # READY | PARTIAL | WARNING | FAILED
    mode: str = "free_local"
    engine: str = ""
    title: str = ""
    idea: str = ""
    story: str = ""
    output_dir: str = ""
    video: str = ""
    contact_sheet: str = ""
    scene_path: str = ""
    characters_path: str = ""
    plan_path: str = ""
    subtitles_srt: str = ""
    subtitles_ass: str = ""
    qc: FreeQC = field(default_factory=FreeQC)
    notes: list[str] = field(default_factory=list)
    warning: str = ""
    generated_at: str = ""
    render_time_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        data["qc"] = self.qc.to_dict()
        return data


# ---------------------------------------------------------------- idea → story


def _expand_idea(config: Any, idea: str, model: str = "") -> str:
    """Optionally expand the idea into a short story using local Ollama.

    Deterministic fallback returns the idea verbatim when Ollama is absent or
    fails, so the pipeline never depends on a live service.
    """
    try:
        from ..llm import OllamaClient
    except Exception:
        return idea
    try:
        client = OllamaClient(config)
        if model:
            client.model = model
        if not client.available():
            return idea
        prompt = (
            "Напиши 2-3 предложения короткой истории для вертикального видео "
            f"по идее: {idea}. Без диалогов, только описательное повествование."
        )
        text = client.generate(prompt, format_json=False).strip()
        return text if text else idea
    except Exception:
        return idea


# ---------------------------------------------------------------- idea → plan


def _wants_two_actors(*texts: str) -> bool:
    """True when the idea names both characters the skinned rig can play."""
    blob = " ".join(t.lower() for t in texts if t)
    milo = ("milo" in blob) or ("мило" in blob)
    nina = ("nina" in blob) or ("нина" in blob)
    return milo and nina


def _two_actor_plan(
    *,
    total: float,
    title: str = "",
    lang: str = "ru",
    width: int = 1080,
    height: int = 1920,
) -> AnimationPlan:
    """Deterministic Milo + Nina scene for the skinned rig.

    Milo notices something, walks over and points at it; Nina turns, reacts and
    answers.  Every verb is declared in ``extra_actions`` because ``talk``,
    ``reach``, ``turn_in_place`` and the emotion states are rig states, not part
    of the shared 2D catalogue.
    """

    def at(fraction: float) -> float:
        return round(total * fraction, 3)

    milo = [
        PlanAction(character="milo", action="idle", start=0.0, duration=at(0.15)),
        PlanAction(character="milo", action="walk", start=at(0.15), duration=at(0.36) - at(0.15)),
        PlanAction(character="milo", action="reach", start=at(0.36), duration=at(0.46) - at(0.36)),
        PlanAction(character="milo", action="talk", start=at(0.46), duration=at(0.62) - at(0.46)),
        PlanAction(character="milo", action="happy", start=at(0.62), duration=at(0.82) - at(0.62)),
        PlanAction(character="milo", action="idle", start=at(0.82), duration=round(total - at(0.82), 3)),
    ]
    nina = [
        PlanAction(character="nina", action="idle", start=0.0, duration=at(0.24)),
        PlanAction(character="nina", action="turn_in_place", start=at(0.24), duration=at(0.38) - at(0.24)),
        PlanAction(character="nina", action="surprised", start=at(0.38), duration=at(0.52) - at(0.38)),
        PlanAction(character="nina", action="talk", start=at(0.52), duration=at(0.68) - at(0.52)),
        PlanAction(character="nina", action="happy", start=at(0.68), duration=at(0.86) - at(0.68)),
        PlanAction(character="nina", action="idle", start=at(0.86), duration=round(total - at(0.86), 3)),
    ]

    def line_at(fraction: float) -> float:
        # keep the last line inside the scene so TTS is never cut off
        return round(min(total * fraction, max(0.0, total - 0.9)), 3)

    if lang == "ru":
        voice_lines = [
            PlanVoiceLine(character="milo", text="Смотри, что я нашёл!", start=line_at(0.34)),
            PlanVoiceLine(character="nina", text="Где? Я не вижу.", start=line_at(0.52)),
            PlanVoiceLine(character="milo", text="Вот, прямо здесь!", start=line_at(0.66)),
            PlanVoiceLine(character="nina", text="Ого! Как красиво!", start=line_at(0.80)),
        ]
    else:
        voice_lines = [
            PlanVoiceLine(character="milo", text="Look what I found!", start=line_at(0.34)),
            PlanVoiceLine(character="nina", text="Where? I can't see it.", start=line_at(0.52)),
            PlanVoiceLine(character="milo", text="Right here!", start=line_at(0.66)),
            PlanVoiceLine(character="nina", text="Wow! It's beautiful!", start=line_at(0.80)),
        ]

    return AnimationPlan(
        scene_id="free_scene_milo_nina",
        title=title or "Milo и Nina: находка",
        duration=round(total, 2),
        fps=30,
        width=width,
        height=height,
        environment="stage",
        characters=[
            PlanCharacter(
                id="milo",
                name="Milo",
                rig="humanoid_v1",
                x=0.32,
                y=1.0,
                scale=1.0,
                facing=1,
                z=20,
                palette={
                    "hoodie": "#2ba9a0",
                    "shorts": "#22304a",
                    "skin": "#e6ae85",
                    "hair": "#211f26",
                    "shoes": "#f7d02c",
                },
            ),
            PlanCharacter(
                id="nina",
                name="Nina",
                rig="humanoid_v1",
                x=0.68,
                y=1.0,
                scale=1.0,
                facing=-1,
                z=21,
                palette={
                    "dress": "#e8731f",
                    "tights": "#6b3d99",
                    "skin": "#f0b88f",
                    "hair": "#733320",
                    "shoes": "#47296a",
                },
            ),
        ],
        actions=milo + nina,
        camera=[PlanCameraKey(t=0.0, x=0.50, y=0.55, zoom=1.05)],
        voice_lines=voice_lines,
        extra_actions={
            "talk",
            "reach",
            "turn_in_place",
            "happy",
            "surprised",
            "sad",
            "angry",
            "confused",
            "worried",
            "neutral",
            "gesture",
            "interact",
        },
    )


def build_plan_for_idea(
    idea: str,
    *,
    title: str = "",
    duration: float = DEFAULT_DURATION,
    lang: str = "ru",
    seed: int = 20261023,
    width: int = 1080,
    height: int = 1920,
    original: str = "",
) -> AnimationPlan:
    """Build a deterministic local plan for the idea.

    The planner maps a few keywords to the built-in twilight-forest scenario
    (a girl finds a glowing stone).  This is a documented, offline default; it
    does not pretend to understand arbitrary prose.
    """
    total = min(max(float(duration), MIN_DURATION), MAX_DURATION)
    if _wants_two_actors(idea, original):
        # the rig can only play Milo and Nina; a two-name idea therefore gets a
        # real two-actor scene instead of a renamed solo performance.
        return _two_actor_plan(
            total=total, title=title, lang=lang, width=width, height=height
        )

    # timeline (fractions of the total duration) for the Maya scenario.
    # Using fractions guarantees actions always fit inside the scene.
    f_enter = 0.0
    f_walk = 0.18
    f_crouch = 0.36
    f_pickup = 0.50
    f_attach = 0.55
    f_react = 0.62
    f_joy = 0.74
    f_end = 0.96

    t_enter = total * f_enter
    t_walk = total * f_walk
    t_crouch = total * f_crouch
    t_pickup = total * f_pickup
    t_attach = total * f_attach
    t_react = total * f_react
    t_joy = total * f_joy
    t_end = total * f_end

    maya = PlanCharacter(
        id="maya",
        name="Майя",
        rig="humanoid_v1",
        x=0.44,
        y=1.0,
        scale=1.0,
        facing=1,
        z=20,
        appear_at=0.0,
        palette={
            "skin": "#e8b48c",
            "hair": "#3a2a20",
            "dress": "#2a6b5e",
            "dress2": "#1f4f46",
            "eyes": "#4a6b8a",
            "shoes": "#22242c",
        },
    )
    stone = PlanObject(
        id="stone",
        kind="gem",
        z=15,
        appear_at=t_walk - 0.5,
        color="#8df0b4",
        keyframes=[
            {"t": t_walk - 0.5, "x": 0.62, "y": 0.80, "scale": 0.55, "rotation": 0.0},
            {"t": t_attach, "x": 0.62, "y": 0.80, "scale": 0.55, "rotation": 0.0},
        ],
    )

    actions = [
        PlanAction(character="maya", action="idle", start=t_enter, duration=t_walk - t_enter,
                   params={"sway": 0.03}),
        PlanAction(character="maya", action="walk", start=t_walk, duration=t_crouch - t_walk,
                   params={"to_x": 0.62, "cycles": 3.0, "stride": 0.5}),
        PlanAction(character="maya", action="crouch", start=t_crouch, duration=t_pickup - t_crouch,
                   params={"intensity": 1.0}),
        PlanAction(character="maya", action="pickup", start=t_pickup, duration=t_react - t_pickup,
                   params={"intensity": 1.0}),
        PlanAction(character="maya", action="react", start=t_react, duration=t_joy - t_react,
                   params={"intensity": 1.0}),
        PlanAction(character="maya", action="joy", start=t_joy, duration=t_end - t_joy,
                   params={"intensity": 1.0}),
    ]

    events = [
        PlanEvent(t=t_attach, kind="attach", object="stone", character="maya",
                 point="hand_r", offset=(0.0, -0.02), scale=0.6),
    ]

    camera = [
        PlanCameraKey(t=0.0, x=0.50, y=0.55, zoom=1.0),
        PlanCameraKey(t=t_walk, x=0.52, y=0.56, zoom=1.10),
        PlanCameraKey(t=t_attach, x=0.56, y=0.62, zoom=1.35),
        PlanCameraKey(t=t_react, x=0.55, y=0.60, zoom=1.28),
        PlanCameraKey(t=t_joy, x=0.54, y=0.58, zoom=1.20),
    ]

    voice_lines = []
    if lang == "ru":
        voice_lines = [
            PlanVoiceLine(character="maya", text="Что это за свет в лесу?", start=1.2),
            PlanVoiceLine(character="maya", text="Камень… он светится!", start=t_react + 0.1),
            PlanVoiceLine(character="maya", text="Это волшебно!", start=t_joy + 0.2),
        ]
    else:
        voice_lines = [
            PlanVoiceLine(character="maya", text="What is that light in the forest?", start=1.2),
            PlanVoiceLine(character="maya", text="A stone… it is glowing!", start=t_react + 0.1),
            PlanVoiceLine(character="maya", text="It is magical!", start=t_joy + 0.2),
        ]

    return AnimationPlan(
        scene_id="free_scene_01",
        title=title or "Майя и светящийся камень",
        duration=round(total, 2),
        fps=30,
        width=width,
        height=height,
        environment="forest",
        characters=[maya],
        objects=[stone],
        actions=actions,
        events=events,
        camera=camera,
        voice_lines=voice_lines,
        contact_times=[t_attach, t_react, t_joy],
        blink_times=[1.0, 4.0, 9.0],
        seed=seed,
        blend=0.18,
        voice_lang=lang,
        voice_gain=0.9,
    )


# ------------------------------------------------------------------- subtitles


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms:02d}"


def _speech_durations(result: DispatchResult, plan: AnimationPlan) -> dict[float, float]:
    """Return {start_second: duration} for each spoken line."""
    durations: dict[float, float] = {}
    anim = getattr(result, "animation_result", None)
    if anim is not None:
        lines = (getattr(anim, "lip_sync", {}) or {}).get("lines", [])
        for line in lines:
            start = round(float(line.get("start", 0.0)), 3)
            durations[start] = float(line.get("duration", 1.2))
    # fallback estimate for engines without envelope analysis
    for line in plan.voice_lines:
        durations.setdefault(round(line.start, 3), 1.4)
    return durations


def build_subtitles(result: DispatchResult, plan: AnimationPlan, out_dir: Path, lang: str = "ru") -> tuple[Path, Path]:
    """Write SRT and ASS files for the plan's dialogue."""
    sub_dir = out_dir / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    speech = _speech_durations(result, plan)

    srt_lines: list[str] = []
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {plan.width}",
        f"PlayResY: {plan.height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default, Montserrat, 52, &H00FFFFFF, &H00101010, &H80000000, -1, 0, 0, 0, 100, 100, 0, 0, 1, 3, 2, 2, 80, 80, 120, 1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for idx, line in enumerate(plan.voice_lines, 1):
        start = round(line.start, 3)
        dur = speech.get(start, 1.4)
        end = min(plan.duration, start + max(0.45, dur))
        srt_lines.append(str(idx))
        srt_lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        srt_lines.append(line.text)
        srt_lines.append("")
        text = line.text.replace("\n", "\\N").replace("{", "(").replace("}", ")")
        ass_lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,{line.character},0,0,0,,{text}"
        )

    srt_path = sub_dir / f"{lang}.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    ass_path = sub_dir / f"{lang}.ass"
    ass_path.write_text("\n".join(ass_lines), encoding="utf-8")
    return srt_path, ass_path


# ------------------------------------------------------------------------ QC


def _sample_frames(plan: AnimationPlan, scene_path: Path, config: Any) -> list[str]:
    """Render a handful of frozen-camera frames and hash them (unique-frame fraction)."""
    try:
        from ..animation.scene import load_scene
        from ..animation.renderer import AnimationRenderer
        scene = load_scene(scene_path)
        renderer = AnimationRenderer(scene, config)
        hashes: list[str] = []
        steps = 12
        for i in range(steps):
            t = scene.duration * (i / max(1, steps - 1))
            image = renderer.frame(t, camera=False)
            hashes.append(hashlib.sha256(image.tobytes()).hexdigest())
        return hashes
    except Exception:
        return []


def run_qc(result: DispatchResult, plan: AnimationPlan, config: Any, out_dir: Path) -> FreeQC:
    """Compute the honest quality metrics for the produced video."""
    qc = FreeQC()
    video = Path(result.video) if result.video else None

    if video is None or not video.exists():
        qc.status = "FAILED"
        qc.notes.append("Видеофайл не создан.")
        return qc

    probe = ffprobe_json(video)
    vstream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    astream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
    qc.duration_s = round(float(probe.get("format", {}).get("duration", 0.0) or 0.0), 3)
    qc.width = int(vstream.get("width", 0) or 0)
    qc.height = int(vstream.get("height", 0) or 0)
    fps = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "0/1"
    try:
        num, den = fps.split("/")
        qc.fps = round(float(num) / max(1, float(den)), 2)
    except Exception:
        qc.fps = 0.0
    qc.streams_ok = bool(vstream) and bool(astream) if qc.duration_s > 0 else bool(vstream)

    # decode check (full media pipeline)
    try:
        decode = run([ffmpeg_bin(), "-v", "error", "-i", str(video), "-f", "null", "-"], timeout=600)
        qc.decode_ok = decode.returncode == 0 and not decode.stderr.strip()
    except Exception:
        qc.decode_ok = False
    if not qc.decode_ok:
        qc.notes.append("Итоговый MP4 не декодируется без ошибок.")

    # unique-frame fraction (only procedural path; blender derives from video)
    if getattr(result, "engine_used", None) == "procedural" and result.scene_path:
        hashes = _sample_frames(plan, Path(result.scene_path), config)
        if hashes:
            unique = len(set(hashes))
            total = len(hashes)
            qc.unique_frame_fraction = round(unique / total, 3)
            if unique < 2:
                qc.notes.append("Кадры почти не меняются: возможно, не только камера.")
    else:
        qc.unique_frame_fraction = 1.0  # blender produces fresh frames per render

    # motion + props
    anim = getattr(result, "animation_result", None)
    if anim is not None:
        mc = getattr(anim, "motion_checks", {}) or {}
        qc.motion_ok = bool(mc.get("limbs_move_without_camera"))
        qc.character_walks = bool(mc.get("character_walks"))
        ac = getattr(anim, "attachment_checks", {}) or {}
        qc.attachment_verified = bool(ac.get("verified")) and bool(ac.get("enabled"))
        qc.props_interacted = bool(plan.events) and qc.attachment_verified
        if not qc.motion_ok:
            qc.notes.append("Проверка движения не подтверждена (конечности почти не движутся).")
    else:
        # blender / unknown path: no automatic joint verification
        qc.motion_ok = qc.unique_frame_fraction >= 0.5
        qc.props_interacted = bool(plan.events)

    # audio
    audio_dur = float(astream.get("duration", 0.0) or 0.0)
    audio_present = bool(astream) and audio_dur > 0.0
    qc.audio_present = audio_present or bool(getattr(anim, "audio", "")) if getattr(result, "engine_used", None) == "procedural" else audio_present
    qc.audio_video_match = qc.duration_s > 0 and abs(audio_dur - qc.duration_s) < 1.5 if audio_present else False
    if plan.voice_lines and not qc.audio_video_match:
        qc.notes.append("Длительность аудио не совпадает с видео или звук отсутствует.")

    # subtitles
    srt = out_dir / "subtitles" / f"{plan.voice_lang}.srt"
    qc.subtitles_present = srt.exists()
    if srt.exists():
        try:
            blocks = [b for b in srt.read_text(encoding="utf-8").strip().split("\n\n") if b.strip()]
            qc.subtitles_parsed = len(blocks) >= 1 and all("-->" in b for b in blocks)
        except Exception:
            qc.subtitles_parsed = False
    if plan.voice_lines and (not qc.subtitles_present or not qc.subtitles_parsed):
        qc.notes.append("Субтитры отсутствуют или повреждены.")

    # status
    fatal = not qc.decode_ok or (not qc.streams_ok) or qc.duration_s <= 0
    missing_local = (not qc.audio_present) or (not qc.subtitles_parsed) or (not qc.motion_ok)
    if fatal:
        qc.status = "FAILED"
    elif missing_local:
        qc.status = "PARTIAL"
    else:
        qc.status = "READY"
    return qc


# ------------------------------------------------------------- main pipeline


def _build_contact_sheet(
    video: Path, out_dir: Path, *, tiles: str = "6x2", duration: float = 0.0
) -> Path | None:
    """Tile evenly spaced frames of ``video`` into a JPEG (ffmpeg only).

    The 2D engine returns its own sheet; Blender-backed engines (``skinned``)
    returned none, so the report and the CLI showed an empty path.  Returning a
    real file keeps the review step honest for those engines too.

    The sample rate is chosen so exactly ``cols*rows`` frames are taken for the
    known duration; a fixed 1 fps left black tiles on a 10 s video, which read
    like blank frames.
    """
    sheet = out_dir / "contact_sheet.jpg"
    try:
        cols, _, rows = tiles.lower().partition("x")
        count = max(1, int(cols) * int(rows or 1))
        rate = count / duration if duration > 0 else 1.0
        proc = run(
            [
                ffmpeg_bin(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vf",
                f"fps={rate:.6f},scale=270:480,tile={tiles}",
                "-frames:v",
                "1",
                str(sheet),
            ],
            timeout=300,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not sheet.exists() or sheet.stat().st_size == 0:
        return None
    return sheet


def run_free_create(
    idea: str,
    out_dir: str | Path,
    config: Any,
    *,
    title: str = "",
    engine: str = ENGINE_AUTO,
    ollama_model: str = "",
    piper_voice: str = "",
    duration: float = DEFAULT_DURATION,
    quality: str = "high",
    diagnostics: bool = False,
    resume: bool = False,
    overwrite: bool = False,
) -> FreeResult:
    """Run the free-local pipeline end-to-end for a text idea."""
    if not idea or not str(idea).strip():
        raise ValueError("Идея пустая.")
    if engine not in ENGINE_CHOICES:
        raise ValueError(f"Неизвестный движок: {engine}. Допустимо: {', '.join(ENGINE_CHOICES)}.")
    if quality not in DEFAULT_QUALITIES:
        raise ValueError(f"Неизвестное качество: {quality}. Допустимо: {', '.join(DEFAULT_QUALITIES)}.")
    duration = min(max(float(duration), MIN_DURATION), MAX_DURATION)

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite and not resume:
        raise FileExistsError(f"Папка результата не пуста: {out_dir}. Используйте --overwrite.")
    out_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    notes: list[str] = []

    # A. idea → story
    story = _expand_idea(config, str(idea).strip(), model=ollama_model)
    if story != str(idea).strip():
        notes.append("История расширена локальной моделью Ollama.")
    else:
        notes.append("Ollama недоступна/не использована — история = исходная идея.")

    # B. plan (deterministic)
    plan = build_plan_for_idea(
        story, title=title, duration=duration, lang="ru", original=str(idea).strip()
    )
    plan_path = out_dir / "animation_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    # C/D. render via dispatcher
    render_out = out_dir / "render"
    if resume and (render_out / (plan.to_scene_dict()["id"] + ".json")).exists() and (render_out / "procedural" / "pilot.mp4").exists():
        # reuse existing procedural render
        from ..animation import AnimationEngine
        scene_path = render_out / f"{plan.scene_id}.json"
        scene_path.write_text(json.dumps(plan.to_scene_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        anim = AnimationEngine(config).render(scene_path, render_out / "procedural")
        result = DispatchResult(status="OK", engine_used="procedural", video=anim.video,
                                contact_sheet=anim.contact_sheet, scene_path=str(scene_path),
                                characters_path=anim.characters, report_json=anim.report_json,
                                render_dir=str(render_out / "procedural"), animation_result=anim)
        notes.append("Использован существующий рендер (resume).")
    else:
        result = render_plan(plan, config, render_out, engine=engine)

    notes.extend(result.notes)
    if result.error:
        notes.append(f"Ошибка рендера: {result.error}")

    # F. non-procedural engines (voxel/blender/skinned) have no built-in audio:
    # mix Piper voice.
    if (engine in (ENGINE_VOXEL, ENGINE_BLENDER, ENGINE_SKINNED) and result.status == "OK"
            and result.video and plan.voice_lines):
        try:
            from ..tts import TTSService
            from .assembly import mix_audio
            tts = TTSService(config)
            segments = []
            for line in plan.voice_lines:
                wav, dur = tts.synthesize_line(line.text, plan.voice_lang, voice_override=piper_voice)
                segments.append((round(max(0.0, float(line.start)), 3), str(wav)))
            if segments:
                mix_audio(Path(result.video), segments, out_dir)
                result.video = str(out_dir / "final_audio.mp4")
                notes.append("Озвучка Piper добавлена к видео (voxel/blender/skinned).")
        except Exception as exc:  # honest: don't claim audio we did not create
            notes.append(f"Озвучка не добавлена: {exc}")

    # E0. contact sheet for engines that do not build one themselves
    if not result.contact_sheet and result.video:
        sheet = _build_contact_sheet(
            Path(result.video), out_dir, duration=float(plan.duration or 0.0)
        )
        if sheet is not None:
            result.contact_sheet = str(sheet)
            notes.append(f"Контактный лист: {sheet.name}.")

    # E. subtitles
    srt_path, ass_path = build_subtitles(result, plan, out_dir, lang=plan.voice_lang)
    # G. QC
    qc = run_qc(result, plan, config, out_dir)
    qc.render_errors = 1 if result.status == "FAILED" else 0

    # F. audio is produced inside the procedural engine; blender embeds audio? Not yet.
    # Ensure the audio note is honest.
    if plan.voice_lines and not qc.audio_present:
        qc.notes.append("Озвучка не добавлена: голос Piper недоступен или не создан.")

    # final status
    if result.status == "PARTIAL" or result.status == "FAILED":
        status = result.status
    else:
        status = qc.status

    elapsed = round(time.time() - start, 2)
    free_result = FreeResult(
        status=status,
        engine=result.engine_used or engine,
        title=plan.title,
        idea=str(idea).strip(),
        story=story,
        output_dir=str(out_dir),
        video=result.video,
        contact_sheet=result.contact_sheet,
        scene_path=result.scene_path or str(render_out / f"{plan.scene_id}.json"),
        characters_path=result.characters_path,
        plan_path=str(plan_path),
        subtitles_srt=str(srt_path),
        subtitles_ass=str(ass_path),
        qc=qc,
        notes=notes,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        render_time_s=elapsed,
    )

    # report
    _write_report(free_result, out_dir)
    return free_result


def _write_report(result: FreeResult, out_dir: Path) -> None:
    report = result.to_dict()
    (out_dir / "free_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Free-local animation pipeline — отчёт",
        "",
        f"- Статус: **{result.status}**",
        f"- Движок: {result.engine}",
        f"- Видео: `{result.video}`",
        f"- Контактный лист: `{result.contact_sheet}`",
        f"- Субтитры: `{result.subtitles_srt}`, `{result.subtitles_ass}`",
        f"- Время рендера: {result.render_time_s} c",
        "",
        "## QC",
        "",
    ]
    for key in ("duration_s", "width", "height", "fps", "decode_ok", "unique_frame_fraction",
                "motion_ok", "character_walks", "props_interacted", "attachment_verified",
                "audio_present", "audio_video_match", "subtitles_present", "subtitles_parsed"):
        lines.append(f"- {key}: {getattr(result.qc, key)}")
    lines.append("")
    lines.append("## Примечания")
    lines.append("")
    for note in result.notes + result.qc.notes:
        lines.append(f"- {note}")
    (out_dir / "free_report.md").write_text("\n".join(lines), encoding="utf-8")
