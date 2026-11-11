# Video Model Research (Mission 3)

Research date: 2026-10-27. Method: current web search over vendor docs (Google AI,
Kling, Wan, LTX, Artificial Analysis leaderboards) — no invented endpoints or
pricing. Line items state explicitly whether a fact is verified from an official
source or reported by a third-party aggregator.

## 1. Hardware constraint (measured)

| Resource | Measured | Consequence |
|---|---|---|
| GPU | NVIDIA GeForce RTX 3050 Laptop, 4 GB VRAM (WDDM) | No modern open-weight video model fits comfortably |
| Host RAM | 16.8 GB | Below the 32 GB recommended minimum for Wan-style pipelines |
| Free disk | D: 76.7 GB, C: 63 GB | Enough for weights but not the bottleneck |
| CUDA | 13.4 (driver) | Local PyTorch inference possible for small models only |

**Conclusion: full local video generation is not feasible on this machine.**
Representative reported requirements: Wan 2.2 14B needs ~24 GB VRAM for 720p
(~14–16 GB with CPU offload); LTX-2 official minimum ~32 GB (≈12–24 GB FP8);
HunyuanVideo 13B requires a 24 GB+ card. A 4 GB laptop GPU is firmly out of
range. This is recorded so the wrapper never pretends local generation works.

## 2. Candidate classes and models

### 2.1 Open-weight (local) — verified requirements

| Model | Developer | Variants | Verified VRAM req | T2V/I2V | Notes |
|---|---|---|---|---|---|
| Wan 2.2 | Alibaba | 1.3B / 5B / 14B MoE | 14B: 24 GB (720p), ~14–16 GB offload; 5B: ~8–12 GB quantized | both | Apache-2.0, I2V anchors first frame; 720p/24fps max |
| LTX-2.x | Lightricks | 22B | official min ~32 GB; ~12–24 GB FP8 | both | camera motion, last-frame control, API exists |
| HunyuanVideo | Tencent | 8.3B / 13B | 24 GB+ | both | |
| Mochi / CogVideoX | — | — | CogVideoX 5B ~8 GB | both | lower ceiling |

Local verdict: not usable on 4 GB. Would require a different machine (24 GB+).

### 2.2 Commercial/cloud APIs — verified access & pricing

| Model | Provider | Access | T2V/I2V | First/Last frame | Character ref | Native audio | Portrait 9:16 | Pricing (verified) |
|---|---|---|---|---|---|---|---|---|
| Veo 3.1 Lite | Google (Gemini API) | API (key) | both | — (I2V start frame) | reference image | No (silent) | yes | $0.05/s 720p, $0.08/s 1080p; 4/6/8s; 20 MB max image |
| Veo 3.1 Fast/Standard | Google | API | both | — | reference image | Yes | yes | Fast $0.10–0.12/s; Standard $0.40/s |
| Kling 3.0 | Kling AI (+fal/Muapi) | API | both | **yes** (first+last) | **Elements from 2–4 images** | Yes | yes | ~$0.14/5s Standard 720p, $0.28/5s Pro 1080p; multi-shot up to 6 shots |
| Sora 2 Pro | OpenAI | API | both | limited | limited | yes | yes | premium (Elo 1367) |
| Wan 3.0 | Alibaba API | API | both | — | — | yes | yes | $12/min (leaderboard, expensive) |
| Aggregators | fal.ai / Replicate / Muapi | API | varies | varies | varies | varies | varies | single-submit-and-poll pattern |

All of the above are paid APIs; none has a free tier for this use case.
The Gemini API key and Kling key are **not present** on this machine today.

## 3. Decision

- **Primary backend (recommended default):** *Kling 3.0* — native first/last-frame
  control and k Elements (2–4 character reference images) give the strongest
  out-of-the-box cross-scene character consistency, plus native audio and 9:16.
  Access via the official Kling API or an aggregator (fal.ai) behind a single
  interface.
- **Backup backend:** *Veo 3.1 Lite* on the Gemini API — cheapest per second,
  image-to-video with a reference image, 9:16 portrait, deterministic 4/6/8 s
  clips. Good for high-volume short clips; silent output (audio mixed later).
- **Local option:** define the interface + adapter, but mark **not runnable on
  this 4 GB GPU**. Document the real requirement (24 GB+ / A100 etc.) and leave
  the adapter dormant.
- **Long-term architecture:** provider-agnostic `VideoBackend` interface
  (submit/poll/result/cancel/errors), with per-provider adapters and config
  selection. The pipeline never hard-codes one API.

## 4. Capability mapping to the mission

| Needed control | Kling 3.0 | Veo 3.1 Lite |
|---|---|---|
| Story → text prompt | yes | yes |
| Image-to-video (animate a character frame) | yes | yes |
| First/last frame keyframing | yes | no |
| Character consistency across scenes | Elements (refs) | reference image per clip |
| Camera motion control | yes (model-dependent) | prompt-only |
| Vertical 9:16 | yes | yes |
| Native audio | yes | no (mix later) |

This table is what the backend adapter contract is built around, not a promise of
guaranteed identity across clips (see LIMITATIONS doc).
