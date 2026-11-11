# Generative Video Setup (Mission 3)

This document explains how to configure and run the generative/hybrid modes,
and what depends on a paid provider key.

## 1. What you need (per provider)

| Provider | Env keys (in `.env`) | Notes |
|---|---|---|
| `disabled` (default) | none | No generative video possible; planning artifacts only. |
| `veo` (Gemini) | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Veo 3.1 Lite on the Gemini API. Paid. 9:16. Silent video (audio mixed by us). |
| `kling` (official API) | `KLING_ACCESS_KEY`, `KLING_SECRET_KEY` | Kling v3, first/last frame + Elements for character consistency. Paid. |
| `fal` (aggregator) | `FAL_KEY` (or `FAL_API_KEY`), optional `SHORTSFACTORY_FAL_MODEL` | Generic queue client for hosted models (default Kling v3 t2v). Paid. |

Set the active provider:
```
SHORTSFACTORY_VIDEO_PROVIDER=veo
```

## 2. Steps
1. Copy the template: `copy .env.example .env` (never commit `.env`).
2. Fill only the key for the provider you will use.
3. Run:
   ```
   shortsfactory ai-create --script "my story.txt" --preset cinematic3d \
       --mode generative --provider veo --output output/mission3_ai_video
   ```
4. If no key is set, the command returns a WARNING and writes the planning
   artifacts (scenes, cinematic plan, bibles, manifest) — it does **not**
   fabricate a video.

## 3. Cost control
- `--poll-timeout` caps waiting per clip (default 600 s).
- `--poll-interval` sets polling cadence (default 5 s).
- A cost estimate is printed when the provider exposes a rate; otherwise it is
  not printed (we never invent pricing).
- There is one submit per scene; no automatic regeneration loop.

## 4. Hardware
- Local open-weight video generation is not feasible on this machine
  (RTX 3050 Laptop, 4 GB VRAM). All generative modes go through a cloud API.
- The `procedural` mode works fully offline and is untouched.

## 5. Verification
Before a first paid run, validate the exact request shape for the chosen
provider against its official docs — the adapters centralize request bodies in
single methods (`_build_payload`/`submit`) so a field correction is one edit.
The end-to-end *assembly + QC* stages are verified with synthetic clips in
`output/mission3_ai_video/engine_check` (no key required).
