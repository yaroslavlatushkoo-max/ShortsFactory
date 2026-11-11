# Integration results (actual run)

## Command

Run from `D:\ShortsFactory` on 2026-10-26:

```powershell
.\.venv\Scripts\python.exe -m shortsfactory integrated-create `
  --script "examples\handoff_story.txt" `
  --output "output\integrated_pilot"
```

Result: command exit code `0`, integration QC status **WARNING** (because two
checks are `NOT_CHECKED`; no check failed).

## Input

`examples\handoff_story.txt`:

> Мило замечает светящийся шар на столе, подходит к нему, берёт его и передаёт
> Нине. Нина удивляется, благодарит Мило, после чего они вместе уходят.

## Stage results

| Stage | Result | Evidence |
| --- | --- | --- |
| Story Studio | PASS, 0 errors, 0 warnings, 13 actions, 1 dialogue line | `story\project.json`, `story\validation.json` |
| Animation Lab | `pass`, 16 motion segments, 2 interactions, 0 issues | `motion\motion_plan.json`, `motion\validation.json` |
| Factory storyboard | compiled to scene v2, 13 beats | `factory\storyboard.source.json`, `factory\scene.compiled.json` |
| Factory render | real artistic MP4 with Milo, Nina and the orb | `factory\pilot.mp4` |

## Final artifacts

| Artifact | Absolute path |
| --- | --- |
| Video | `D:\ShortsFactory\output\integrated_pilot\factory\pilot.mp4` |
| Contact sheet | `D:\ShortsFactory\output\integrated_pilot\factory\contact_sheet.jpg` |
| Integration QC JSON | `D:\ShortsFactory\output\integrated_pilot\report.json` |
| Integration QC Markdown | `D:\ShortsFactory\output\integrated_pilot\report.md` |
| Story Project | `D:\ShortsFactory\output\integrated_pilot\story\project.json` |
| MotionPlan | `D:\ShortsFactory\output\integrated_pilot\motion\motion_plan.json` |
| Factory report | `D:\ShortsFactory\output\integrated_pilot\factory\report.json` |

## Video facts

From the Factory report and independent `ffprobe`:

- container: MP4 (`mov,mp4,m4a,3gp,3g2,mj2`);
- duration: 9.200 s;
- size: 1,931,650 bytes;
- video: H.264, 1080 × 1920, 30/1 fps;
- audio: AAC, 2 streams total;
- engine frame count: 276; `ffprobe` container `nb_frames`: 275;
- full decode with `ffmpeg -v error -i pilot.mp4 -f null -`: exit code 0, no
  errors.

The one-frame difference is the encoder/container frame count versus the
engine's sampled frame count; the video plays and decodes successfully. It is
reported rather than hidden.

## Factory quality checks

| Check | Result | Actual value / note |
| --- | --- | --- |
| Full decode | PASS | `decode_full: true` |
| Determinism | PASS | 3 samples identical at 2.3 s, 4.6 s, 6.9 s |
| Motion without camera | PASS | `limbs_move_without_camera: true` |
| Attachment | PASS | `verified: true`, transfer jump `45.71 px` |
| Framing | PASS | `framing.ok: true` |
| Pose quality | PASS | enabled, no issues |
| Lip-sync | PASS | enabled, verified |

Factory attachment threshold is 160 px for a transfer. The pilot is under it;
the object is held at `hand_r` during both holds and follows the hand.

## Combined QC checks

| Check | Status |
| --- | --- |
| `story_schema` | PASS |
| `storyboard_compile` | PASS |
| `motion_plan` | PASS |
| `timings` | PASS |
| `mappings` | PASS |
| `interactions` | PASS |
| `motion` | PASS |
| `framing` | PASS |
| `pose_quality` | PASS |
| `video` | PASS |
| `lip_sync` | PASS |
| `silhouette_intersections` | NOT_CHECKED |
| `curved_trajectories` | NOT_CHECKED |

Overall: **WARNING**, not PASS, because `NOT_CHECKED` is treated as unverified.

## Tests

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Actual result: **121 passed**.

Targeted integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_integration.py -q
```

Actual result: **12 passed**.

The integration tests cover:

- text parser entities, actions, timings and dialogue speaker;
- Story Studio Project validation and IDs;
- Lab pickup/transfer ownership and validation;
- Factory storyboard character/object IDs, attach/transfer events and voice;
- deterministic Lab plan generation;
- unknown action rejection;
- unknown character rejection;
- unreachable prop reporting by Lab;
- Factory storyboard compile failure handling;
- combined QC `FAIL` on broken attachment;
- combined QC `WARNING` and `NOT_CHECKED` semantics.

Existing Factory tests (`tests\test_animation.py`, `tests\test_scripts.py` and
the full suite) remain green; no existing test was removed or weakened.

## Problems fixed during integration

1. **Story Studio ownership/continuity** — initial/ending world state had to
   match simulated prop ownership and presence; the generated Project now
   validates with 0 errors.
2. **Lab unreachable target** — the prop was initially 0.206 units from Milo,
   above the Lab reach limit 0.20. The workbench position was moved to x=0.33;
   pickup distance is now within reach.
3. **Dialogue speaker** — the quoted thank-you was attributed to Milo; speaker
   detection was corrected to Nina (the character immediately before the quote),
   with a deterministic fallback.
4. **Illegal Lab state machine edge** — `react -> speak` with no gap was bridged
   by shortening the reaction and inserting an explicit idle segment.
5. **Transfer jump** — initial transfer produced 335.65 px, then 183.96 px,
   above the Factory 160 px threshold. Moving the center positions to 0.30/0.70
   and transferring at 25% of the give action reduced the measured jump to
   **45.71 px** while keeping the characters visually separated.
6. **Character overlap** — the first passing attempt put Milo at 0.44 and Nina
   at 0.56; the contact sheet showed overlapping bodies. The final positions
   0.30/0.70 remove the overlap.
7. **QC aggregation** — `motion` and `pose_quality` were initially treated as
   `NOT_CHECKED`; they now read the Factory `verified` structure
   (`limbs_move_without_camera`, enabled/no pose issues, attachment verified).
8. **Failure reports** — a failed stage now writes `report.json`/`report.md`
   with the stage name, error and artifacts instead of leaving an empty folder.

## Known non-blocking observations

- The contact sheet contains only three sampled frames (2.9 s, 4.4 s, 5.7 s),
  matching the Factory contact-sheet selection, not every frame.
- The final MP4 is 9.2 s although the Story target was 10 s; the difference is
  below the Story Studio 3 s tolerance.
- `silhouette_intersections` and `curved_trajectories` remain unverified by
  design; see `INTEGRATION_LIMITATIONS.md`.
- No git commit was made. The three projects were not git repositories at the
  start, so a SHA-256 baseline manifest was recorded instead at
  `C:\Users\vo_va\AppData\Local\Temp\opencode\integration_baseline_20261026.json`.
