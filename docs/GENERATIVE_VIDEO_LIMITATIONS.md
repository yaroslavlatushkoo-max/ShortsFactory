# Generative Video Limitations (Mission 3)

Honest statement of what is and is not verified, and what depends on external
providers.

## 1. No real generative video yet
- The backend interface and adapters are implemented, but **no paid provider
  key is configured** on this machine. Therefore this project has **not**
  produced a generative MP4 and does not claim to have done so.
- `ai-create --mode generative|hybrid` without a key prints a WARNING and
  returns only planning artifacts. This is by design (no fake output).

## 2. Ver/ Kling request field verification pending
- The cloud adapters are written against documented API shapes. Exact request
  fields (e.g. Veo `generationConfig`, Kling signing) must be **validated
  against official docs with a test key** before the first paid run. Request
  bodies are centralized in one method per provider to make this a single edit.
- Kling JWT signing is implemented but not field-verified without a live key.

## 3. Character identity is not guaranteed
- Character bibles give consistent textual descriptions, but **text alone does
  not guarantee a consistent face across clips**. True identity requires
  backend reference images / conditioning (Veo I2V reference, Kling Elements).
  Our manifest records this; it does not overclaim.

## 4. Hardware
- Local open-weight video generation is infeasible on this machine
  (RTX 3050 Laptop, 4 GB VRAM). All generative modes require a cloud API.
- Procedural mode remains fully offline.

## 5. Lip-sync
- Audio is mixed at the timeline level. **Precise lip-sync is not claimed** —
  we do not run phoneme-level alignment.

## 6. Quality metrics
- Our QC is lightweight: decode, container/stream sanity, flicker (mean
  normalized luminance change), frame continuity, resolution consistency.
  These do **not** prove subjective visual quality; contact sheets and the
  videos themselves are for human review.

## 7. What remains procedural
- Story→2D animated video (existing `integrated-create`, procedural mode).
- TTS voice synthesis (Piper), editing, subtitles, QC infrastructure.

## 8. Summary of implemented vs planned
**Implemented:** architecture, backend interface + 3 adapters + disabled guard,
presets, planner, cinematic planning, bibles/manifest, CLI (`ai-create`),
assembly, QC, tests (17 new, full suite green), docs, `.env.example`.

**Planned / blocked:** a real generative MP4 (blocked on a paid provider key),
per-provider live field verification, and the generative/hybrid comparison
pilots.
