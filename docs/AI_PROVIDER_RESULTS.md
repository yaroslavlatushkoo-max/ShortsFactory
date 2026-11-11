# AI Provider Results (ShortsFactory — Mission 4)

Date: 2026-10-27.

## 1. What was actually run

**No paid API was called.** No cloud key is present on this machine, so no real
cloud text/video/voice request was sent. All cloud providers are therefore
`IMPLEMENTED_UNVERIFIED` — their request/parse logic is unit-tested with mocked
HTTP, but the live API contract was not confirmed with a real key.

What IS verified locally:

| Check | Result |
|---|---|
| `python -m shortsfactory.cli providers` | lists 8 providers with correct per-provider key names, no key values leaked |
| `python -m shortsfactory.cli providers-check` | `ollama` OK (api probe, ~100 ms), `piper` OK (config); `gemini`/`veo` show "Key present" (via `GEMINI_API_KEY`, hidden); other cloud providers "No key configured"; exit 1 |
| `python -m shortsfactory.cli providers` | `gemini` and `veo` now both `настроен=True` via the same `GEMINI_API_KEY` (the Mission 4.5 discrepancy is fixed) |
| `python -m shortsfactory.cli ai-create --mode generative --provider disabled --dry-run` | status `PLANNED`, 5 scenes planned (Ollama qwen2.5:7b), no paid requests, cost "cannot be estimated" (disabled backend has no tariff) |
| `ai-create --mode generative --provider disabled` (no dry-run) | status `WARNING` (no fake generative video), planning artifacts written |
| procedural mode | unchanged, produces real 2D MP4 |
| Full test suite | passes without any key or paid call |

## 2. Registry state (`providers`)

Text/planning: `ollama` (local, configured), `gemini` (cloud, key present),
`openai_compat` (cloud). Voice: `piper` (local, configured), `elevenlabs`
(cloud). Video: `veo` (cloud, key present via `GEMINI_API_KEY`), `kling`,
`fal` (cloud, not configured). The `kling` adapter was updated to accept the
modern single `KLING_API_KEY` (Bearer token) and still supports the legacy AK/SK
pair. All cloud providers report `проверка без платы=False` (free availability
check not possible). "Key present" is **not** key validity.

## 3. Status legend

- `IMPLEMENTED_UNVERIFIED` — code + mocks + unit tests; no live key confirmed
  (all cloud providers today).
- `VERIFIED` — live behavior confirmed (Ollama, Piper, procedural renderer,
  assembly/QC on synthetic clips, planning artifacts).

When you add a key and explicitly run `--allow-paid-cost` / an actual
generation, the smoke path and real pilot can move providers to `VERIFIED`.

## 4. Where outputs go

- Default: `output/mission4_ai_video/…` (scenes/cinematic/bibles/manifest JSON,
  clips, audio, final MP4, `qc.json`, contact sheet).
- Dry-run: planning JSON + manifest with `status: PLANNED`, `video: not-run`.
