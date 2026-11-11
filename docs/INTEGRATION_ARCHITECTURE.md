# Architecture of the integrated pipeline

## Status

The integration is implemented as a **vertical slice** in `D:\ShortsFactory`:

```
short text
  -> Story Studio Project (Pydantic, validated)
  -> Animation Lab SceneDescription / MotionPlan (validated)
  -> ShortsFactory storyboard v1
  -> ShortsFactory scene v2
  -> real artistic render (Milo / Nina, orb)
  -> MP4 + contact sheet + integration QC report
```

The three projects remain physically separate. No source tree was copied into
another project, no renderer was reimplemented and no dependency was installed
into another project's virtual environment.

## Component ownership

| Component | Owns | Public integration point |
| --- | --- | --- |
| Story Studio `D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio` | Meaning: scenes, actions, dialogue, zones, camera plan, semantic validation | `story_studio.models.Project`, `story_studio.validation.validate_project` |
| Animation Lab `D:\Animation_Intelligence_Lab` | Kinematics: segments, interpolation, interactions, ownership, motion validation | `animation_lab.models.SceneDescription`, `motion_planner.build_plan`, `validation.validate_plan`, `exporter.plan_to_dict` |
| ShortsFactory `D:\ShortsFactory` | Artistic characters, poses, expressions, frames, voice, audio, MP4, factory QC | `animation.storyboard.compile_storyboard`, `animation.engine.AnimationEngine.render` |

The integration package in ShortsFactory is the only new owner of the glue:

```
src/shortsfactory/integration/
  contracts.py          component discovery, worker process protocol
  text_story.py         narrow rule-based text parser
  story_adapter.py      Story Studio worker client
  animation_plan.py     Story -> Lab and Lab -> Factory conversions
  factory_adapter.py    Factory storyboard compile + render
  pipeline.py           orchestration, QC aggregation, reports
  workers/
    story_studio_worker.py
    animation_lab_worker.py
```

## Process boundaries

Dependencies are deliberately not unified.

| Process | Interpreter | Why |
| --- | --- | --- |
| Orchestrator / Factory render | `D:\ShortsFactory\.venv\Scripts\python.exe` | Factory has Pillow, Piper, ffmpeg wrapper and pytest |
| Story Studio worker | system Python with `pydantic` (default `py -3.12` or `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`) | Story Studio has its own Pydantic 2 models and no venv of its own |
| Animation Lab worker | `D:\Animation_Intelligence_Lab\.venv\Scripts\python.exe` | Lab owns its Pydantic 2.14 / Typer environment |

Workers are standalone scripts. The parent sends one JSON object on stdin and
receives one JSON object on stdout. The child sets UTF-8 stdio. This is why the
Factory venv does not need `pydantic`, `story_studio` or `animation_lab`.

Overrides:

- `SHORTSFACTORY_STORY_STUDIO`
- `SHORTSFACTORY_ANIMATION_LAB`
- `SHORTSFACTORY_STORY_PYTHON`
- `SHORTSFACTORY_LAB_PYTHON`

## Data flow and conversions

### 1. Text -> Story Studio Project

`text_story.parse_story_text()` produces an intermediate **story spec**:

- characters with stable IDs `char_milo`, `char_nina`;
- prop `prop_orb`;
- location `loc_workshop` with zones `left_workbench`, `center_floor`,
  `right_shelf`, `entrance`;
- actions with exact `start_time` / `end_time`, action type, target, emotion,
  gaze target and parameters;
- dialogue line with character, text and time;
- initial and ending world state, including prop owner and `present_in_scene`.

The Story Studio worker turns that spec into the real Pydantic `Project`,
constructs `Character`, `Location`, `Prop`, `Action`, `DialogueLine`,
`CameraPlan`, `Scene` and runs `validate_project()`.

The semantic owner is Story Studio. The intermediate spec is only a transport
artifact, saved as `story/story_spec.json`.

### 2. Story Studio Project -> Lab SceneDescription

`animation_plan.build_lab_scene()` is an explicit, testable conversion:

| Story Studio | Lab |
| --- | --- |
| `char_milo` / `char_nina` | same character IDs |
| `prop_orb` | same prop ID |
| `Action.start_time/end_time` | `MotionSegment.start_time/end_time` |
| `walk` + `parameters.to_zone` | `walk` with `start_state`/`end_state` positions |
| `look_at` / `look_around` | `turn` |
| `pick_up` | `reach` segment + `pickup` interaction |
| `give` | `reach` segment + `transfer` interaction |
| `receive` | `reach` segment |
| `react` | `react` |
| `speak` | `speak` |
| `exit` | `walk` with end position at the stage entrance |
| initial actor/prop state | `SceneDescription.characters` / `props` |

Temporal gaps are filled with explicit `idle` segments. This avoids relying on
the Lab validator silently resynchronising illegal state transitions. A
`react -> speak` adjacency is shortened by 0.05 s and bridged with `idle`.

The Lab worker runs `build_plan()` and `validate_plan()`. The result contains:

- `motion_segments` (13 action segments + idle bridges);
- `interactions` (`pickup` at `pick_up.end - 0.1`, `transfer` at
  `give.start + 0.25 * give.duration`);
- `keyframes` sampled at the Lab FPS;
- `validation_report` with `pass` / `warnings` / `fail`.

The Lab's curved/waypoint trajectory representation is **not** forwarded to
Factory (see limitations).

### 3. Lab MotionPlan -> Factory storyboard v1

`build_factory_storyboard()` uses the validated plan as the source of timing and
positions:

| Lab / Story | Factory beat |
| --- | --- |
| `walk`, `run` | `walk_to`, `run_to` with `x` end position |
| `enter`, `exit` | `enter`, `exit` with `x` |
| `look_at` | `look_around` |
| `pick_up` | `take` + `attach` event, `attach_at` from Lab pickup time |
| `give` | `give` + `transfer` event, `transfer_at` from Lab transfer time |
| `receive` | `reach` |
| `react` with reaction style | `surprise` / `joy` / `confused` / `annoyed` / `react` |
| `speak` | `say` with the Story Studio dialogue text |
| character state | `characters[]` with `look: milo/nina` |
| prop state | `objects[]` with `kind: orb`, `color` |

The Factory storyboard is still version 1 and is compiled by the existing
`compile_storyboard()`. The existing renderer is not modified.

### 4. Factory scene -> MP4

`ShortsFactoryAdapter.render()` writes `storyboard.source.json`, calls
`compile_storyboard()`, writes `scene.compiled.json`, then calls
`AnimationEngine(config).render()`. The engine produces:

- `factory/pilot.mp4` (H.264 + AAC);
- `factory/contact_sheet.jpg`;
- `factory/report.json` and `factory/report.md`;
- `factory/characters.json`, `factory/pilot_scene.json`, frames and audio.

## Coordinate contract

This is the most important lossy boundary.

| Space | x | y | Meaning |
| --- | --- | --- | --- |
| Story Studio `NormalizedPoint` | 0..1 left-right | 0..1 top-bottom | scene position |
| Lab `Vec2Field` | 0..1 left-right | 0..1 top-bottom, origin top-left | stage position of the character root |
| Factory character | 0..1 left-right | **1.0 = floor line under the feet** | character ground anchor |

The adapter maps Story/Lab scene positions to Factory ground anchors with
`y = 1.0`. Horizontal positions are preserved. The renderer owns vertical body
dimensions. `animation_plan._position_for()` is the single place that maps zones
to stage x positions:

- `left_workbench` x = 0.33 (prop at 0.22);
- `center_floor` x = 0.30 (Milo) / 0.70 (Nina);
- `right_shelf` x = 0.78;
- `entrance` x = 0.16 (Milo) / 0.84 (Nina).

The wide center separation is intentional: it keeps the artistic characters
from visually overlapping and still allows the hands to meet during the transfer.
The actual transfer jump is measured by Factory (`45.71 px` for the final pilot),
not assumed from the Lab plan.

## Error handling

- Missing component / missing `src` directory -> `IntegrationError` before any
  worker starts.
- Worker non-zero exit -> `IntegrationError` with stderr tail.
- Invalid Story Studio project -> the adapter raises with up to eight
  validation messages; the failure is also written to `report.json`/`report.md`.
- Invalid Lab input or planning error -> worker exits non-zero, pipeline writes
  a failure report and does not render.
- Storyboard compile error -> `IntegrationError`; `storyboard.source.json` is
  kept for diagnosis.
- Render/decode error -> `IntegrationError`; partial output is not presented as
  success.
- Non-empty output directory -> refuse unless `--overwrite` is given.
- QC `FAIL` -> CLI exit code 3. QC `WARNING` (for example `NOT_CHECKED`) -> exit
  code 0, but the status remains `WARNING`; it is not reported as `PASS`.

## Backward compatibility

- Only a new `integrated-create` subcommand was added to the existing CLI.
- Existing commands (`create`, `animate`, `batch`, `doctor`, etc.) are untouched.
- `compile_storyboard`, `load_scene`, `AnimationEngine` and the existing tests
  are not modified.
- Existing pilots under `output/_animation_engine_v4` are not overwritten.
- New artifacts go to a separate output directory, by default
  `output/integrated_pilot`.
- The integration tests use the existing Factory renderer; no alternative
  renderer exists.

## Execution order

1. Validate component paths.
2. Parse text and write `input/story.txt`.
3. Story Studio worker -> `Project` + semantic validation.
4. Build Lab `SceneDescription`.
5. Lab worker -> `MotionPlan` + motion validation.
6. Build Factory storyboard v1.
7. Compile with existing Factory compiler.
8. Render with existing Factory engine (frames, voice, audio, MP4, factory QC).
9. Aggregate Story, Lab and Factory results into integration QC.
10. Write `report.json` and `report.md`.

The integration report is authoritative for the cross-component status; Factory
`report.json` remains the authoritative render report.
