# Animation Quality Guide

## Prerequisites

The three projects stay in their existing directories:

- ShortsFactory: `D:\ShortsFactory`
- Story Studio: `D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio`
- Animation Intelligence Lab: `D:\Animation_Intelligence_Lab`

The integration does not install packages. It uses each component's native
environment through subprocess workers:

| Component | Interpreter | Why |
|---|---|---|
| ShortsFactory orchestration/render | `D:\ShortsFactory\.venv\Scripts\python.exe` | Factory dependencies, Pillow, Piper, ffmpeg |
| Story Studio | system Python 3.12 with pydantic | no virtualenv exists in Story Studio |
| Animation Lab | `D:\Animation_Intelligence_Lab\.venv\Scripts\python.exe` | Lab models and validator |

If paths differ, override them:

```powershell
$env:SHORTSFACTORY_STORY_STUDIO = "D:\path\to\ShortsFactory_StoryStudio"
$env:SHORTSFACTORY_ANIMATION_LAB = "D:\path\to\Animation_Intelligence_Lab"
$env:SHORTSFACTORY_STORY_PYTHON = "C:\Path\Python312\python.exe"
$env:SHORTSFACTORY_LAB_PYTHON = "D:\Animation_Intelligence_Lab\.venv\Scripts\python.exe"
```

## Run one film

From `D:\ShortsFactory`:

```powershell
.\.venv\Scripts\python.exe -m shortsfactory integrated-create `
  --script "examples\handoff_story.txt" `
  --output "output\animation_intelligence_upgrade\01_handoff" `
  --overwrite --json
```

`--overwrite` is required only when the target directory already contains
files. The command returns JSON with output paths. Exit code is `0` for
`PASS`/`WARNING`, `3` for `FAIL`, and `2` for an integration error.

## What is produced

```
output/<run>/
  input/story.txt
  story/story_spec.json
  story/project.json
  story/validation.json
  motion/lab_scene.json
  motion/motion_plan.json
  motion/validation.json
  motion/trajectory_metrics.json
  factory/storyboard.source.json
  factory/scene.compiled.json
  factory/pilot.mp4
  factory/pilot_scene.json
  factory/audio/pilot_audio.wav
  factory/contact_sheet.jpg
  factory/diagnostics/*.png
  factory/report.json
  factory/report.md
  report.json
  report.md
```

## Motion profiles

The parser chooses a profile from explicit motion words:

| Words in script | Profile |
|---|---|
| `по дуге`, `дугой`, `arc`, `полукруг` | `arc` |
| `зигзаг`, `через стол`, `по точкам`, `waypoint`, `обходя` | `waypoints` |
| `плавно`, `кривой`, `curve`, `bezier`, `огибая` | `bezier3` |
| otherwise | `linear` |

The profile is recorded in `report.json` under `input.motion_profile` and in
`motion/trajectory_metrics.json`. Paths are evaluated in the renderer, not only
stored as endpoint metadata.

## QC statuses

- `PASS`: measured and within threshold.
- `WARNING`: measured with non-critical issue or intentional contact context.
- `FAIL`: measured and outside threshold; render exit code is `3`.
- `NOT_CHECKED`: no reliable measurement was performed. It is not a pass.

`silhouette_intersections` is measured from rendered alpha masks at six samples
per second. `lip_sync` is `NOT_CHECKED` for a silent film. `trajectory_integration`
checks that every moving Lab segment reached a Factory action path.

## Diagnostics

- `factory/report.json` contains the Factory frame/hand/object checks.
- `factory/diagnostics/` contains rendered overlap frames (up to 20).
- `report.json` contains per-check status, message, details, and artifact paths.
- `report.md` is a readable summary for a human review.

## Tests

```powershell
# targeted integration
D:\ShortsFactory\.venv\Scripts\python.exe -m pytest tests\test_integration.py -q

# full Factory suite
D:\ShortsFactory\.venv\Scripts\python.exe -m pytest -q

# Story Studio suite
$env:PYTHONPATH = "D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio\src"
python -m pytest "D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio\tests" -q

# Animation Lab suite
D:\Animation_Intelligence_Lab\.venv\Scripts\python.exe -m pytest "D:\Animation_Intelligence_Lab\tests" -q
```

## Troubleshooting

- `Worker ... завершился` usually means the selected Python lacks pydantic or the
  Lab virtualenv is missing. Set the interpreter overrides above.
- `Story Studio отклонил сценарий` means semantic validation failed; read
  `story/validation.json`.
- `motion=FAIL` means the rendered film had a sampled pair with no visible
  movement. Add a visible body action; do not lower Factory thresholds.
- `attachment=False` means rendered hand/object distance or a transfer jump is
  outside the existing Factory threshold. Inspect `factory/report.json`.
- Existing outputs are never silently replaced; choose a new `--output` or pass
  `--overwrite` deliberately.
