# Quickstart

## 1. Install (Windows)

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install -e ".[dev]"
```

Linux/macOS: replace `.venv\Scripts\activate` with `source .venv/bin/activate`.

## 2. Generate and inspect the demo scene

```bat
animation-lab example --output output/example.json
```

This writes the reference scenario *glowing orb handoff*: character A walks
from the left toward the orb, picks it up, hands it to character B; B takes
it, examines it, reacts and steps back.

## 3. Validate

```bat
animation-lab validate examples/object_handoff.json
```

Expected output ends with `status: pass` / `no issues found` and exit code 0.

Run the same command on the intentionally broken scene to see the analyzer:

```bat
animation-lab validate examples/broken_handoff.json
```

Reports codes such as `DOUBLE_OWNERSHIP`, `TRAJECTORY_DISCONTINUITY`,
`TARGET_UNREACHABLE` and exits with code 1.

## 4. Simulate

```bat
animation-lab simulate examples/object_handoff.json --fps 30
```

Prints frame count and the ordered event log (segment starts, pickup,
transfer...). Add `--output output/frames.csv` to save the frame table.

## 5. Visualize

```bat
animation-lab plot examples/object_handoff.json --output output/handoff.png
```

Writes a diagnostic PNG: character paths, keyframe markers, prop path,
attachment points, stick figures at t = 0/25/50/75/100%, scene bounds,
validation summary.

## 6. Export

```bat
animation-lab export examples/object_handoff.json --format json --output output/plan.json
animation-lab export examples/object_handoff.json --format csv  --output output/frames.csv
animation-lab report examples/object_handoff.json --output output/report.json
```

The JSON export is versioned (`schema_version: "1.0.0"`) and validated
against `schemas/motion_plan.schema.json`.

## 7. Run the test suite

```bat
pytest -q
```

## 8. Use from Python

```python
from animation_lab.exporter import import_scene
from animation_lab.motion_planner import build_plan
from animation_lab.simulation import Simulator
from animation_lab.validation import validate_plan

scene = import_scene("examples/object_handoff.json")
plan, log = build_plan(scene)
report = validate_plan(plan)
result = Simulator(plan).run()

chars, props = plan and Simulator(plan).state_at(3.0)
print(report.status, len(result.frames), props["orb_glow"].owner_id)
```
