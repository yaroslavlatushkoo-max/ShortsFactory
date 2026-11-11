from __future__ import annotations

import re
import shutil
import wave
from pathlib import Path
from typing import Any

from .config import Config
from .logging_setup import get_logger
from .models import Script
from .util import (
    atomic_write_json,
    clean_text,
    now_iso,
    sentence_split,
    sha256_text,
)


class TTSError(RuntimeError):
    pass


def _clean_for_speech(text: str) -> str:
    value = clean_text(text)
    value = re.sub(r"[*#_`~]+", " ", value)
    value = re.sub(r"[\[\]{}<>]", " ", value)
    value = re.sub(r"\s*[—–]\s*", ", ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if value and value[-1] not in ".!?…:;":
        value += "."
    return value


def _wav_info(path: Path) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            return (
                handle.getnchannels(),
                handle.getsampwidth(),
                handle.getframerate(),
                handle.getnframes(),
            )
    except wave.Error as exc:
        raise TTSError(f"Не читается WAV {path}: {exc}") from exc


class TTSService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self._voices: dict[tuple[str, bool], Any] = {}
        self._cache_dir = config.workspace_dir / "_cache" / "tts"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------- voices
    def voice_path(self, lang: str, override: str = "") -> Path:
        raw = override or str(self.config.get("tts", f"voice_{lang}", ""))
        if not raw:
            raise TTSError(f"Не задан голос для языка {lang}")
        path = Path(raw)
        if not path.is_absolute():
            path = self.config.models_dir / path
        if path.suffix.lower() != ".onnx":
            path = path.with_suffix(".onnx")
        if not path.exists():
            raise TTSError(
                f"Модель Piper не найдена: {path}. Проверьте models/piper и команду doctor."
            )
        return path

    def _voice(self, lang: str, override: str = "") -> Any:
        path = self.voice_path(lang, override)
        key = (str(path), False)
        if key not in self._voices:
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise TTSError("piper-tts не установлен. Запустите setup.ps1.") from exc
            self.logger.info("Загрузка голоса Piper: %s", path.name)
            self._voices[key] = PiperVoice.load(str(path), use_cuda=False)
        return self._voices[key]

    def _settings(self, lang: str, length_scale: float | None, voice_override: str = "") -> dict[str, Any]:
        base = float(self.config.get("tts", f"length_scale_{lang}", 1.08))
        scale = float(length_scale if length_scale is not None else base)
        scale = max(
            float(self.config.get("tts", "min_length_scale", 0.98)),
            min(float(self.config.get("tts", "max_length_scale", 1.15)), scale),
        )
        speaker = None
        if lang == "en":
            value = self.config.get("tts", "speaker_en", 0)
            speaker = int(value) if value is not None else None
        path = self.voice_path(lang, voice_override)
        stat = path.stat()
        return {
            "scale": scale,
            "speaker": speaker,
            "voice": path,
            "voice_key": f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}",
        }

    # ------------------------------------------------------------------ synthesis
    def synthesize_line(
        self,
        text: str,
        lang: str,
        *,
        voice_override: str = "",
        length_scale: float | None = None,
        speaker: int | None = None,
    ) -> tuple[Path, float]:
        """Synthesize one utterance, reusing the sentence cache.

        Returns the cached WAV path and its duration in seconds. Used by the
        animation engine to drive mouth movement from the real voice track.
        """
        settings = self._settings(lang, length_scale, voice_override)
        if speaker is not None:
            # character voices pick their own speaker on multi-speaker models
            settings["speaker"] = int(speaker)
        key = self._cache_key(text, lang, settings)
        path = self._cache_dir / f"{key}.wav"
        if not path.exists():
            self.logger.info("Озвучка анимационной реплики %s: %.60s", lang, text)
            self._synthesize_sentence(text, lang, settings, path)
        channels, _width, rate, frames = _wav_info(path)
        if channels != 1:
            raise TTSError(f"Piper вернул не mono WAV: {path}")
        return path, frames / rate

    def _cache_key(self, text: str, lang: str, settings: dict[str, Any]) -> str:
        return sha256_text(
            f"{lang}|{settings['voice_key']}|{settings['scale']:.3f}|{settings['speaker']}|{text}"
        )

    def _synthesize_sentence(
        self, text: str, lang: str, settings: dict[str, Any], cache_wav: Path
    ) -> None:
        from piper import SynthesisConfig

        voice = self._voice(lang, str(settings["voice"]))
        kwargs: dict[str, Any] = {
            "length_scale": float(settings["scale"]),
            "normalize_audio": True,
        }
        if settings["speaker"] is not None:
            kwargs["speaker_id"] = int(settings["speaker"])
        try:
            with wave.open(str(cache_wav), "wb") as wav_file:
                voice.synthesize_wav(
                    _clean_for_speech(text),
                    wav_file,
                    syn_config=SynthesisConfig(**kwargs),
                )
        except Exception as exc:  # piper raises several low-level exception types
            raise TTSError(f"Piper не смог озвучить фразу «{text[:80]}»: {exc}") from exc
        if not cache_wav.exists() or cache_wav.stat().st_size < 44:
            raise TTSError(f"Piper вернул пустой WAV для фразы «{text[:80]}»")

    def generate(
        self,
        script: Script,
        lang: str,
        workspace: Path,
        *,
        length_scale: float | None = None,
        voice_override: str = "",
    ) -> dict[str, Any]:
        settings = self._settings(lang, length_scale, voice_override)
        script_hash = sha256_text(script.all_text)
        audio_dir = workspace / "audio" / lang
        manifest_path = audio_dir / "manifest.json"
        if manifest_path.exists():
            try:
                import json

                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    existing.get("script_sha") == script_hash
                    and existing.get("voice") == settings["voice"].name
                    and abs(float(existing.get("length_scale", 0)) - settings["scale"]) < 0.001
                    and existing.get("speaker") == settings["speaker"]
                    and all((audio_dir / item.get("file", "")).exists() for item in existing.get("scenes", []))
                    and all(
                        (audio_dir / sentence.get("file", "")).exists()
                        for item in existing.get("scenes", [])
                        for sentence in item.get("sentences", [])
                    )
                ):
                    self.logger.info("Озвучка %s уже готова, используется кэш", lang)
                    return existing
            except (OSError, ValueError, TypeError):
                pass

        (audio_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (audio_dir / "sentences").mkdir(parents=True, exist_ok=True)
        pause_frames_default = int(
            float(self.config.get("tts", "sentence_pause", 0.10)) * 22050
        )
        manifest_scenes: list[dict[str, Any]] = []
        total_speech = 0.0

        for scene in script.scenes:
            sentences = sentence_split(scene.text)
            if not sentences:
                raise TTSError(f"Сцена {scene.id} пустая")
            frames_all = bytearray()
            sentence_records: list[dict[str, Any]] = []
            sample_rate = 22050
            for index, sentence in enumerate(sentences, 1):
                key = self._cache_key(sentence, lang, settings)
                cache_wav = self._cache_dir / f"{key}.wav"
                if not cache_wav.exists():
                    self.logger.info("Озвучка %s %s, фраза %s/%s", lang, scene.id, index, len(sentences))
                    self._synthesize_sentence(sentence, lang, settings, cache_wav)
                channels, width, sample_rate, frames = _wav_info(cache_wav)
                if channels != 1 or width != 2:
                    raise TTSError(f"Piper вернул не mono 16-bit WAV: {cache_wav}")
                sentence_path = audio_dir / "sentences" / f"{scene.id}_{index:02d}.wav"
                shutil.copy2(cache_wav, sentence_path)
                start = len(frames_all) / (sample_rate * 2)
                with wave.open(str(cache_wav), "rb") as handle:
                    data = handle.readframes(frames)
                frames_all.extend(data)
                duration = frames / sample_rate
                sentence_records.append(
                    {
                        "text": sentence,
                        "file": sentence_path.relative_to(audio_dir).as_posix(),
                        "start": round(start, 3),
                        "duration": round(duration, 3),
                    }
                )
                if index < len(sentences):
                    pause = int(
                        float(self.config.get("tts", "sentence_pause", 0.10)) * sample_rate
                    )
                    frames_all.extend(b"\x00\x00" * pause)
            scene_path = audio_dir / "scenes" / f"{scene.id}.wav"
            with wave.open(str(scene_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(bytes(frames_all))
            scene_duration = len(frames_all) / (sample_rate * 2)
            total_speech += scene_duration
            manifest_scenes.append(
                {
                    "scene_id": scene.id,
                    "file": scene_path.relative_to(audio_dir).as_posix(),
                    "duration": round(scene_duration, 3),
                    "sentences": sentence_records,
                }
            )

        manifest = {
            "lang": lang,
            "engine": "piper",
            "voice": settings["voice"].name,
            "voice_path": str(settings["voice"]),
            "voice_key": settings["voice_key"],
            "length_scale": settings["scale"],
            "speaker": settings["speaker"],
            "script_sha": script_hash,
            "total_speech": round(total_speech, 3),
            "sample_rate": 22050,
            "scenes": manifest_scenes,
            "generated_at": now_iso(),
        }
        atomic_write_json(manifest_path, manifest)
        self.logger.info(
            "Озвучка %s готова: %.1f с речи, %s сцен", lang, total_speech, len(manifest_scenes)
        )
        return manifest

    # ---------------------------------------------------------------------- probe
    def probe(self, lang: str, text: str = "Проверка синтеза речи.") -> dict[str, Any]:
        settings = self._settings(lang, None)
        key = self._cache_key(text, lang, settings)
        path = self._cache_dir / f"{key}.wav"
        if not path.exists():
            self._synthesize_sentence(text, lang, settings, path)
        channels, width, rate, frames = _wav_info(path)
        return {
            "ok": True,
            "lang": lang,
            "voice": settings["voice"].name,
            "duration": round(frames / rate, 2),
            "sample_rate": rate,
            "channels": channels,
            "width": width,
        }


def load_manifest(workspace: Path, lang: str) -> dict[str, Any] | None:
    import json

    path = workspace / "audio" / lang / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
