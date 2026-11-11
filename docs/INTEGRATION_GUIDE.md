# Integration guide

## What works

`integrated-create` accepts a short Russian script and runs the real pipeline:

```
text -> Story Studio Project -> Animation Lab MotionPlan -> Factory storyboard
     -> Factory Animation Engine -> MP4 + contact sheet + QC report
```

The renderer is the existing ShortsFactory Animation Engine v4 with the artistic
Milo/Nina characters and the orb. The output is a real H.264/AAC MP4, not a Lab
diagnostic simulation.

## Requirements

- Windows.
- ShortsFactory virtual environment:
  `D:\ShortsFactory\.venv\Scripts\python.exe`
- Story Studio at:
  `D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio`
- Animation Intelligence Lab at:
  `D:\Animation_Intelligence_Lab`
- A system Python with Pydantic for Story Studio. By default the adapter looks
  for `py -3.12` or `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
- The Lab virtual environment:
  `D:\Animation_Intelligence_Lab\.venv\Scripts\python.exe`
- `ffmpeg` and `ffprobe` on `PATH` for the Factory verification steps.

No network access or paid API is required.

## Run

From `D:\ShortsFactory`:

```powershell
.\.venv\Scripts\python.exe -m shortsfactory integrated-create `
  --script "examples\handoff_story.txt" `
  --output "output\integrated_pilot"
```

Use `--title "..."` to override the title. Existing non-empty output folders are
refused; add `--overwrite` only for an integration folder you own.

`--json` prints the machine-readable result summary.

Exit codes:

- `0` — QC `PASS` or `WARNING` (the video exists);
- `3` — QC `FAIL`;
- `2` — integration/configuration/validation error.

## Output layout

```
output/integrated_pilot/
  input/
    story.txt                  original text
  story/
    story_spec.json            intermediate text -> Story Studio spec
    project.json               validated Story Studio Project
    validation.json            Story Studio validation summary
  motion/
    lab_scene.json             Lab SceneDescription
    motion_plan.json           Lab MotionPlan (segments, keyframes, interactions)
    validation.json            Lab validation report
  factory/
    storyboard.source.json     Factory storyboard v1
    scene.compiled.json        Factory scene v2
    pilot.mp4                  real rendered video
    contact_sheet.jpg          visual verification
    report.json                Factory render report
    report.md                  Factory readable report
    characters.json
    pilot_scene.json
    frames/
    audio/
  report.json                  combined integration QC
  report.md                    readable integration QC
```

The final video and contact sheet are always referenced from `report.json`; do
not hard-code `pilot.mp4` if the engine changes its file naming.

## Component overrides

Set these environment variables when the default sibling layout is different:

```powershell
$env:SHORTSFACTORY_STORY_STUDIO = "D:\path\to\ShortsFactory_StoryStudio"
$env:SHORTSFACTORY_ANIMATION_LAB = "D:\path\to\Animation_Intelligence_Lab"
$env:SHORTSFACTORY_STORY_PYTHON = "C:\path\to\python.exe"
$env:SHORTSFACTORY_LAB_PYTHON = "D:\path\to\Animation_Intelligence_Lab\.venv\Scripts\python.exe"
```

The path passed to `--script` and `--output` is resolved by Python, so spaces in
Windows paths are handled without manual quoting inside the configuration.

## Supported input subset

The text parser is rule-based and intentionally narrow. The supported demo
vocabulary is:

- characters: `Мило` / `Milo`, `Нина` / `Nina`;
- prop: `светящийся шар`, `шар`, `сфера`, `orb`;
- actions: notice/look, approach, take, hand over, surprise, thanks, leave.

The example `examples\handoff_story.txt` uses the complete supported path. Other
names or unsupported verbs raise a clear `IntegrationError`; the system does not
silently invent characters or actions.

## Verification

Targeted integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_integration.py -q
```

Existing Factory tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Video check:

```powershell
ffprobe -v error -show_entries format=duration,size `
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate `
  -of json "output\integrated_pilot\factory\pilot.mp4"

ffmpeg -v error -i "output\integrated_pilot\factory\pilot.mp4" -f null -
```

The Factory engine already performs full decode, determinism, motion,
attachment, framing, pose-quality and lip-sync checks. The integration QC adds
Story and Lab validation and preserves their statuses, including `NOT_CHECKED`.

## Reading the QC report

`report.json` checks use exactly:

- `PASS` — the check ran and passed;
- `WARNING` — the check ran and found a non-fatal issue;
- `FAIL` — the check ran and failed;
- `NOT_CHECKED` — no reliable check was performed.

A report containing `NOT_CHECKED` is overall `WARNING`, never `PASS`. The two
current `NOT_CHECKED` items are silhouette intersections and forwarding of
curved Lab trajectories; see `INTEGRATION_LIMITATIONS.md`.
