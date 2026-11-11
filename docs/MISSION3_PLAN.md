# Mission 3 — Implementation plan (Phase 1 audit & research)

Status: baseline recorded, research done — implementation next. This is the
short architectural plan required before changes. It is a working plan; the
authoritative architecture doc is `docs/GENERATIVE_VIDEO_ARCHITECTURE.md`.

## 1. Baseline (what exists)
- ShortsFactory: 2D Pillow front-view cut-out rig, SVG assets, deterministic
  renderer, Piper TTS, FFmpeg export 1080x1920, QC. 6 integration pilots (all
  WARNING). Tests: 25 integration + 134 factory + 41 storystudio + 129 lab.
- StoryStudio: offline pre-production storyboard compiler (Pydantic, 30 atomic
  actions, camera 9:16). Not a renderer.
- Animation Lab: motion plan/simulation/validation, `motion_plan` JSON.
- Hardware: RTX 3050 Laptop 4GB VRAM → local video generation infeasible.
- Network: web access works; Ollama running (qwen3:4b etc.); ANTHROPIC_API_KEY
  present; no video-generation API key present.

## 2. Mode separation
- `procedural` — unchanged existing renderer.
- `generative` — new video backend (Kling/Veo/fal adapter), only when configured;
  without a provider it raises a clear error, never silently fakes.
- `hybrid` — generative scene video + Story/Animation Lab control + audio +
  subtitles + QC.

## 3. New modules (in `src/shortsfactory/generative/`)
1. `models.py` — dataclasses for Scene, CinematicShot, CharacterBible,
   EnvironmentBible, StylePreset, GenerationSpec, BackendCapabilities, QCReport.
2. `presets.py` — 6 visual presets (cinematic3d, stylized_film, premium2d,
   realistic_env, painterly_fantasy, documentary).
3. `planner.py` — story text → scenes (Ollama → Anthropic → deterministic
   splitter). Emits scenes with location/characters/action/dialogue/emotion/
   duration/visual goal.
4. `cinematic.py` — scene → CinematicShot (shot type, rake, camera motion,
   composition, light, palette, mood, key visual events, transitions).
5. `references.py` — character/environment/style bibles + consistent prompt
   blocks; optional background-image generation from the procedural renderer for
   image-to-video providers.
6. `backend.py` — `VideoBackend` ABC (submit t2v/i2v/flf2v, poll, fetch, cancel,
   capabilities, cost estimate) + registry + config loading .env.
7. `providers/kling.py`, `providers/veo.py`, `providers/fal.py`,
   `providers/disabled.py`.
8. `assembly.py` — FFmpeg scene concat, audio mix (Piper TTS reuse), subtitles.
9. `qc.py` — decode, flicker/frame-diff metric, per-clip + clip-boundary checks,
   resolution/duration, contact sheet.
10. `pipeline.py` — orchestrator: story->scenes->cinematic->references->gen->
    assemble->audio->MP4->QC.
11. CLI: add `shortsfactory ai-create` subcommand (respects existing style).

## 4. Data flow
```
story.txt + preset + backend cfg + [refs]
  → planner → scenes
  → cinematic → shots
  → references → bibles + prompts (+ optional reference frames)
  → backend.submit → poll → fetch (per scene)
  → assembly (concat + audio + subs) → final MP4
  → qc → report.json/md + contact sheet
```

## 5. Deliverables / tests
- Tests: `tests/test_generative_*.py` — backend config, missing key, timeout,
  malformed response, no-video error, corrupt clip, resolution check, assembly,
  export, secret non-leak, explicit fallback policy.
- Output dir: `D:\ShortsFactory\output\mission3_ai_video\` (script, scene plan,
  cinematic plan, bibles, manifest, clips, final MP4, contact sheet, QC, logs).
- Docs: VIDEO_MODEL_RESEARCH.md (done), GENERATIVE_VIDEO_ARCHITECTURE.md,
  GENERATIVE_VIDEO_SETUP.md, GENERATIVE_VIDEO_RESULTS.md,
  GENERATIVE_VIDEO_LIMITATIONS.md.

## 6. Known constraints (frozen)
- Preserve all three projects and the old CLI; never overwrite prior pilots.
- No paid video API calls without an explicitly configured provider; no invented
  pricing/endpoints; no fake generative output when the backend is unavailable.
- The generative backend is optional: if unconfigured, `ai-create` tells the
  user and offers `--mode procedural`.
