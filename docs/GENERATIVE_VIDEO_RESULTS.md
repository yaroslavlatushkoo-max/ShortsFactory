# Generative Video Results (Mission 3)

Status summary: **architecture + planning implemented and verified; real
generative MP4 NOT produced** — no paid provider key is configured on this
machine. This is reported honestly per mission rules.

## 1. What was verified without a key

| Item | Evidence |
|---|---|
| Planning (story → scenes → cinematic → bibles → manifest) | `output/mission3_ai_video/` JSON artifacts from `ai-create` |
| Backend interface + providers registered | unit tests pass |
| Missing-key / disabled behavior (refuses to fake) | `test_disabled_backend_refuses_work`, CLI WARNING output |
| Cost estimation when a rate exists | `test_cost_estimate_uses_per_second` |
| Timeout handling | `test_timeout_raises` |
| Malformed/no-video structures | module-level guards + tests |
| Assembly (concat + audio mix) | synthetic-clip self-check → final MP4 |
| QC (decode, flicker, continuity, contact sheet) | synthetic-clip self-check |

## 2. Recorded run (no provider)
```
shortsfactory ai-create --script "A young fox named Milo…" \
    --mode generative --provider disabled --output output/mission3_ai_video
=> status WARNING, ran_generative=false, video="" (no fake)
```

## 3. Comparison set (Mission 3 §12)
- **Procedural pilot:** existing `output/integrated_pilot/factory/pilot.mp4`
  (9.2 s, 275 frames, WARNING status) and prior pilots.
- **Generative pilot:** not produced — no provider key. A real MP4 will appear
  here the first time a key is configured and `ai-create` runs successfully.
- **Hybrid pilot:** same constraint (needs generative clips).

Because visual quality needs human judgment, contact sheets + videos are the
comparison medium; we do not invent numeric quality scores.

## 4. What a full generative run will create
`output/mission3_ai_video/`: script.txt, scenes.json, cinematic_plan.json,
character_bible.json, environment_bible.json, visual_style_bible.json,
scene_continuity_manifest.json, clips/*.mp4, final MP4, qc/contact_sheet.jpg,
qc.json, engineCheck artifacts, time/cost stats.
