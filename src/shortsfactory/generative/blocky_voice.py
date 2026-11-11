"""Voice engine for blocky cartoons: profiles, per-line WAVs, timing and the mix.

The engine voices a scene **before** it is rendered.  Every spoken line becomes
its own WAV file under ``<film>/audio/voices/<character>/line_00N.wav``; the
measured duration of that file replaces the duration authored in the scene JSON,
and the same numbers then drive the subtitles, the mouth animation and the final
audio mix.  Nothing here needs a GPU: Piper runs on the CPU, and the only other
tool is ffmpeg.

Voices are local Piper models.  ``VoiceProfile.pitch`` shifts the formants with
ffmpeg (``asetrate`` + ``aresample`` + ``atempo`` keeps the duration), so two
characters differ in timbre even when they share a base model.  These are
synthetic voices; no real person's voice is cloned.
"""

from __future__ import annotations

import json
import shutil
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ..tts import TTSError, TTSService
from ..util import ffmpeg_bin, run

#: Mixing format.  Piper returns 22.05 kHz mono; everything is resampled once.
AUDIO_RATE = 48000
DEFAULT_VOICE_GAIN = 1.0
DEFAULT_SFX_GAIN = 0.45
DEFAULT_AMBIENCE_GAIN = 0.16
#: Hard ceiling of the mix.  Speech stays below it, so it never clips.
PEAK_LIMIT = 0.95
#: Lip-sync resolution: one loudness value per 1/50 s.  It matches the mouth
#: animation rate (30 fps) with headroom and keeps the payload small.
ENVELOPE_HZ = 50.0


# --------------------------------------------------------------------- profiles


@dataclass(frozen=True)
class VoiceProfile:
    """One character voice.

    ``speed`` multiplies the language's base ``length_scale`` (so 1.0 means "as
    configured", 1.05 is a little slower); ``pitch`` is a semitone shift;
    ``pronunciation`` rewrites words before synthesis.
    """

    voice_id: str
    lang: str
    voice: str
    speed: float = 1.0
    pitch: float = 0.0
    speaker: int | None = None
    volume: float = DEFAULT_VOICE_GAIN
    provider: str = "piper"
    pronunciation: dict[str, str] = field(default_factory=dict)

    def speak(self, text: str) -> str:
        """Apply the pronunciation table to one line."""
        spoken = str(text or "").strip()
        for word, replacement in self.pronunciation.items():
            if word and word.lower() in spoken.lower():
                spoken = _replace_word(spoken, word, replacement)
        return spoken

    def to_dict(self) -> dict[str, Any]:
        data = {
            "voice_id": self.voice_id,
            "lang": self.lang,
            "voice": self.voice,
            "speed": self.speed,
            "pitch": self.pitch,
            "speaker": self.speaker,
            "volume": self.volume,
            "provider": self.provider,
        }
        if self.pronunciation:
            data["pronunciation"] = dict(self.pronunciation)
        return data


def _replace_word(text: str, word: str, replacement: str) -> str:
    """Case-insensitive whole-word replacement that keeps the rest intact."""
    lowered = text.lower()
    needle = word.lower()
    out: list[str] = []
    start = 0
    while True:
        index = lowered.find(needle, start)
        if index < 0:
            out.append(text[start:])
            break
        before = lowered[index - 1] if index > 0 else " "
        after_index = index + len(needle)
        after = lowered[after_index] if after_index < len(lowered) else " "
        boundary = not (before.isalnum() or after.isalnum())
        out.append(text[start:index])
        out.append(replacement if boundary else text[index:after_index])
        start = after_index
    return "".join(out)


#: Steve is the calm one (male, slightly brighter), Alex the lively one
#: (female, a touch slower and quieter).  RU voices are two different models;
#: the pitch shift only refines Steve.  EN reuses the two bundled models with
#: different speakers of the multi-speaker LibriTTS voice.
VOICE_PROFILES: dict[tuple[str, str], VoiceProfile] = {
    ("steve", "ru"): VoiceProfile(
        voice_id="steve-ru",
        lang="ru",
        voice="ru_RU-dmitri-medium.onnx",
        speed=1.02,
        pitch=1.5,
        volume=1.0,
    ),
    ("alex", "ru"): VoiceProfile(
        voice_id="alex-ru",
        lang="ru",
        voice="ru_RU-irina-medium.onnx",
        speed=0.98,
        volume=0.95,
        pronunciation={"что": "што", "чтобы": "штобы"},
    ),
    ("steve", "en"): VoiceProfile(
        voice_id="steve-en",
        lang="en",
        voice="en_US-ryan-high.onnx",
        speed=1.0,
        volume=1.0,
    ),
    ("alex", "en"): VoiceProfile(
        voice_id="alex-en",
        lang="en",
        voice="en_US-libritts_r-medium.onnx",
        speed=1.0,
        speaker=12,
        volume=0.95,
    ),
}


def resolve_profile(
    character: str, lang: str = "ru", override: dict[str, Any] | None = None
) -> VoiceProfile:
    """Find the profile for one character, optionally patched by the scene.

    Raises ``ValueError`` for an unknown character or language instead of
    silently falling back to the default narrator.
    """
    who = str(character or "").strip().lower()
    profile = VOICE_PROFILES.get((who, lang))
    if profile is None:
        available = ", ".join(f"{c}/{l}" for c, l in sorted(VOICE_PROFILES))
        raise ValueError(
            f"Нет голосового профиля для «{who}» на языке «{lang}». "
            f"Доступно: {available}."
        )
    if override:
        profile = replace(profile, **override)
    return profile


# ------------------------------------------------------------------------ takes


@dataclass
class VoiceTake:
    """One voiced line: the WAV on disk and the timing taken from it."""

    character: str
    text: str
    start: float
    duration: float
    wav: Path
    voice_id: str
    profile: VoiceProfile
    cached: bool = False
    #: loudness curve sampled from the real WAV (see :func:`speech_envelope`)
    envelope: list[float] = field(default_factory=list)
    envelope_hz: float = ENVELOPE_HZ
    #: spoken spans inside the take, relative to the WAV start, in seconds
    segments: list[list[float]] = field(default_factory=list)

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def speech_seconds(self) -> float:
        """Total time the take actually contains speech."""
        return round(sum(max(0.0, b - a) for a, b in self.segments), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "character": self.character,
            "text": self.text,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "wav": str(self.wav),
            "voice_id": self.voice_id,
            "pitch": self.profile.pitch,
            "speed": self.profile.speed,
            "cached": self.cached,
            "envelope_hz": self.envelope_hz,
            "envelope_frames": len(self.envelope),
            "segments": [list(span) for span in self.segments],
            "speech_s": self.speech_seconds,
        }


def _wav_meta(path: Path) -> tuple[int, int, int]:
    """(channels, rate, frames) of a PCM WAV file."""
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnchannels(), handle.getframerate(), handle.getnframes()
    except (wave.Error, EOFError) as exc:
        raise TTSError(f"Не читается WAV реплики: {path} ({exc})") from exc


def wav_duration(path: Path) -> float:
    """Real length of a WAV file in seconds."""
    _channels, rate, frames = _wav_meta(path)
    if rate <= 0:
        raise TTSError(f"Некорректная частота дискретизации: {path}")
    return frames / float(rate)


# --------------------------------------------------------------------- lip sync


def read_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV into a float32 mono array in ``[-1, 1]``."""
    target = Path(path)
    try:
        with wave.open(str(target), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise TTSError(f"Не читается WAV реплики: {target} ({exc})") from exc
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise TTSError(f"Неподдерживаемая разрядность WAV ({width * 8} бит): {target}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, int(rate)


def speech_envelope(
    path: str | Path,
    *,
    hz: float = ENVELOPE_HZ,
    attack: float = 0.55,
    release: float = 0.30,
) -> list[float]:
    """Loudness of a WAV over time, one value per ``1/hz`` second, in ``[0, 1]``.

    The curve is RMS per window, normalised by its 95th percentile (so one loud
    syllable does not flatten the rest) and smoothed with a fast attack and a
    slower release, which is what a jaw actually does.  A silent file returns
    zeros and therefore a closed mouth.
    """
    samples, rate = read_wav_mono(path)
    if samples.size == 0 or rate <= 0:
        return []
    step = max(1, int(round(rate / max(1.0, float(hz)))))
    frames: list[float] = []
    for start in range(0, samples.size, step):
        chunk = samples[start:start + step]
        frames.append(float(np.sqrt(np.mean(np.square(chunk.astype(np.float64))))))
    curve = np.asarray(frames, dtype=np.float64)
    reference = float(np.percentile(curve, 95)) if curve.size else 0.0
    if reference <= 1e-7:
        return [0.0] * int(curve.size)
    curve = np.clip(curve / reference, 0.0, 1.0)
    smoothed = np.empty_like(curve)
    level = 0.0
    for index, value in enumerate(curve):
        coeff = attack if value > level else release
        level += coeff * (value - level)
        smoothed[index] = level
    return [round(float(value), 4) for value in smoothed]


def speech_segments(
    envelope: Sequence[float],
    *,
    hz: float = ENVELOPE_HZ,
    threshold: float = 0.12,
    min_silence: float = 0.12,
    min_speech: float = 0.08,
    pad: float = 0.04,
) -> list[list[float]]:
    """Split an envelope into spoken ``[[start, end], ...]`` spans, in seconds.

    Spans shorter than ``min_speech`` are dropped as clicks; gaps shorter than
    ``min_silence`` are bridged (a stop consonant must not close the mouth in
    the middle of a word).  The whole file is silent → an empty list, and the
    caller keeps the mouth shut instead of guessing.
    """
    values = [max(0.0, float(v)) for v in envelope or []]
    if not values:
        return []
    step = 1.0 / max(1.0, float(hz))
    runs: list[list[float]] = []
    inside = False
    begin = 0.0
    for index, value in enumerate(values + [0.0]):
        loud = value >= threshold
        if loud and not inside:
            inside, begin = True, index * step
        elif not loud and inside:
            inside = False
            runs.append([begin, index * step])
    merged: list[list[float]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < min_silence:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    limit = len(values) * step
    spans = []
    for begin, end in merged:
        begin = max(0.0, begin - pad)
        end = min(limit, end + pad)
        if end - begin >= min_speech:
            spans.append([round(begin, 3), round(end, 3)])
    return spans


def _shift_pitch(source: Path, dest: Path, semitones: float) -> None:
    """Re-render ``source`` into ``dest`` with shifted formants, same length."""
    _channels, rate, _frames = _wav_meta(source)
    ratio = 2.0 ** (float(semitones) / 12.0)
    filters = (
        f"asetrate={rate * ratio:.3f},aresample={rate},"
        f"atempo={1.0 / ratio:.6f},volume=1.0"
    )
    proc = run(
        [
            ffmpeg_bin(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            filters,
            "-ac",
            "1",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        timeout=180,
        check=False,
    )
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size <= 44:
        raise TTSError(
            f"ffmpeg не смог изменить тембр реплики {source.name}: "
            f"{(proc.stderr or '').strip()[:200]}"
        )


def synthesize_dialogue(
    dialogue: Sequence[dict[str, Any]],
    out_dir: str | Path,
    config: Any,
    lang: str = "ru",
    *,
    force: bool = False,
    service: TTSService | None = None,
) -> list[VoiceTake]:
    """Voice every line of ``dialogue`` and return the takes in scene order.

    Files land in ``<out_dir>/audio/voices/<character>/line_NNN.wav``.  An
    existing file is reused unless ``force`` is set, which is the "regenerate one
    line" switch the CLI exposes.  The scene's own ``duration`` is *not* used as
    a source of truth: the returned take durations are measured from the WAVs.
    """
    out = Path(out_dir)
    tts = service or TTSService(config)
    base_scale = float(config.get("tts", f"length_scale_{lang}", 1.08))
    voices_dir = out / "audio" / "voices"
    counters: dict[str, int] = {}
    takes: list[VoiceTake] = []

    for index, line in enumerate(dialogue, 1):
        who = str(line.get("character") or "").strip().lower()
        text = str(line.get("text") or "").strip()
        if not who:
            raise ValueError(f"Реплика #{index}: не указан персонаж.")
        if not text:
            raise ValueError(f"Реплика #{index}: пустой текст.")
        profile = resolve_profile(who, lang, line.get("voice"))
        spoken = profile.speak(text)
        counters[who] = counters.get(who, 0) + 1
        dest_dir = voices_dir / who
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"line_{counters[who]:03d}.wav"
        cached = dest.exists() and not force

        if not cached:
            try:
                source, _duration = tts.synthesize_line(
                    spoken,
                    lang,
                    voice_override=profile.voice,
                    length_scale=base_scale * profile.speed,
                    speaker=profile.speaker,
                )
            except TTSError as exc:
                raise TTSError(
                    f"Реплика #{index} («{text[:40]}») не озвучена: {exc}"
                ) from exc
            if abs(profile.pitch) > 1e-3:
                _shift_pitch(source, dest, profile.pitch)
            else:
                shutil.copyfile(source, dest)

        duration = wav_duration(dest)
        envelope = speech_envelope(dest)
        takes.append(
            VoiceTake(
                character=who,
                text=text,
                start=max(0.0, float(line.get("start") or 0.0)),
                duration=duration,
                wav=dest,
                voice_id=profile.voice_id,
                profile=profile,
                cached=cached,
                envelope=envelope,
                segments=speech_segments(envelope),
            )
        )
    return takes


def apply_timings(
    dialogue: Sequence[dict[str, Any]], takes: Sequence[VoiceTake]
) -> list[dict[str, Any]]:
    """Return a copy of ``dialogue`` whose durations are the measured ones."""
    updated: list[dict[str, Any]] = []
    for line, take in zip(dialogue, takes):
        entry = dict(line)
        entry["duration"] = round(take.duration, 3)
        entry["voice_id"] = take.voice_id
        entry["wav"] = str(take.wav)
        # lip-sync data measured from the real take, not guessed from the text
        envelope = getattr(take, "envelope", None)
        segments = getattr(take, "segments", None)
        if envelope:
            entry["envelope"] = list(envelope)
            entry["envelope_hz"] = float(getattr(take, "envelope_hz", ENVELOPE_HZ) or 0.0)
        if segments:
            entry["segments"] = [list(span) for span in segments]
        updated.append(entry)
    return updated


#: Silence kept between two spoken lines when a real take is longer than the
#: slot the scene author reserved for it.
DIALOGUE_GAP = 0.18


def schedule_dialogue(
    dialogue: Sequence[dict[str, Any]], *, gap: float = DIALOGUE_GAP
) -> list[dict[str, Any]]:
    """Push lines later so two characters never talk over each other.

    Scenes are authored with *estimated* line lengths; the synthesised take is
    usually longer, which used to make the next character start while the
    previous one was still speaking (and produce overlapping subtitles).  Each
    line keeps its own duration and only ever moves forward.
    """
    ordered = sorted(dialogue, key=lambda line: float(line.get("start") or 0.0))
    cursor = 0.0
    shifted: list[dict[str, Any]] = []
    for line in ordered:
        entry = dict(line)
        duration = float(entry.get("duration") or 0.0)
        start = max(float(entry.get("start") or 0.0), cursor)
        entry["start"] = round(start, 3)
        shifted.append(entry)
        cursor = start + max(0.0, duration) + max(0.0, float(gap))
    return shifted


# ---------------------------------------------------------------------- ambience


#: Very small procedural beds: filtered noise, no assets, no downloads.
AMBIENCE_PRESETS: tuple[str, ...] = ("forest", "cave", "meadow", "none")


def ambient_wav(
    preset: str,
    duration: float,
    sample_rate: int = AUDIO_RATE,
    seed: int = 20261030,
) -> bytes:
    """Procedural background bed as 16-bit mono WAV bytes."""
    name = str(preset or "none").lower()
    if name == "none":
        raise ValueError("Пресет окружения «none» не создаёт аудио.")
    if name not in AMBIENCE_PRESETS:
        raise ValueError(
            f"Неизвестный пресет окружения «{preset}». Доступно: {', '.join(AMBIENCE_PRESETS)}."
        )
    frames = max(1, int(round(max(0.1, float(duration)) * sample_rate)))
    rng = np.random.default_rng(seed)
    white = rng.normal(0.0, 1.0, frames)
    # brown noise: integrate white, remove the drift, then low-pass
    brown = np.cumsum(white)
    brown -= np.linspace(brown[0], brown[-1], frames)
    brown /= max(1e-6, float(np.max(np.abs(brown))))
    if name == "forest":
        alpha = 0.02
        gain = 0.55
    elif name == "cave":
        alpha = 0.006
        gain = 0.6
    else:
        alpha = 0.04
        gain = 0.35
    smoothed = np.empty_like(brown)
    acc = 0.0
    for i, value in enumerate(brown):
        acc += alpha * (value - acc)
        smoothed[i] = acc
    # a slow swell so the bed does not sound like a constant hiss
    t = np.linspace(0.0, max(0.1, duration), frames, endpoint=False)
    smoothed *= 1.0 + 0.25 * np.sin(2.0 * np.pi * 0.07 * t)
    smoothed /= max(1e-6, float(np.max(np.abs(smoothed))))
    samples = np.clip(smoothed * gain, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    import io as _io

    buffer = _io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


# ------------------------------------------------------------------------ mixing


@dataclass
class MixInput:
    """One audio source placed at ``start`` seconds with a linear gain."""

    path: Path
    start: float = 0.0
    gain: float = 1.0
    label: str = "voice"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "start": round(self.start, 3),
            "gain": round(self.gain, 3),
            "label": self.label,
        }


def mix_scene_audio(
    dest: str | Path,
    inputs: Sequence[MixInput],
    *,
    duration: float,
    sample_rate: int = AUDIO_RATE,
    peak: float = PEAK_LIMIT,
) -> Path:
    """Mix voice, SFX and ambience into one mono track under the peak limit.

    Every input is delayed to its scene time, gained, summed without
    ``amix``'s automatic attenuation (``normalize=0``) and then limited, so the
    speech stays the loudest, clearest element and the mix never clips.
    """
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not inputs:
        raise ValueError("Нечего микшировать: нет ни одной аудиодорожки.")

    command: list[str] = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    chains: list[str] = []
    labels: list[str] = []
    for index, item in enumerate(inputs):
        if not Path(item.path).exists():
            raise FileNotFoundError(f"Нет аудиофайла для микса: {item.path}")
        command += ["-i", str(item.path)]
        delay_ms = int(round(max(0.0, float(item.start)) * 1000.0))
        chains.append(
            f"[{index}:a]aresample={sample_rate},"
            f"aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"volume={max(0.0, float(item.gain)):.4f},"
            f"adelay=delays={delay_ms}:all=1[v{index}]"
        )
        labels.append(f"[v{index}]")
    chain = ";".join(chains)
    count = len(inputs)
    # ``apad`` first: the mix must cover the whole scene, otherwise the last
    # line ends the track early and ``-shortest`` would cut the picture short.
    chain += (
        f";{''.join(labels)}amix=inputs={count}:normalize=0:dropout_transition=0,"
        # ``level=false`` is required: with the default ``level=true`` alimiter
        # re-normalises the result and the peak lands back at 1.0 (measured).
        f"apad,alimiter=limit={min(0.99, max(0.1, peak)):.3f}:level=false,"
        f"aresample={sample_rate}[mix]"
    )
    command += [
        "-filter_complex",
        chain,
        "-map",
        "[mix]",
        "-t",
        f"{max(0.1, float(duration)):.3f}",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    proc = run(command, timeout=600, check=False)
    if proc.returncode != 0 or not target.exists() or target.stat().st_size <= 44:
        raise RuntimeError(
            f"Не удалось свести звук сцены: {(proc.stderr or '').strip()[:400]}"
        )
    return target


def mix_report(path: Path) -> dict[str, Any]:
    """Peak level and length of a mixed WAV, for the QC section of the report."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(data**2))) if data.size else 0.0
    return {
        "duration_s": round(frames / float(rate), 3) if rate else 0.0,
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "clipped": bool(peak >= 0.999),
        "rate": rate,
    }


def write_manifest(takes: Iterable[VoiceTake], path: str | Path) -> Path:
    """Write ``audio/voices/manifest.json`` so a later run can reuse or audit."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"takes": [take.to_dict() for take in takes]}
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
