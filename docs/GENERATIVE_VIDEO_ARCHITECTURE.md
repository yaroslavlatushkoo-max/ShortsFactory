# Generative Video Architecture (Mission 3)

Status: **implemented**. Real generative rendering requires a configured paid
provider key (see SETUP). Without one, the pipeline produces the full planning
artifacts and explicitly refuses to fake a video.

## 1. Layered design (decoupled from frame creation)

```
Story text
  └─ Story Intelligence (planner.py)      → ScenePlan[]    (who/what/why/emotion/visual goal)
  └─ Cinematic Planning (cinematic.py)    → CinematicShot[] (shot type, angle, camera motion, composition, light, palette, mood, transitions)
  └─ Reference & Continuity (references.py)→ CharacterBible, EnvironmentBible, VisualStyleBible, continuity manifest
  └─ Video Generation Backend (backend.py) → provider adapters (veo/kling/fal/disabled)
  └─ Assembly (assembly.py)               → FFmpeg concat + TTS audio mix + subtitles-capable
  └─ QC (qc.py)                           → decode, flicker, continuity, contact sheet
```

Each layer emits plain, validateable structures (`models.py`) persisted as JSON.
No layer creates frames except the backend and the procedural renderer.

### Layers
- **A. Story Intelligence** — `planner.py` via Ollama (or deterministic paragraph
  splitter offline). Emits `ScenePlan`: location, characters, action summary,
  dialogue, emotion, visual goal, duration.
- **B. Cinematic Planning** — `cinematic.py` deterministic rules produce
  meaningful wide/medium/close choices grounded in content, with camera
  position/motion, composition, depth, lighting, palette, mood, key events,
  transitions.
- **C. Reference & Continuity** — `references.py` builds stable per-character
  bibles (deterministic descriptions reused across scenes) and a continuity
  manifest. Identity is **not** claimed without backend reference images.
- **D. Video Generation Backend** — `backend.py` defines
  `VideoBackend` (submit/poll/fetch/cancel + capabilities + cost estimate).
  Providers: `veo` (Gemini), `kling` (official API), `fal` (aggregator),
  `disabled` (default, refuses). No pipeline code depends on one API.
- **E. Audio & Finishing** — `assembly.py` normalizes clips to the canvas,
  concats with FFmpeg, places Piper TTS dialogue at scene offsets and mixes.
  Lip-sync is not claimed (timeline-level mixing only).

## 2. Modes (Mission 3 §4)
- `procedural` — delegates to the existing `integrated-create` 2D pipeline.
  Always available offline. Never labelled as generative.
- `generative` — uses the configured video backend. Without a key it stops
  after planning artifacts and returns WARNING (no fake video).
- `hybrid` — generative scene video + programmatic control of script/timing/
  audio/editing/QC. Uses the same backend; QC and audio are controlled by us.

## 3. Provider capabilities (from research, NOT invented)
| | Veo 3.1 Lite | Kling 3.0 | fal (hosted) |
|---|---|---|---|
| T2V | + | + | depends on model |
| I2V | + (reference image) | + | depends |
| First/last frame | – | + | depends |
| Character reference | reference image | Elements (2–4 img) | depends |
| Native audio | no (silent) | yes | depends |
| 9:16 | yes | yes | yes |
| Cost | $0.05/s (720p), $0.08/s (1080p) | ~$0.14/5s std 720p | per model |

## 4. Safety & honesty
- `disabled` is the default; a missing key raises `BackendNotConfigured`.
- Real keys only via `.env` (`.env.example` documents them; source/logs/reports
  never contain secrets).
- No uncontrolled regeneration: single submit per scene, poll with timeout
  (`--poll-timeout`), cancel on timeout, persists only successful clips.
- Cost estimate is precomputed when the provider exposes a rate; otherwise
  `estimated_usd` stays `None` (never invented).

## 5. Files (new)
```
src/shortsfactory/generative/
  models.py  presets.py  planner.py  cinematic.py  references.py
  backend.py  pipeline.py  assembly.py  qc.py
  providers/{veo,kling,fal}.py
src/shortsfactory/cli.py        (+ ai-create subcommand)
.env.example  tests/test_generative_backend.py  tests/test_generative_pipeline.py
```
