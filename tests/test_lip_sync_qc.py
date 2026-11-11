"""Lip-sync QC tests: verify_lip_sync metrics on a synthetic payload + WAV."""

import json
import wave

from shortsfactory.generative.animation_pipeline import lip_sync_qc as lq
from shortsfactory.generative.animation_pipeline import mouth_rig as mr


def _write_speech_wav(path, *, sr=22050, silence=0.25, tone=0.5, tail=0.25):
    """Synthetic 16-bit mono WAV: silence, then a tone burst, then silence."""
    import numpy as np
    n = int((silence + tone + tail) * sr)
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    s0 = int(silence * sr)
    s1 = int((silence + tone) * sr)
    x[s0:s1] = 0.45 * np.sin(2 * np.pi * 440.0 * t[s0:s1])
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _payload(steve_states: dict) -> dict:
    return {
        "fps": 30,
        "frames": [{} for _ in range(30)],
        "mouth_states": {"steve": steve_states},
    }


def test_verify_lip_sync_pass_when_mouth_follows_speech(tmp_path):
    wav = tmp_path / "mix.wav"
    _write_speech_wav(wav)
    segs = mr._speech_segments_from_wav(wav)
    assert segs, "тон в WAV обязан быть найден как речь"
    speaking = {
        fi: "steve_mouth_open.png"
        for fi in range(30)
        if any(a <= fi / 30 < b for a, b in segs)
    }
    assert speaking
    result = lq.verify_lip_sync(_payload(speaking), wav)
    assert result["status"] == "ok"
    assert result["speech_segments"] == [
        [round(a, 4), round(b, 4)] for a, b in segs
    ]
    m = result["characters"]["steve"]
    assert m["frames_speaking"] == len(speaking)
    assert m["frames_closed_while_speaking"] == 0
    assert m["ratio_closed"] == 0.0
    assert m["frames_flapping_while_silent"] == 0
    assert m["verdict"] == "pass"


def test_verify_lip_sync_warns_on_silent_mouth_during_speech(tmp_path):
    wav = tmp_path / "mix.wav"
    _write_speech_wav(wav)
    states = {fi: "steve_mouth_closed.png" for fi in range(30)}
    result = lq.verify_lip_sync(_payload(states), wav)
    m = result["characters"]["steve"]
    assert m["frames_speaking"] > 0
    assert m["frames_closed_while_speaking"] == m["frames_speaking"]
    assert m["ratio_closed"] == 1.0
    assert m["verdict"] == "warn"


def test_verify_lip_sync_counts_flapping_in_silence(tmp_path):
    wav = tmp_path / "mix.wav"
    _write_speech_wav(wav, silence=0.5, tone=0.4, tail=0.1)
    segs = mr._speech_segments_from_wav(wav)
    first_speech = min(a for a, _b in segs)
    # рот открыт только ДО начала речи — «хлопание в тишине»
    states = {
        fi: "steve_mouth_open.png"
        for fi in range(max(1, int(first_speech * 30) - 4))
    }
    result = lq.verify_lip_sync(_payload(states), wav)
    m = result["characters"]["steve"]
    assert m["frames_flapping_while_silent"] == len(states)
    assert m["ratio_flapping"] > 0.15
    assert m["verdict"] == "warn"


def test_verify_lip_sync_skips_payload_without_mouth_states(tmp_path):
    wav = tmp_path / "mix.wav"
    _write_speech_wav(wav)
    result = lq.verify_lip_sync({"fps": 30, "frames": [{}]}, wav)
    assert result["status"] == "skipped"
    assert result["characters"] == {}


def test_attach_lip_sync_qc_skips_without_wav(tmp_path):
    """QC не падает и пишет статус skipped, если WAV фильма не найден."""
    payload_path = tmp_path / "payload_demo.json"
    payload_path.write_text(
        json.dumps(_payload({0: "steve_mouth_open.png"})), encoding="utf-8"
    )
    script = tmp_path / "blocky_demo.py"
    script.write_text(
        f'PAYLOAD = Path(r"{payload_path}")\nDIAG = Path(r"{tmp_path}")\n',
        encoding="utf-8",
    )
    diag_path = tmp_path / "demo_diagnostics.json"
    diag = {"status": "OK"}
    result = lq.attach_lip_sync_qc(tmp_path, script, diag_path, diag)
    assert result["status"] == "skipped"
    assert "WAV" in result["reason"]
    saved = json.loads(diag_path.read_text(encoding="utf-8"))
    assert saved["lip_sync"]["status"] == "skipped"


def test_attach_lip_sync_qc_computes_metrics_and_writes_diag(tmp_path):
    payload_path = tmp_path / "payload_demo.json"
    payload_path.write_text(
        json.dumps(_payload({fi: "steve_mouth_open.png" for fi in range(30)})),
        encoding="utf-8",
    )
    script = tmp_path / "blocky_demo.py"
    script.write_text(f'PAYLOAD = Path(r"{payload_path}")\n', encoding="utf-8")
    diag_path = tmp_path / "demo_diagnostics.json"
    # WAV рядом с payload: mux_audio кладёт sfx.wav именно туда
    wav = tmp_path / "sfx.wav"
    _write_speech_wav(wav)
    diag = {"status": "OK"}
    result = lq.attach_lip_sync_qc(tmp_path, script, diag_path, diag)
    assert result["status"] == "ok"
    assert result["payload"] == "payload_demo.json"
    m = result["characters"]["steve"]
    assert m["frames_speaking"] > 0
    assert m["verdict"] == "warn"  # рот открыт и в тишине тоже
    saved = json.loads(diag_path.read_text(encoding="utf-8"))
    assert saved["lip_sync"]["characters"]["steve"]["frames_speaking"] > 0
