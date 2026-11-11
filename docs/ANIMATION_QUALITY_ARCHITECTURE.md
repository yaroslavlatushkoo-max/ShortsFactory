# Animation Quality Architecture (Mission 2)

## Scope

This document describes the upgraded pipeline that connects three independent
components without copying or merging their source trees:

1. **Story Studio** (`D:\ShortsFactory_StoryStudio\ShortsFactory_StoryStudio`)
   owns semantics: characters, props, scenes, actions, dialogue, timing.
2. **Animation Intelligence Lab** (`D:\Animation_Intelligence_Lab`) owns
   kinematics: trajectories, interpolation, keyframes, interaction state.
3. **ShortsFactory** (`D:\ShortsFactory`) owns visual production: artistic
   characters, poses, rendering, audio, encoding, and frame-level QC.

Story Studio and Lab run in separate Python processes through JSON workers.
ShortsFactory imports neither package at runtime, so their dependency sets remain
unchanged.

```
text -> Story Studio Project -> Lab SceneDescription -> MotionPlan
     -> Factory storyboard v1 (+ path payload) -> compiled scene v2
     -> AnimationEngine render -> MP4 + frames + QC
```

## Data ownership

| Data | Owner | Transport |
|---|---|---|
| Story text, characters, dialogue, intent, semantic timing | Story Studio | `story/project.json`, `story/validation.json` |
| Segment geometry, easing, keyframes, interaction ownership | Animation Lab | `motion/motion_plan.json`, `motion/validation.json` |
| Path payload consumed by the renderer | ShortsFactory | `path` inside action/beat params |
| Frames, audio, MP4, silhouette masks, diagnostics | ShortsFactory | `factory/` |
| Cross-component verdicts | integration layer | `report.json`, `report.md` |

## Temporal trajectory contract

The Factory storyboard keeps version `1`. A moving beat may additionally carry:

```json
{
  "path": {
    "kind": "linear|hold|bezier3|waypoints|arc",
    "points": [{"x": 0.30, "y": 0.65}, ...],
    "easing": "linear|ease_in|ease_out|ease_in_out",
    "arc_height": 0.10,
    "source": "animation-lab"
  }
}
```

`compile_storyboard` copies this payload into the compiled action's `params`
without changing older storyboard fields. `scene.validate` checks the endpoint
against `to_x`, coordinate bounds, and path shape. `renderer.character_pose`
evaluates the path at the current normalized action time instead of linearly
interpolating only the endpoint. `x` is the root position; `y` is converted to a
bounded visual lift (`path_lift`, maximum 0.12 scene units). This is a front-view
rig, not a free 3D root, so the lift is deliberately limited and documented as a
visual approximation.

Lab trajectories are the source of truth:

- `linear`: start → end.
- `bezier3`: one or two control points; integration emits two.
- `waypoints`: start + intermediate controls + end.
- `arc`: one control point, converted to a vertical arc.
- `hold`: no movement.

Every moving Factory action receives the converted path. The integration QC
`trajectory_integration` check fails if a moving Lab segment has no corresponding
Factory beat path.

## Interaction contract

Story Studio owns semantic `pick_up` / `give` / `receive` / `put_down`.
The integration maps them to Lab `pickup` / `transfer` / `detach` and to Factory
attach/transfer events. Timing is preserved:

- pickup: Lab time = action end − 0.1 s;
- transfer: Lab time = action start + 25% of give duration;
- detach: action end − 0.1 s.

Lab validates ownership, reachability, and continuity. Factory independently
validates the rendered hand-to-object distance, spread, transfer jump, and
detachment jump. A mismatch is exposed in the combined report; neither validator
is silently bypassed.

## Silhouette QC

`AnimationEngine.verify_silhouette_intersections` samples the actual rendered
character and object masks (not bounding-box guesses):

- character mask: `_paste_character(..., mask=True)` on a transparent stage;
- object mask: `_draw_object` on a transparent stage;
- overlap counts are measured in pixels at six samples per second;
- contact windows around reach/take/give/offer/drop/carry/pickup/putdown are
  treated as intentional contact context;
- large body overlap outside contact is `FAIL`; smaller or contact-window
  overlap is `WARNING`;
- an object hidden behind a character (`object.z < character.z`) is not counted
  as an overlap;
- up to 20 diagnostic PNGs are written to `factory/diagnostics/`.

This is a 2D alpha-mask measurement. It cannot prove anatomical non-penetration
or 3D depth ordering.

## Audio / lip-sync

The existing Factory path is preserved: Piper voice track + procedural audio +
AAC mux. Lip-sync is the Factory envelope-based check, not phoneme alignment.
Scenarios without dialogue correctly report `NOT_CHECKED` for lip-sync instead
of claiming a pass.

## Failure handling and compatibility

- Story Studio errors stop before Lab with a readable error.
- Lab validation failures are preserved in `motion/validation.json`; the
  integration report marks the stage and does not hide warnings.
- Render failures write a failure report with stage, message, and known
  artifacts.
- Old storyboard version 1 files without `path` render exactly as before.
- Existing Factory CLI commands, renderer entry points, and old pilots are not
  modified or overwritten.
- New results live under `output/animation_intelligence_upgrade/`.

## Determinism

Factory samples three frames and checks identical hashes inside one render; the
report stores the result. Lab plan construction and path conversion are pure
functions of the validated project and profile. Integration tests check stable
Lab plans and path endpoints.
