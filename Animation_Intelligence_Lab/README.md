# Animation Intelligence Lab

Standalone toolkit for **character motion planning, simulation and validation**
in short 2D animated scenes. It turns a JSON description of characters, props
and actions into a machine-readable motion plan: timeline, trajectories, key
poses, interaction events, diagnostic checks and a versioned JSON export for
an external animation engine.

> **What this system is NOT**
>
> - it does **not** draw final artistic characters (stick figures are for
>   diagnostics only);
> - it does **not** synthesize speech or audio;
> - it does **not** render a final cartoon / MP4;
> - it does **not** guarantee the absence of all visual defects — it detects
>   the classes of problems listed in §Validation below;
> - it computes and checks motion **within its kinematic model** (no physics,
>   no dynamics, no GPU, fully offline).

## Features

- **Strict data models** (Pydantic 2): `CharacterState`, `Joint`, `Pose`,
  `MotionSegment`, `PropState`, `Interaction`, `Keyframe`, `MotionPlan`,
  `SceneDescription` — with cross-reference and range validation.
- **Deterministic trajectories**: linear, cubic Bézier, waypoints, hold, arc;
  easing `linear` / `ease_in` / `ease_out` / `ease_in_out`; velocity and
  acceleration estimation; segment-boundary continuity checks.
- **2D kinematic rig**: joint hierarchy, forward kinematics, rotation limits,
  pose library, pose interpolation, mirroring; demo humanoid (head, torso,
  shoulders, elbows, hands, hips, knees, feet).
- **Prop interactions**: `pickup` / `attach` / `transfer` / `detach` /
  `release` with precondition enforcement — violations raise
  `InteractionError` instead of silently repairing data; single-ownership
  invariant at any instant.
- **Finite state machine** for character actions (13 states) with legal
  transitions and event-driven gates.
- **Lightweight collisions**: circles, AABBs, proximity warnings, heuristic
  visual-defect suspicion, scene-bounds and arm-reach checks.
- **Deterministic simulator**: state at any time, fixed-fps sampling,
  ordered event log, repeatable runs, CSV frame export.
- **Motion quality analyzer**: 14 issue codes with severity, time, subject,
  related events and a suggested fix. Never returns an unconditional PASS.
- **Diagnostic visualization**: PNG with trajectories, keyframes, prop paths,
  attachment points, stick-figure skeletons, scene bounds and the validation
  summary; frame-strip viewer; CSV export.
- **Versioned JSON export** (`schema_version` 1.0.0) backed by
  `schemas/motion_plan.schema.json`, plus an `EngineAdapter` interface for
  third-party engines (a reference `KeyframeListAdapter` is included).

## Installation (Windows, Python 3.12+)

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

or with plain pip:

```bat
pip install -r requirements.txt
pip install -e .
```

## Quick start

```bat
animation-lab example --output output/example.json
animation-lab validate examples/object_handoff.json
animation-lab simulate examples/object_handoff.json --fps 30
animation-lab plot examples/object_handoff.json --output output/handoff.png
animation-lab export examples/object_handoff.json --format json
animation-lab report examples/object_handoff.json --output output/report.json
```

`validate` exits with code 1 when the plan has errors, 2 on invalid input.

## Tests

```bat
pip install -e ".[dev]"
pytest -q
```

## Units and coordinates

| Quantity  | Unit                                                        |
|-----------|-------------------------------------------------------------|
| position  | normalized scene units, `[0,1]` per axis, origin top-left, y down |
| velocity  | normalized scene units / second                             |
| time      | seconds                                                     |
| rotation  | degrees, positive = clockwise on screen                     |

Pixel coordinates exist only at visualization/export time
(`vectors.normalized_to_pixels(p, width, height)`).

## Documentation

- `docs/ARCHITECTURE.md` — module map and data flow
- `docs/MOTION_FORMAT.md` — exported JSON format and schema
- `docs/KINEMATICS.md` — coordinates, rig model, limits, limitations
- `docs/INTERACTIONS.md` — prop events, preconditions, ownership rules
- `docs/INTEGRATION_GUIDE.md` — connecting an external engine

## License

MIT.
