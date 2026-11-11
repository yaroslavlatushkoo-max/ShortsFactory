"""Smoke-тест TTS (tts.py): синтез одной короткой RU-реплики голосом dmitri.

Модель Piper и piper-tts берутся из локальной установки; без них тест
аккуратно пропускается. Кэш синтеза изолируется в tmp_path.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from shortsfactory.config import ROOT, Config
from shortsfactory.tts import TTSService

VOICE = ROOT / "models" / "piper" / "ru_RU-dmitri-medium.onnx"

pytest.importorskip("piper", reason="piper-tts не установлен")
pytestmark = pytest.mark.skipif(not VOICE.exists(), reason=f"Модель Piper не найдена: {VOICE}")


@pytest.fixture()
def service(tmp_path: Path) -> TTSService:
    svc = TTSService(Config(ROOT))
    svc._cache_dir = tmp_path / "tts_cache"  # изолируем кэш синтеза от workspace
    svc._cache_dir.mkdir(parents=True, exist_ok=True)
    return svc


def test_synthesize_line_creates_mono_wav_and_hits_cache(service: TTSService):
    path1, duration1 = service.synthesize_line("Привет.", "ru", voice_override=str(VOICE))

    assert path1.exists()
    assert path1.suffix == ".wav"
    assert path1.parent == service._cache_dir
    with wave.open(str(path1), "rb") as handle:
        assert handle.getnchannels() == 1  # Piper обязан вернуть mono
        frames = handle.getnframes()
        rate = handle.getframerate()
    assert rate > 0
    assert frames / rate == pytest.approx(duration1, abs=0.01)
    assert duration1 > 0.2

    # повторный вызов той же фразы должен взять файл из кэша, не пересинтезируя
    stamp = path1.stat().st_mtime_ns
    path2, duration2 = service.synthesize_line("Привет.", "ru", voice_override=str(VOICE))
    assert path2 == path1
    assert path1.stat().st_mtime_ns == stamp
    assert duration2 == duration1
