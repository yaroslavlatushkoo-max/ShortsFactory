# Changelog

## 1.0.0 — 2026-10-25

### Added
- Core data models (Pydantic 2): CharacterState, Joint, Pose, MotionSegment,
  PropState, Interaction, Keyframe, MotionPlan, SceneDescription,
  ValidationReport with strict cross-reference validation.
- Deterministic trajectory evaluators: linear, cubic Bézier, waypoints,
  hold, arc; easing (linear, ease_in, ease_out, ease_in_out); velocity and
  acceleration estimation; continuity checks.
- 2D kinematic rig (forward kinematics, joint limits, mirroring) and pose
  library with interpolation and transitions.
- Prop interaction subsystem: pickup / attach / transfer / detach / release
  with precondition enforcement (InteractionError on violation) and
  single-ownership invariant.
- Character action finite state machine (13 states) with legal-transition
  table and event-driven transitions.
- Lightweight collision module: circles, AABBs, proximity warnings,
  heuristic visual-defect detection, scene bounds, arm-reach checks.
- Deterministic simulator with fixed-fps sampling, ordered event log,
  repeatable runs, CSV frame export.
- Motion quality analyzer: 14 issue codes across severity levels with
  per-issue time, subject, related events and suggestions.
- Diagnostic visualization (PNG): trajectories, keyframe markers, prop
  paths, attachment points, stick-figure skeletons, scene bounds,
  validation summary; frame strip viewer; CSV export.
- Versioned JSON export + JSON Schema + EngineAdapter interface for
  third-party engines.
- Typer CLI: validate, simulate, plot, export, report, example, adapters.
- Built-in demo scenes: glowing-orb handoff (valid), simple walk, and a
  deliberately broken handoff that the validator must catch.
- Test suite (13 files) covering vectors, trajectories, interpolation,
  planner, poses, rig, collisions, interactions, FSM, simulation,
  validation, exporter and CLI.
