"""End-to-end orchestrator for `shortsfactory ai-create` (Mission 3 §5, Mission 4).

story -> scene plan -> cinematic plan -> references -> generation -> assembly
-> audio -> final MP4 -> QC. The generative path refuses to fake output: if no
backend is configured it stops after the planning artifacts with a clear
message. Procedural mode delegates to the existing integration pipeline.

Mission 4 additions: per-stage provider selection (text/video/voice), dry-run
cost preflight, generation limits, resumable per-scene video, and a richer
manifest that records providers/models/stages/costs — never secrets.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import Config
from . import assembly, qc
from .backend import BackendError, _load_dotenv, create_backend
from .cinematic import plan_cinematic
from .models import AiCreateResult, SceneGenerationSpec
from .planner import plan_story
from .presets import get_preset
from .references import (
    build_character_bibles,
    build_environment_bibles,
    build_manifest,
    build_prompt,
    build_style_bible,
)
from .registry import Capability, TextProvider, VoiceProvider, mask_secrets, registry as get_registry


def _write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _ollama(config: Config):
    from ..llm import OllamaClient

    try:
        if config.get("llm", "enabled", True):
            client = OllamaClient(config)
            if client.available():
                return client
    except Exception:  # noqa: BLE001
        return None
    return None


class _PlannerAdapter:
    """Maps a registered TextProvider onto the legacy OllamaClient protocol used by planner.py."""

    def __init__(self, provider: TextProvider, model: str = "") -> None:
        self._provider = provider
        self._model = model

    def generate_json(self, prompt: str, *, system: str = "", temperature: float | None = None) -> dict:
        kwargs: dict[str, Any] = {"system": system, "attempts": 3, "temperature": temperature}
        return self._provider.generate_structured(prompt, **kwargs)


def _resolve_text_provider(config: Config, text_provider: str, text_model: str):
    """Return a planner-callable object or None (deterministic fallback).

    Preference: requested provider (if configured) -> any configured text
    provider -> Ollama if reachable -> None.
    """
    reg = get_registry()
    requested = text_provider or "ollama"
    obj = reg.get_provider(requested)
    if obj is not None and isinstance(obj, TextProvider) and obj.is_configured():
        return _PlannerAdapter(obj, text_model)
    configured = reg.providers_for_capability(Capability.TEXT_GENERATION, only_configured=True)
    for p in configured:
        if isinstance(p, TextProvider):
            return _PlannerAdapter(p, text_model)
    return _ollama(config)


def _resolve_voice_provider(voice_provider: str):
    reg = get_registry()
    requested = voice_provider or "piper"
    obj = reg.get_provider(requested)
    if obj is not None and isinstance(obj, VoiceProvider) and obj.is_configured():
        return obj
    fallback = reg.get_provider("piper")
    if isinstance(fallback, VoiceProvider) and fallback.is_configured():
        return fallback
    return None


def _procedural_runner(script: str, out_dir: Path, config: Config, title: str) -> AiCreateResult:
    from ..integration import IntegrationError, run_integrated_create

    res = AiCreateResult(mode="procedural", provider="procedural-renderer", output_dir=str(out_dir))
    try:
        integ = run_integrated_create(
            script, out_dir, config=config, title=title or None, overwrite=True
        )
    except IntegrationError as exc:
        res.status = "ERROR"
        res.error = str(exc)
        res.notes.append("procedural: интеграционный конвейер не смог разобрать сценарий")
        return res
    res.status = "OK" if integ.status == "PASS" else "WARNING"
    res.video = integ.video
    res.contact_sheet = integ.contact_sheet
    res.notes.append("Сгенерировано процедурным 2D-рендером (не генеративным видео).")
    return res


def _estimate_specs(scenes, shots, preset, references, backend):
    return [
        SceneGenerationSpec(
            scene_idx=s.scene_idx,
            prompt="",
            negative_prompt="",
            duration=scenes[s.scene_idx].duration,
            reference_images=[Path(r) for r in (references or []) if Path(r).exists()],
        )
        for s in shots
    ]


def run_ai_create(
    script: str,
    out_dir: Path,
    config: Config,
    *,
    preset_id: str = "cinematic3d",
    mode: str = "generative",
    provider: str = "",
    video_model: str = "",
    title: str = "",
    references: list[str] | None = None,
    with_audio: bool = True,
    poll_interval: float = 5.0,
    poll_timeout: float = 600.0,
    overwrite: bool = False,
    text_provider: str = "",
    text_model: str = "",
    voice_provider: str = "",
    dry_run: bool = False,
    max_generations: int | None = None,
) -> AiCreateResult:
    if mode not in {"procedural", "generative", "hybrid"}:
        raise ValueError(f"Неизвестный режим {mode!r} (допустимо: procedural/generative/hybrid)")

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Папка результата не пуста: {out_dir} (укажите --overwrite)")
    out_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Ensure .env is loaded before any provider/backend is constructed.
    _load_dotenv(config.root)

    preset = get_preset(preset_id)

    if mode == "procedural":
        return _procedural_runner(script, out_dir, config, title)

    # --- planning (shared by generative & hybrid) ---
    planner = _resolve_text_provider(config, text_provider, text_model)
    scenes = plan_story(script, planner)
    if not scenes:
        res = AiCreateResult(mode=mode, output_dir=str(out_dir))
        res.status = "ERROR"
        res.error = "Планировщик не вернул ни одной сцены."
        return res
    shots = plan_cinematic(scenes, preset)
    chars = build_character_bibles(scenes)
    envs = build_environment_bibles(scenes)
    style = build_style_bible(preset)
    prompts = [
        build_prompt(scenes[s.scene_idx], s, style, chars, envs) for s in shots
    ]
    manifest = build_manifest(scenes, chars, envs, style, prompts)

    (out_dir / "script.txt").write_text(script, encoding="utf-8")
    _write_json(out_dir / "scenes.json", [s.to_dict() for s in scenes])
    _write_json(out_dir / "cinematic_plan.json", [s.to_dict() for s in shots])
    _write_json(out_dir / "character_bible.json", {k: v.to_dict() for k, v in chars.items()})
    _write_json(out_dir / "environment_bible.json", {k: v.to_dict() for k, v in envs.items()})
    _write_json(out_dir / "visual_style_bible.json", style.to_dict())

    res = AiCreateResult(mode=mode, output_dir=str(out_dir))
    res.notes.append("Планирование завершено (сцены, кинематограф, библии, манифест).")
    res.notes.append("Идентичность персонажа не гарантируется без reference-изображений/кондиционирования.")

    # --- text provider info ---
    text_meta: dict[str, Any] = {"provider": "deterministic-rule-based", "model": ""}
    if planner is not None:
        inner = getattr(planner, "_provider", None)
        if inner is not None:
            text_meta = {
                "provider": inner.id,
                "model": (inner.models_for(Capability.TEXT_GENERATION) or [""])[0],
                "configured": inner.is_configured(),
            }
        else:
            text_meta = {"provider": "ollama", "model": getattr(planner, "model", "")}

    # --- backend setup ---
    backend = create_backend(provider, root=config.root)
    res.provider = backend.name

    if video_model:
        backend.apply_model_override(video_model)
        res.notes.append(f"Модель видео переопределена через CLI: {video_model}")

    specs = [
        SceneGenerationSpec(
            scene_idx=s.scene_idx,
            prompt=prompts[s.scene_idx],
            negative_prompt=(preset.negative_prompt if backend.capabilities.supports_negative_prompt else ""),
            duration=scenes[s.scene_idx].duration,
            reference_images=[Path(r) for r in (references or []) if Path(r).exists()],
        )
        for s in shots
    ]

    # --- cost estimate (never invented; only when tariff is known) ---
    estimate = backend.estimate_cost(specs)
    res.cost = estimate
    planned_gen = len(specs)
    limit = min(max_generations or planned_gen, planned_gen)

    # Full-plan estimate vs the estimate for the generation that will actually run.
    # Clips that already exist are reused (free), so they are excluded from the
    # limited estimate to keep it honest.
    def _target_for(spec: SceneGenerationSpec) -> Path:
        return clips_dir / f"scene_{spec.scene_idx}.mp4"

    def _reusable(spec: SceneGenerationSpec) -> bool:
        return _target_for(spec).exists() and _target_for(spec).stat().st_size > 0

    capped_specs = specs[:limit]
    new_specs = [s for s in capped_specs if not _reusable(s)]
    reused_count = len(capped_specs) - len(new_specs)
    estimate_limited = backend.estimate_cost(new_specs)
    res.cost_limited = estimate_limited
    if limit < planned_gen and estimate is not None:
        lim_usd = estimate_limited.estimated_usd if estimate_limited is not None else "н/д"
        reused_note = f" ({reused_count} сцен уже готовы и не входят в ограниченную оценку)" if reused_count else ""
        res.notes.append(
            f"Оценка стоимости полного плана: {estimate.estimated_usd} USD; "
            f"с учётом лимита ({limit}/{planned_gen} генераций): {lim_usd} USD{reused_note}."
        )

    if dry_run:
        res.status = "PLANNED"
        res.ran_generative = False
        res.notes.append(
            f"dry-run: план построен, генераций запланировано {limit}/{planned_gen}. "
            "Ни один платный запрос НЕ отправлен."
        )
        if estimate is not None:
            res.notes.append(
                f"Оценка стоимости (если тариф известен): {estimate.estimated_usd} USD ({estimate.provider})."
            )
        else:
            res.notes.append("Стоимость не может быть оценена: тариф провайдера неизвестен/не задан.")
        _finish_manifest(out_dir, manifest, res, backend, text_meta, None, None, planned_gen, limit)
        return res

    if not backend.is_configured():
        res.status = "WARNING"
        res.ran_generative = False
        res.notes.append(
            "Генеративный видеобэкенд не настроен — НАСТОЯЩЕЕ генеративное видео НЕ создавалось. "
            "Артефакты планирования доступны. Задайте провайдера и ключ в .env и повторите, "
            "или используйте --mode procedural."
        )
        _finish_manifest(out_dir, manifest, res, backend, text_meta, None, None, planned_gen, limit)
        return res

    # --- generation (with resumable scene clips, bounded by limit) ---
    clips: list[str] = []
    failed = 0
    skipped = 0
    for spec in specs[:limit]:
        target = clips_dir / f"scene_{spec.scene_idx}.mp4"
        if target.exists() and target.stat().st_size > 0:
            clips.append(str(target))
            skipped += 1
            continue
        try:
            job = backend.run_to_completion(spec, clips_dir, poll_interval, poll_timeout)
            src = Path(job.media)
            if src.resolve() != target.resolve():
                shutil.copy2(str(src), str(target))
            clips.append(str(target))
        except BackendError as exc:
            failed += 1
            res.notes.append(f"Сцена {spec.scene_idx}: {mask_secrets(str(exc), backend.env)}")
    if not clips:
        res.status = "ERROR"
        res.error = "Все сцены не удалось сгенерировать."
        res.notes.append("Видео не собрано — генеративный провайдер не вернул клипы.")
        _finish_manifest(out_dir, manifest, res, backend, text_meta, None, None, planned_gen, limit)
        return res
    if failed:
        res.notes.append(f"некоторые сцены сорваны ({failed}) — собраны только успешные")
    if skipped:
        res.notes.append(f"повторно использованы готовые сцены ({skipped}) — без новых платных генераций")
    if limit < planned_gen:
        res.notes.append(f"лимит генераций {limit}/{planned_gen} — остальные сцены не генерировались")
    res.clips = clips
    res.ran_generative = True

    # --- audio & assembly ---
    segments: list[tuple[float, str]] = []
    if with_audio:
        try:
            voice = _resolve_voice_provider(voice_provider)
            if voice is not None:
                audio_dir = out_dir / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                t = 0.0
                for i, scene in enumerate(scenes[: len(clips)]):
                    if scene.dialogue.strip():
                        ext = "wav" if voice.provider_type == "local" else "mp3"
                        out_wav = audio_dir / f"line_{i}.{ext}"
                        _, meta = voice.synthesize(scene.dialogue, lang="ru", out_path=out_wav)
                        segments.append((t, str(out_wav)))
                    t += scene.duration
                segments = segments[: len(clips)]
                res.notes.append(f"Озвучка: {voice.id}")
            else:
                res.notes.append("озвучка пропущена: голосовой провайдер не настроен")
                segments = []
        except Exception as exc:  # noqa: BLE001
            res.notes.append(f"озвучка пропущена: {mask_secrets(str(exc))}")
            segments = []

    try:
        final_path = assembly.build_final(clips, out_dir, segments or None)
    except Exception as exc:  # noqa: BLE001
        res.status = "ERROR"
        res.error = f"Сборка не удалась: {mask_secrets(str(exc))}"
        return res
    res.video = str(final_path)

    # --- QC ---
    try:
        metrics = qc.run_qc(clips, final_path, out_dir / "qc")
        qc_json = _write_json(out_dir / "qc.json", metrics.to_dict())
        res.qc_json = str(qc_json)
        sheet = out_dir / "qc" / "contact_sheet.jpg"
        res.contact_sheet = str(sheet) if sheet.exists() else ""
        res.status = "OK" if (metrics.decode_ok and metrics.clips_ok == len(clips)) else "WARNING"
    except Exception as exc:  # noqa: BLE001
        res.notes.append(f"QC не выполнен: {exc}")

    _finish_manifest(out_dir, manifest, res, backend, text_meta, voice_picked(res, voice_provider), clips, planned_gen, limit)
    res.notes.append("Для оценки субъективного визуального качества откройте контактный лист и видео.")
    return res


def voice_picked(res: AiCreateResult, voice_provider: str) -> dict[str, Any] | None:
    for note in res.notes:
        if note.startswith("Озвучка:"):
            return {"provider": note.split(":", 1)[1].strip(), "configured": True}
    return None


def _finish_manifest(
    out_dir: Path,
    manifest: dict[str, Any],
    res: AiCreateResult,
    backend,
    text_meta: dict[str, Any],
    voice_meta: dict[str, Any] | None,
    clips,
    planned_gen: int | None = None,
    cap: int | None = None,
) -> None:
    """Augment the continuity manifest with provider/stage/cost records (no secrets)."""
    stages = [
        {"stage": "story_planning", "status": "done", "provider": text_meta.get("provider", ""),
         "model": text_meta.get("model", ""), "path": str(out_dir / "scenes.json")},
        {"stage": "cinematic_planning", "status": "done", "provider": "deterministic",
         "model": "", "path": str(out_dir / "cinematic_plan.json")},
        {"stage": "references", "status": "done", "provider": "deterministic", "model": "",
         "path": str(out_dir / "scene_continuity_manifest.json")},
        {"stage": "video_generation", "status": res.status, "provider": res.provider,
         "model": (backend.capabilities.supports_t2v and getattr(backend, "default_model", "") or ""),
         "path": str(out_dir / "clips") if clips else "not-run"},
        {"stage": "assembly_audio", "status": "done" if res.video else "skipped",
         "provider": (voice_meta or {}).get("provider", ""), "model": "",
         "path": str(res.video or "")},
        {"stage": "qc", "status": res.status, "provider": "local", "model": "",
         "path": str(res.qc_json or "")},
    ]
    manifest["stages"] = stages
    manifest["providers"] = {
        "text": text_meta,
        "video": {"id": res.provider, "configured": backend.is_configured()},
        "voice": voice_meta or {"provider": "none", "configured": False},
    }
    manifest["limits"] = {
        "dry_run": res.status == "PLANNED",
        "planned_generations": planned_gen if planned_gen is not None else len(res.clips or []),
        "max_generations": cap if cap is not None else len(res.clips or []),
    }
    manifest["cost"] = res.cost.to_dict() if res.cost is not None else {"estimated_usd": None}
    manifest["timestamps"] = {"completed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    manifest_path = _write_json(out_dir / "scene_continuity_manifest.json", manifest)
    res.manifest_path = str(manifest_path)
