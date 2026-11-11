# AI Provider Limitations (ShortsFactory — Mission 4)

## 1. Status honesty

- No cloud key is present on this machine. Therefore **no real generative video
  was produced** in Mission 4. All cloud providers (gemini, openai_compat, veo,
  kling, fal, elevenlabs) are `IMPLEMENTED_UNVERIFIED`.
- `ai-create --mode generative` without a configured provider returns `WARNING`
  and writes planning artifacts — it never fakes a video. `--dry-run` returns
  `PLANNED`.
- Local fallback for text/planning (`ollama`) and voice (`piper`) is configured
  and verified. There is **no free local video option** on the 4-GB-GPU laptop.

## 2. Known verification gaps

- **Kling** now uses the modern single **API Key** (`Authorization: Bearer <key>`)
  when `KLING_API_KEY`/`KLINGAI_API_KEY` is set; the legacy AK/SK JWT signing is
  kept only as a fallback. Neither scheme has been field-verified without a live
  key (mock tests only cover header selection/configuration, not real API success).
- **Veo** uses the Gemini `generateContent` REST API; the model id
  (`veo-3.1-lite-generate-001`) is per current docs, but playback of inline video
  bytes vs GCS URIs should be confirmed on first real call.
- **ElevenLabs** pricing/voice availability is unverified.
- Regional availability of each API in the user's region is not verified (some
  Google/Kling endpoints may be geo-restricted).

## 3. Environment / hardware

- Local video generation is infeasible (RTX 3050 Laptop, 4 GB VRAM). Cloud or a
  24 GB+ GPU is required for real generative clips.
- The generative path needs internet. Procedural mode works fully offline.

## 4. Semantics

- "Key present" ≠ "key valid"; "API reachable" ≠ "generation works".
  `providers-check` states exactly this and does not run paid checks without
  explicit `--allow-paid-cost`.
- A shared credential (e.g. one `GEMINI_API_KEY` for both Gemini and Veo) only
  means the key is *present* for both; it does not imply either service accepts
  it or that either works.
- No provider generation is ever auto-run just because a key exists.

## 5. Guarantees we do not make

- No guarantee of cross-scene character identity without reference-image /
  conditioning support (see `scene_continuity_manifest.json` →
  `continuity.identity_guarantee`).
- Cost estimates are estimates, not invoices.

## 6. Kling API contract verification (as of 2026-10-27)

The official doc pages (`kling.ai/document-api/...`) are a JS-rendered SPA, so
their content could not be extracted statically. The table below records what
was confirmed from independent mirrors / SDK catalogs vs what remains
UNVERIFIED against the live API.

| Contract item | Status | Source / note |
|---|---|---|
| Modern auth: single API Key, `Authorization: Bearer <key>` | CONFIRMED (documented) | official quick-start snippet; ai-sdk.dev klingai provider; wireflow |
| Legacy auth: AK/SK JWT (HMAC-SHA256) | CONFIRMED as legacy | community/third-party docs; kept as fallback only |
| Model IDs `kling-v3.0-t2v` / `kling-v3.0-i2v` | CONFIRMED as valid model IDs (documented) | ai-sdk.dev catalog; aggregator playgrounds |
| Endpoint paths `/v1/videos/text2video` / `image2video`, poll `/v1/videos/{id}` | DOCUMENTED VIA MIRROR, UNVERIFIED vs live | useapi.net v1 reference; official SPA not directly extractable |
| Request body: flat `{model_name, prompt, negative_prompt, cfg_scale, mode, aspect_ratio, duration}` | UNVERIFIED | some newer Kling endpoints nest fields under an `input` object; conflict not resolved |
| `negative_prompt` support for model 3.0 | UNVERIFIED (risk) | one aggregator note claims 2.5/2.6/3.0 do NOT accept it — needs live check |
| Duration range 3–15s, `9:16` | DOCUMENTED (prior research), UNVERIFIED live | vendor docs |
| Response: `task_id` + status `SUCCEED`/`FAILED` | DOCUMENTED VIA MIRROR, UNVERIFIED live | useapi.net / maze docs |

Because the request body format could not be confirmed from the official page,
the Kling adapter's flat-body contract remains UNVERIFIED until a live call
with a real key is performed under `--allow-paid-cost`.
