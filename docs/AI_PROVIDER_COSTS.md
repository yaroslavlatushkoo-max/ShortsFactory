# AI Provider Costs (ShortsFactory — Mission 4)

The numbers below come from vendor pricing pages as researched in
`VIDEO_MODEL_RESEARCH.md` (2026-10-27). They are **informational** and subject to
change; the code never invents or hard-codes live billing. A cost is recorded in
a manifest only when a provider reports a known tariff.

## 1. Video providers (paid)

| Provider/Model | Format | Price (as researched) | Notes |
|---|---|---|---|
| Veo 3.1 Lite (Gemini) | 720p | $0.05/s | silent output, 4/6/8s, 9:16, image-to-video |
| Veo 3.1 Lite | 1080p | $0.08/s | same |
| Kling 3.0 Standard | 720p | ~$0.14 / 5s | native audio; first/last frame; character Elements |
| Kling 3.0 Pro | 1080p | ~$0.28 / 5s | same |
| fal.ai (aggregator) | depends on model | varies | single submit/poll pattern |
| Wan 3.0 (via API) | — | ~$12/min (leaderboard) | expensive; not recommended here |

## 2. Text providers

| Provider | Model | Pricing |
|---|---|---|
| Ollama (local) | qwen3:4b, qwen2.5:7b, … | free |
| Gemini | gemini-2.0-flash | paid (per-token) — verify current tier |
| OpenAI-compatible | gpt-4o-mini etc. | paid (per-token); depends on endpoint |

## 3. Voice providers

| Provider | Pricing |
|---|---|
| Piper (local) | free |
| ElevenLabs | paid, pay-per-character — pricing not verified at writing time; treat as unverified |

## 4. Cost-control mechanisms

- `--dry-run`: plans, prints a cost estimate when the tariff is known (else
  states "tariff unknown"), sends nothing.
- `--max-generations N`: hard cap on video generations (0 = no cap).
- Resumable cached clips: a re-run reuses already-generated scene files without
  re-paying.
- No auto-run: a cloud key's presence alone never triggers a paid request; a
  run must be explicitly invoked by the user.
- `providers-check --allow-paid-cost`: the only path that may spend money, and
  only when the user explicitly passes the flag.

Example: a 5-scene pilot with Veo 3.1 Lite, 5s 720p each = 25s × $0.05 =
**~$1.25** (video only; audio via Piper free). With Kling Standard 5×5s =
5 × ~$0.14 ≈ **~$0.70**. These are estimates, not guarantees.
