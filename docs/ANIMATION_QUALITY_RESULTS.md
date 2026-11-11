# Animation Quality Results (Mission 2)

Date: 2026-10-26. Working tree: `D:\ShortsFactory` (no Git repository).
All results are new; `output/integrated_pilot` was not overwritten.

## Produced films

Root: `D:\ShortsFactory\output\animation_intelligence_upgrade`.

| Scenario | Script | Trajectory | Status | Frames | Duration | MP4 size | Silhouette | Attachment |
|---|---|---|---|---:|---:|---:|---|---|
| 01_handoff | `examples/handoff_story.txt` | linear | WARNING | 279 | 9.32 s | 2,061,373 B | WARNING, 0 failures | verified |
| 02_arc | `examples/handoff_arc.txt` | arc | WARNING | 279 | 9.32 s | 2,049,178 B | WARNING, 0 failures | verified |
| 03_waypoints | `examples/waypoints_story.txt` | waypoints | WARNING | 255 | 8.52 s | 1,895,422 B | WARNING, 0 failures | verified |
| 04_multi_dialogue | `examples/multi_dialogue_story.txt` | linear | WARNING | 368 | 12.27 s | 2,374,898 B | WARNING, 0 failures | verified |
| 05_pickup | `examples/pickup_story.txt` | linear | WARNING | 186 | 6.20 s | 996,286 B | PASS | verified |
| 06_return | `examples/return_story.txt` | linear | WARNING | 312 | 10.42 s | 2,204,431 B | WARNING, 0 failures | verified |

All six are H.264 1080×1920 at 30 fps with AAC audio. Every file passed
`ffprobe` and a full `ffmpeg -v error -i ... -f null -` decode. Each folder has
`factory/pilot.mp4`, `factory/contact_sheet.jpg`, `report.json`, `report.md`,
`story/`, `motion/`, `factory/`, and `factory/diagnostics/` when the silhouette
detector found an issue.

## Scenario coverage

- 01: handoff + dialogue + exit.
- 02: same story with a curved (`arc`) walk and transfer.
- 03: waypoint path around the table plus dialogue.
- 04: three dialogue lines with three different speech gestures.
- 05: approach + pickup, no handoff, character leaves with the prop.
- 06: handoff and return of the prop to the original owner.

The 05 lip-sync check is `NOT_CHECKED` because the scenario has no dialogue.
That is reported honestly and makes the overall result `WARNING`; it is not a
silent pass.

## QC outcome

All six reports have no `FAIL`. The non-silhouette checks pass:
`story_schema`, `storyboard_compile`, `motion_plan`, `timings`, `mappings`,
`interactions`, `motion`, `framing`, `pose_quality`, `video`, `lip_sync` where
dialogue exists, and `trajectory_integration`.

`silhouette_intersections` is `WARNING` for 01/02/03/04/06 because small
character-character alpha overlaps occur inside intentional contact windows.
There are zero failures. 05 has no contact overlap and is `PASS`.

Factory still reports its original verified checks: decode, determinism,
motion, attachment, lip-sync, framing, and pose quality. Attachment transfer
jumps are 45.71 px (01/02/04), 38.02 px (06), and no transfer in 03/05; the
configured threshold was not changed.

## Trajectory evidence

`motion/trajectory_metrics.json` is written for every film. Values:

- 01 linear: 5 moving beats, deviation 0, curvature ratio 1.0.
- 02 arc: 5 moving beats, max deviation 0.05, curvature ratio 3.56473.
- 03 waypoints: 5 moving beats, max deviation 0.06953, curvature ratio 4.753554.
- endpoint error is 0 for all three.

The trajectories affect the encoded frames. Comparing decoded frames at equal
timestamps:

| Time | linear vs arc mean abs diff | linear vs waypoints mean abs diff |
|---:|---:|---:|
| 1.5 s | 4.316 | 4.512 |
| 2.0 s | 2.125 | 2.404 |
| 4.8 s | 0.362 | 7.255 |

These differences come from the root position at frames where the path is
active; they are not metadata-only changes.

## Comparison with the previous pilot

The previous integrated pilot (`output/integrated_pilot`) remains:

- 9.20 s, 275 frames, 1,931,650 B, H.264/AAC, QC `WARNING` with
  11 PASS and 2 `NOT_CHECKED` checks.

Mission 2 adds six new films, temporal paths, real mask-based silhouette
analysis, speech gestures, trajectory metrics, and diagnostics. The old pilot
was not modified.

## Tests

| Suite | Command | Result |
|---|---|---:|
| Integration | `.venv\Scripts\python.exe -m pytest tests\test_integration.py -q` | 25 passed |
| Factory full | `.venv\Scripts\python.exe -m pytest -q` | 134 passed |
| Story Studio | `PYTHONPATH=...\src python -m pytest -q` | 41 passed |
| Animation Lab | `...\Animation_Intelligence_Lab\.venv\Scripts\python.exe -m pytest -q` | 129 passed |

The test log is stored in `output/animation_intelligence_upgrade/test_log.txt`
when the final packaging step runs. It records the exact commands and exit codes
instead of relying on this prose summary.

## Known failures / limitations

No scenario has a QC `FAIL`. The following are not full passes:

- 05: `lip_sync=NOT_CHECKED` (no dialogue), therefore overall `WARNING`.
- 01/02/03/04/06: silhouette `WARNING` from intentional contact-adjacent
  alpha overlap; zero failure-threshold violations.
- Curved/waypoint motion is 2D root motion with a bounded vertical lift, not a
  full 3D walk cycle.
- The Lab ownership model positions an attached prop at the owner's root; the
  Factory renderer independently attaches it to `hand_r`. The render checks the
  final hand distance, but the intermediate representations are not identical.
- `reach` does not carry a per-hand point through the legacy compiler; receive
  visually uses `hand_r`.
- Exit is clamped to x = 0.16 / 0.84 to keep the artistic characters inside the
  framing check. They do not fully leave the frame.
- There is no phoneme-level lip-sync and no physical simulation.
