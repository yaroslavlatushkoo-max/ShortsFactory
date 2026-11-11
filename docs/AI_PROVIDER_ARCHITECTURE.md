# AI Provider Architecture (ShortsFactory — Mission 4)

Status: implemented and unit-tested. Cloud providers are `IMPLEMENTED_UNVERIFIED`
(no live API call was made — no keys present on this machine).

## 1. Principles

- **Providers are replaceable tools, not the program.** The pipeline orchestrates
  capabilities; each provider is a plug-in behind a common interface.
- **Capability discovery, not guessing.** A provider advertises what it can do,
  the models it offers, its inputs, constraints, key requirements, whether it is
  paid, and whether an availability check is free. Nothing is invented.
- **No forced LLM.** Planning falls back to a deterministic rule-based planner
  when no text provider is configured/available.
- **Compatibility.** The Mission 3 `backend.py` `VideoBackend` interface is
  preserved; it is now also surfaced in the registry via a wrapper.

## 2. Registry (`src/shortsfactory/generative/registry.py`)

`Capability` enum: `TEXT_GENERATION`, `STORY_PLANNING`, `STRUCTURED_OUTPUT`,
`IMAGE_GENERATION`, `VIDEO_GENERATION`, `IMAGE_TO_VIDEO`, `TEXT_TO_SPEECH`,
`SPEECH_TO_TEXT`, `VIDEO_ANALYSIS`. Label map: `CAPABILITY_LABELS`.

`AIProvider` base class exposes:
- identity: `id`, `display_name`, `provider_type` (`local`/`cloud`), `paid`;
- keys: `key_env_names` (env-var names, never values), `is_configured()`,
  `_configured_key_name()` (returns the name, not the value);
- capabilities: `supported_capabilities`, `capability_reports()`,
  `models_for(cap)`, `constraints_for(cap)`, `fee_free_check(cap)`;
- availability: `check_availability()` → `AvailabilityResult` (config-mode by
  default; local providers may do a free api-mode probe);
- `describe()` → dict that **never** contains secret values.

Sub-interfaces:
- `TextProvider`: `generate_text(...)`, `generate_structured(..., schema, attempts,
  on_error)` — JSON extraction + schema validation + bounded retries with backoff,
  raises `ValueError` after attempts.
- `VoiceProvider`: `synthesize(text, *, voice, lang, out_path, params)` →
  `(Path, meta_dict)`.

`ProviderRegistry` provides:
- `register_provider(instance)` / `@register_provider` class decorator;
- `list_providers()`, `get_provider(id)`, `describe_all()`;
- `providers_for_capability(cap, only_configured=False)`;
- `resolve(cap, preferred='', only_configured=False)` — returns the preferred
  provider if it supports the capability (and is configured when required), else
  the first configured provider from the pool, else `None`.

### Recorded providers

| id | type | paid | capabilities | keys | configured |
|---|---|---|---|---|---|
| `gemini` | cloud | да | text_generation, story_planning, structured_output | `GEMINI_API_KEY`/`GOOGLE_API_KEY` | no |
| `ollama` | local | нет | text_generation, story_planning, structured_output | — | yes (when server up) |
| `openai_compat` | cloud | да | text_generation, story_planning, structured_output | `OPENAI_API_KEY` (`OPENAI_BASE_URL`) | no |
| `veo` | cloud | да | video_generation, image_to_video | `GEMINI_API_KEY`/`GOOGLE_API_KEY` | no |
| `kling` | cloud | да | video_generation, image_to_video | `KLING_API_KEY`/`KLINGAI_API_KEY` (modern Bearer) or `KLING_ACCESS_KEY`,`KLING_SECRET_KEY` (legacy) | no |
| `fal` | cloud | да | video_generation, image_to_video | `FAL_KEY`/`FAL_API_KEY` | no |
| `piper` | local | нет | text_to_speech | — | yes |
| `elevenlabs` | cloud | да | text_to_speech | `ELEVENLABS_API_KEY` | no |

## 3. Secret safety

- Providers read credentials via a **live** lookup `_env_value(key)` that checks
  the instance's own `env` snapshot first, then the current `os.environ`. This
  fixes the earlier bug where `.env` keys loaded late (by video backends) were
  invisible to text providers created earlier at import time.
- `mask_secrets(text, env)` redacts any env-variable value whose name contains
  `KEY`/`TOKEN`/`SECRET` or is in an explicit always-secret set
  (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `KLING_*`, `FAL_*`, `ELEVENLABS_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `REPLICATE_API_TOKEN`,
  `RUNPOD_API_KEY`, `HF_TOKEN`, `HUGGINGFACE_TOKEN`).
- `describe()`, manifests, reports, logs and CLI output never contain key values
  — only env-var names.

## 4. HTTP/retry (`http_utils.py`)

`request_with_retry(...)`: retries with exponential backoff on 429 (honors
`Retry-After`, capped) and 5xx/network errors; 401/403 raise `ApiAuthError`
immediately (never retried); other ≥400 raise `ApiTransportError`; exhausted rate
limits raise `ApiRateLimitedError`.

## 5. Video backend integration

`video.py` wraps each existing `VideoBackend` (Mission 3) in a
`VideoBackendAIProvider`, mapping backend capabilities
(`supports_t2v`→`VIDEO_GENERATION`, `supports_image_to_video`→`IMAGE_TO_VIDEO`)
and exposing constraints (max duration, resolutions, aspects, native audio,
camera control, character ref, cost/sec). `check_availability()` is config-mode
only — it **never** sends a paid request.

## 6. Pipeline orchestration (`pipeline.py`)

`run_ai_create(script, out_dir, config, *, preset_id, mode, provider,
video_provider, video_model, text_provider, text_model, voice_provider, dry_run,
max_generations, with_audio, poll_interval, poll_timeout, overwrite)`.

- Story planning: requested text provider → any configured text provider →
  local Ollama → deterministic fallback.
- Cinematic + references: deterministic (as in Mission 3).
- Video: backend from registry; cost estimate; bounded by `max_generations`
  (0 = no cap); resumable clips (existing non-empty clip files are reused).
- Voice: requested voice provider → Piper local fallback; else skip.
- Assembly + QC: unchanged (`assembly.py`, `qc.py`).
- Manifest: stages (provider/model/path), providers (text/video/voice), limits
  (`dry_run`, `planned_generations`, `max_generations`), cost (only when tariff
  known, else `estimated_usd: null`), timestamps. No secrets.

## 7. Modes

- `procedural` — unchanged legacy renderer (integration pipeline).
- `generative` — video backend required; if unconfigured returns `WARNING`
  (no fake output), `PLANNED` under `--dry-run`.
- `hybrid` — generative video with local planning/audio/QC (same code path as
  generative; "hybrid" is the default intent when mixing local planning with a
  cloud video backend).
