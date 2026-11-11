"""Standalone Animation Intelligence Lab worker.

Receives a Lab SceneDescription on stdin and returns a validated MotionPlan plus
the Lab validation report.  Runs in the Lab virtual environment.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    sys.path.insert(0, payload["lab_src"])
    try:
        from animation_lab.models import SceneDescription  # type: ignore
        from animation_lab.motion_planner import build_plan  # type: ignore
        from animation_lab.validation import validate_plan  # type: ignore
        from animation_lab.exporter import plan_to_dict  # type: ignore
    except Exception as exc:  # pragma: no cover - environment failure
        print(f"Animation Lab import failed: {exc}", file=sys.stderr)
        return 3
    try:
        scene = SceneDescription(**payload["scene"])
        plan, log = build_plan(scene)
        validation = validate_plan(plan)
        data = plan_to_dict(plan)
        data["validation_report"] = validation.model_dump(mode="json")
        print(
            json.dumps(
                {
                    "plan": data,
                    "validation": validation.model_dump(mode="json"),
                    "build_log": log.model_dump(mode="json") if hasattr(log, "model_dump") else {},
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
