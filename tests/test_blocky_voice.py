"""Voice engine tests (A2): profiles, pronunciation, ambience, mixing (no Blender)."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

from shortsfactory.generative.blocky_scene import BlockyScene
from shortsfactory.generative.blocky_voice import (
    AUDIO_RATE,
    AMBIENCE_PRESETS,
    ENVELOPE_HZ,
    MixInput,
    VoiceProfile,
    VOICE_PROFILES,
    VoiceTake,
    ambient_wav,
    apply_timings,
    mix_report,
    mix_scene_audio,
    resolve_profile,
    schedule_dialogue,
    speech_envelope,
    speech_segments,
    wav_duration,
)


def _write_tone(path: Path, seconds: float, freq: float, rate: int = AUDIO_RATE) -> Path:
    frames = int(seconds * rate)
    data = bytearray()
    for i in range(frames):
        value = int(12000 * math.sin(2.0 * math.pi * freq * i / rate))
        data += value.to_bytes(2, "little", signed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(data))
    return path


# ------------------------------------------------------------------ profiles


def test_two_characters_get_different_voices():
    steve = resolve_profile("steve", "ru")
    alex = resolve_profile("alex", "ru")
    assert steve.voice.endswith(".onnx") and alex.voice.endswith(".onnx")
    assert steve.voice != alex.voice, "both characters would sound the same"
    assert steve.voice_id != alex.voice_id
    assert ("steve", "en") in VOICE_PROFILES and ("alex", "en") in VOICE_PROFILES


def test_unknown_character_or_language_is_an_error():
    with pytest.raises(ValueError, match="профил"):
        resolve_profile("milo", "ru")
    with pytest.raises(ValueError, match="профил"):
        resolve_profile("steve", "de")


def test_profile_override_changes_only_given_fields():
    base = resolve_profile("alex", "ru")
    louder = resolve_profile("alex", "ru", {"volume": 0.5})
    assert louder.volume == 0.5
    assert louder.voice == base.voice
    assert louder.speed == base.speed


def test_pronunciation_is_whole_word_and_case_insensitive():
    profile = resolve_profile("alex", "ru")
    assert "што" in profile.speak("Что это?")
    assert "што" in profile.speak("ЧТО это?")
    # a word that merely contains the replacement must not be rewritten
    assert "што" not in profile.speak("чтото").lower()
    assert profile.speak("Привет") == "Привет"


def test_profile_round_trip_to_dict():
    data = resolve_profile("steve", "ru").to_dict()
    assert data["voice"] and data["lang"] == "ru"
    assert "pronunciation" not in data  # empty for this voice


# ----------------------------------------------------------------- ambience


def test_ambient_wav_has_requested_length_and_is_audible():
    raw = ambient_wav("forest", 2.0)
    assert len(raw) == 44 + int(2.0 * AUDIO_RATE) * 2
    peak = max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True))
               for i in range(44, len(raw), 2 * 97))
    assert peak > 200, "ambience is silent"


def test_ambient_rejects_unknown_and_none():
    assert "forest" in AMBIENCE_PRESETS
    with pytest.raises(ValueError, match="пресет"):
        ambient_wav("jungle", 1.0)
    with pytest.raises(ValueError, match="не создаёт аудио"):
        ambient_wav("none", 1.0)


# ------------------------------------------------------------------- mixing


def test_mix_scene_audio_places_lines_and_keeps_duration(tmp_path):
    first = _write_tone(tmp_path / "a.wav", 0.4, 440.0)
    second = _write_tone(tmp_path / "b.wav", 0.4, 660.0)
    out = mix_scene_audio(
        tmp_path / "mix.wav",
        [MixInput(first, label="steve"), MixInput(second, start=1.0, gain=0.5, label="alex")],
        duration=2.0,
    )
    assert out.exists()
    assert abs(wav_duration(out) - 2.0) < 0.02
    report = mix_report(out)
    assert report["rate"] == AUDIO_RATE
    assert report["duration_s"] > 1.9
    assert report["peak"] > 0.05
    assert report["clipped"] is False


def test_mix_respects_the_peak_limit_for_a_hot_mix(tmp_path):
    """Three full-scale sources must still come out below the limiter ceiling."""
    loud = _write_tone(tmp_path / "loud.wav", 1.0, 220.0)
    # a tone helper writes -12 dBFS, so amplify to a deliberately hot level
    inputs = [
        MixInput(loud, gain=4.0, label="steve"),
        MixInput(loud, gain=4.0, label="alex"),
        MixInput(loud, gain=4.0, label="sfx"),
    ]
    out = mix_scene_audio(tmp_path / "hot.wav", inputs, duration=1.0, peak=0.95)
    report = mix_report(out)
    assert report["peak"] <= 0.951, report
    assert report["clipped"] is False


def test_schedule_dialogue_never_lets_two_lines_overlap():
    lines = [
        {"character": "steve", "text": "a", "start": 1.0, "duration": 2.6},
        {"character": "alex", "text": "b", "start": 3.0, "duration": 2.6},
        {"character": "steve", "text": "c", "start": 7.0, "duration": 0.8},
    ]
    shifted = schedule_dialogue(lines, gap=0.2)
    assert [line["start"] for line in shifted] == [1.0, 3.8, 7.0]
    for first, second in zip(shifted, shifted[1:]):
        assert first["start"] + first["duration"] + 0.2 <= second["start"] + 1e-9
    # durations and text are untouched, the input is not mutated
    assert [line["duration"] for line in shifted] == [2.6, 2.6, 0.8]
    assert lines[1]["start"] == 3.0


def test_mix_rejects_empty_input_and_missing_file(tmp_path):
    with pytest.raises(ValueError):
        mix_scene_audio(tmp_path / "x.wav", [], duration=1.0)
    with pytest.raises(FileNotFoundError):
        mix_scene_audio(tmp_path / "x.wav", [MixInput(tmp_path / "nope.wav")], duration=1.0)


def test_mix_input_serialises_for_the_report():
    data = MixInput(Path("a.wav"), start=1.25, gain=0.45, label="sfx").to_dict()
    assert data == {"path": "a.wav", "start": 1.25, "gain": 0.45, "label": "sfx"}


# ---------------------------------------------------------------- timings


def test_apply_timings_uses_the_real_take_length():
    dialogue = [{"character": "steve", "text": "Привет", "start": 1.0, "duration": 0.5}]
    take = type(
        "T",
        (),
        {"duration": 1.75, "voice_id": "steve_ru", "wav": Path("a.wav"), "end": 2.75},
    )()
    timed = apply_timings(dialogue, [take])
    assert timed[0]["duration"] == 1.75
    assert timed[0]["voice_id"] == "steve_ru"
    assert timed[0]["text"] == "Привет"
    # the input document is not mutated
    assert dialogue[0]["duration"] == 0.5


# ---------------------------------------------------------------- lip sync


def _write_bursts(path: Path, bursts, seconds: float, rate: int = AUDIO_RATE) -> Path:
    """Tone only inside ``bursts`` (list of ``(start, length)``), silence else."""
    total = int(seconds * rate)
    samples = [0] * total
    for begin, length in bursts:
        first = int(begin * rate)
        for i in range(int(length * rate)):
            index = first + i
            if 0 <= index < total:
                samples[index] = int(12000 * math.sin(2.0 * math.pi * 440.0 * i / rate))
    data = bytearray()
    for value in samples:
        data += value.to_bytes(2, "little", signed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(data))
    return path


def test_speech_envelope_is_normalised_and_tracks_the_signal(tmp_path):
    clip = _write_bursts(tmp_path / "bursts.wav", [(0.0, 0.5), (1.0, 0.5)], 1.5)
    envelope = speech_envelope(clip)
    assert len(envelope) == int(round(1.5 * ENVELOPE_HZ))
    assert max(envelope) > 0.7, "speech is not detected as loud"
    # the silent middle of the file really is quiet
    middle = envelope[int(0.75 * ENVELOPE_HZ)]
    assert middle < 0.15, middle


def test_speech_envelope_of_silence_is_all_zero(tmp_path):
    quiet = _write_bursts(tmp_path / "quiet.wav", [], 1.0)
    envelope = speech_envelope(quiet)
    assert envelope and set(envelope) == {0.0}
    assert speech_segments(envelope) == []


def test_speech_segments_separates_two_spoken_blocks(tmp_path):
    clip = _write_bursts(tmp_path / "two.wav", [(0.1, 0.4), (1.0, 0.4)], 1.6)
    spans = speech_segments(speech_envelope(clip))
    assert len(spans) == 2, spans
    assert 0.0 <= spans[0][0] <= 0.15 and 0.4 <= spans[0][1] <= 0.7
    assert 0.9 <= spans[1][0] <= 1.1 and 1.3 <= spans[1][1] <= 1.6
    # no overlap and no inverted spans
    assert spans[0][1] < spans[1][0]
    assert all(end > begin for begin, end in spans)


def test_speech_segments_bridges_a_short_stop(tmp_path):
    clip = _write_bursts(tmp_path / "stops.wav", [(0.1, 0.3), (0.46, 0.3)], 1.0)
    spans = speech_segments(speech_envelope(clip), min_silence=0.12)
    assert len(spans) == 1, spans


def test_apply_timings_carries_lip_sync_data():
    dialogue = [{"character": "steve", "text": "Привет", "start": 1.0, "duration": 0.5}]
    take = VoiceTake(
        character="steve",
        text="Привет",
        start=1.0,
        duration=1.2,
        wav=Path("a.wav"),
        voice_id="steve-ru",
        profile=VOICE_PROFILES[("steve", "ru")],
        envelope=[0.0, 0.5, 1.0],
        segments=[[0.1, 0.9]],
    )
    timed = apply_timings(dialogue, [take])
    assert timed[0]["envelope"] == [0.0, 0.5, 1.0]
    assert timed[0]["envelope_hz"] == ENVELOPE_HZ
    assert timed[0]["segments"] == [[0.1, 0.9]]
    assert take.speech_seconds == 0.8
    assert take.to_dict()["segments"] == [[0.1, 0.9]]


def test_take_without_audio_gets_no_lip_sync_keys(tmp_path):
    """A silent take must not invent segments; the mouth stays shut."""
    quiet = _write_bursts(tmp_path / "quiet.wav", [], 0.5)
    envelope = speech_envelope(quiet)
    take = VoiceTake(
        character="alex",
        text="...",
        start=0.0,
        duration=0.5,
        wav=quiet,
        voice_id="alex-ru",
        profile=VOICE_PROFILES[("alex", "ru")],
        envelope=envelope,
        segments=speech_segments(envelope),
    )
    timed = apply_timings([{"character": "alex", "text": "...", "start": 0.0,
                            "duration": 0.5}], [take])
    assert "segments" not in timed[0]
    assert take.to_dict()["speech_s"] == 0.0


# --------------------------------------------------------------- scene hook


def _scene_dict() -> dict:
    return {
        "title": "Голосовой тест",
        "duration": 6.0,
        "ambience": "forest",
        "actors": [
            {
                "character": "steve",
                "origin": [0.0, 0.9, 0.0],
                "duration": 6.0,
                "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
            }
        ],
        "camera_cuts": [{"frame": 1, "center": [0.0, 0.0, 1.0], "height": 2.6}],
        "dialogue": [
            {"character": "steve", "text": "Привет!", "start": 1.0, "duration": 1.0}
        ],
    }


def test_scene_reads_ambience_and_rejects_unknown_one(tmp_path):
    scene = BlockyScene.from_dict(_scene_dict())
    assert scene.ambience == "forest"
    assert len(scene.dialogue) == 1
    assert scene.actors[0]["voice_lines"][0]["text"] == "Привет!"
    bad = _scene_dict()
    bad["ambience"] = "jungle"
    with pytest.raises(ValueError, match="амбиент"):
        BlockyScene.from_dict(bad).validate()


def test_default_profile_files_exist_for_ru_pairs():
    from shortsfactory.config import load_config

    cfg = load_config()
    for pair in (("steve", "ru"), ("alex", "ru")):
        voice = VOICE_PROFILES[pair].voice
        assert (Path(cfg.models_dir) / voice).exists(), f"missing Piper voice {voice}"
