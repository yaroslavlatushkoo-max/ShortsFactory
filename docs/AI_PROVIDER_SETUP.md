# AI Provider Setup (ShortsFactory — Mission 4)

## 1. Quick start

Check what is registered and what is configured (no keys are printed):

```powershell
cd D:\ShortsFactory
$env:PYTHONPATH='src'
python -m shortsfactory.cli providers
python -m shortsfactory.cli providers-check
```

`providers-check` runs only **free** checks by default. Cloud-key/API-validity
checks can cost money, so they are skipped unless you pass `--allow-paid-cost`
(explicit permission). The command prints `OK` / `?` (not run / costs money) and
exits non-zero when not everything is OK.

## 2. Environment variables (`.env`)

Copy the template and edit it **yourself** — never paste keys into chat:

```powershell
copy .env.example .env
notepad .env
```

Never commit a real `.env`. The template `.env.example` contains no real keys.

### Text providers
| Var | Purpose |
|---|---|
| `GEMINI_TEXT_MODEL` | Gemini model for text/structured planning (default `gemini-2.0-flash`) |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Gemini auth |
| `OPENAI_BASE_URL` | Custom base (default `https://api.openai.com/v1`) |
| `OPENAI_API_KEY` | OpenAI-compatible auth (required only for api.openai.com) |
| `OPENAI_MODEL` | Model (default `gpt-4o-mini`) |
| `OLLAMA_BASE_URL` | Ollama base (default `http://127.0.0.1:11434`) |
| `OLLAMA_TEXT_MODEL` | Preferred Ollama model |

### Video providers
| Var | Purpose |
|---|---|
| `SHORTSFACTORY_VIDEO_PROVIDER` | `disabled` (default), `veo`, `kling` or `fal` |
| `GEMINI_VIDEO_MODEL` | Veo model id (default `veo-3.1-lite-generate-001`) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Veo auth (Gemini) |
| `KLING_API_KEY` | Kling **API Key** (Bearer token) — recommended, created at https://kling.ai/dev/api-key |
| `KLINGAI_API_KEY` | Alias of the above (official SDK env name) |
| `KLING_ACCESS_KEY`, `KLING_SECRET_KEY` | Legacy AK/SK pair (signed JWT) — only used when no `KLING_API_KEY` |
| `KLING_BASE_URL` | Optional API base override (default `https://api.klingai.com`) |
| `KLING_TEXT_MODEL`, `KLING_IMAGE_MODEL` | Optional model overrides (defaults `kling-v3.0-t2v` / `kling-v3.0-i2v`) |
| `FAL_KEY` / `FAL_API_KEY` | fal.ai auth |
| `SHORTSFACTORY_FAL_MODEL` | fal model (default `fal-ai/kling-video/v3/standard/text-to-video`) |

### Voice providers
| Var | Purpose |
|---|---|
| `ELEVENLABS_API_KEY` | ElevenLabs auth |
| `ELEVENLABS_VOICE_ID` | Voice id (default `21m00Tcm4TlvDq8ikWAM`) |

All cloud providers are paid. The only free options are local: `ollama`
(text/planning) and `piper` (voice). Video has **no free local option** on this
4-GB-GPU machine.

## 3. First run (before spending anything)

Always start with a dry run — it plans everything and shows estimated cost (or
states that no tariff is known), and sends **zero** paid requests:

```powershell
python -m shortsfactory.cli ai-create --script output\mission3_ai_video\story_demo.txt `
  --mode generative --provider disabled --dry-run `
  --output output\mission4_ai_video\dry_run
```

Then, to actually generate video, configure a key in `.env`, pick a provider, and
re-run without `--dry-run` (you will be prompted/expected to explicitly allow the
paid run):

```powershell
python -m shortsfactory.cli ai-create --script output\mission3_ai_video\story_demo.txt `
  --mode generative --video-provider veo `
  --output output\mission4_ai_video\veo_pilot
```

You can also choose per-stage providers:

```powershell
python -m shortsfactory.cli ai-create --script story.txt `
  --text-provider ollama --voice-provider piper --video-provider kling `
  --mode generative --output output\mission4_ai_video\hybrid_pilot
```

## 4. Cost controls

- `--dry-run` — plan only, no paid calls, prints estimated cost or "tariff
  unknown".
- `--max-generations N` — cap the number of video generations (0 = no cap).
- Resumable clips — completed scenes are cached; re-runs don't re-pay for them.
- No automatic cloud generation just because a key exists — a run must be
  explicitly invoked.

## 5. Notes

- `.env` is loaded at CLI and pipeline startup. **Real exported environment
  variables take precedence** over `.env` content. Only variables not already
  in the environment are taken from `.env`.
- One `GEMINI_API_KEY` covers **both** Gemini text planning and Veo video — they
  are two providers but share the Google credential.
- `OLLAMA_TEXT_MODEL` overrides the preferred local model for text planning.
- `providers` shows `настроен` (configured) based on key presence; this is **not**
  key validity.
- `providers-check` distinguishes "key present" from "key valid" and "API
  available" from "generation works". Paid verification is opt-in only.

## 7. Kling: model choice and cost estimates

- `--video-model kling-v3.0-t2v` overrides the text-to-video model and **beats**
  the `KLING_TEXT_MODEL` env var. If `--video-model` is omitted, the model is
  `KLING_TEXT_MODEL` (env) or the default `kling-v3.0-t2v`.
- The image-to-video path is independent: it always uses `KLING_IMAGE_MODEL`
  (env) or `kling-v3.0-i2v` — the CLI `--video-model` is never sent to the i2v
  endpoint, so a t2v model cannot accidentally hit it.
- Cost output shows **two** figures:
  - `Оценка стоимости (полный план)` — the whole plan (before `--max-generations`);
  - `Оценка стоимости (с лимитом)` — only the specs within the cap, and already
    cached clips (existing non-empty `clips/scene_*.mp4`) are excluded, so this
    is the honest estimate of what *this run* will pay.
- `--dry-run` plans and prints both estimates and sends **no** generation
  request (verified by mock tests).

## 6. Choosing a video model and reading cost estimates

**Model selection (Kling):**
- `--video-model <id>` (e.g. `kling-v3.0-t2v`) overrides the **text-to-video**
  model and wins over `KLING_TEXT_MODEL`.
- Image-to-video keeps its own model: `KLING_IMAGE_MODEL` env (default
  `kling-v3.0-i2v`). The CLI model override never leaks into the image-to-video
  endpoint.
- If `--video-model` is omitted, the env vars (`KLING_TEXT_MODEL` /
  `KLING_IMAGE_MODEL`) are used; otherwise built-in defaults apply.
- Endpoint choice is automatic: `text2video` when a scene has no reference
  image, `image2video` when it carries reference images.

**Cost estimates (two lines in `ai-create` output):**
- `Оценка стоимости (полный план)` — the whole planned scene set (before any
  `--max-generations` cap).
- `Оценка стоимости (с лимитом)` — only the scenes that would actually be
  generated under the active cap (`--max-generations`). When no cap applies,
  both lines show the same figure; when the estimate cannot be computed for the
  limited slice, it prints `н/д` rather than a made-up number.
- Estimates are informational; if a provider has no known tariff, both lines are
  omitted and the notes say the cost cannot be estimated.
