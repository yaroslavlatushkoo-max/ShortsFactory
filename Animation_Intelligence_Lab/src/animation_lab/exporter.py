"""exporter.py — versioned JSON export + JSON Schema + adapter interface.

The export format is documented in docs/MOTION_FORMAT.md and backed by
schemas/motion_plan.schema.json (generated from the Pydantic models).

The ``EngineAdapter`` ABC defines the integration point for third-party
animation engines: consume a normalized MotionPlan, emit engine-specific
data.  No particular engine is assumed or shipped.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import MotionPlan, SceneDescription, SCHEMA_VERSION
from .simulation import SimulationResult, frame_csv


class ExportError(ValueError):
    """Raised when an export cannot be performed."""


def plan_to_dict(plan: MotionPlan) -> dict[str, Any]:
    """Serialize a plan into a plain dict following the JSON Schema."""
    data = plan.model_dump(mode="json")
    data["schema_version"] = SCHEMA_VERSION
    return data


def export_json(
    plan: MotionPlan,
    output_path: str | Path,
) -> Path:
    """Write the versioned JSON export of a plan."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = plan_to_dict(plan)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    if not out.exists() or out.stat().st_size == 0:
        raise ExportError(f"failed to write JSON export to {output_path}")
    return out


def export_csv(
    result: SimulationResult,
    output_path: str | Path,
) -> Path:
    """Write per-frame positions of characters and props as CSV."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(frame_csv(result), encoding="utf-8")
    if not out.exists() or out.stat().st_size == 0:
        raise ExportError(f"failed to write CSV export to {output_path}")
    return out


def export_schema(output_path: str | Path) -> Path:
    """Generate/refresh the JSON Schema for the motion plan format."""
    from .models import MotionPlan as MP

    schema = MP.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "MotionPlan"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out


def import_json(input_path: str | Path) -> MotionPlan:
    """Re-import a previously exported plan (round-trip support)."""
    raw = Path(input_path).read_text(encoding="utf-8")
    data = json.loads(raw)
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ExportError(
            f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION!r})"
        )
    return MotionPlan.model_validate(data)


def import_scene(input_path: str | Path) -> SceneDescription:
    """Load a scene description (planner input) from JSON."""
    raw = Path(input_path).read_text(encoding="utf-8")
    return SceneDescription.model_validate_json(raw)


def validate_against_schema(
    plan: MotionPlan,
    schema_path: str | Path,
) -> list[str]:
    """Validate the serialized plan against the JSON Schema.

    Returns a list of validation error messages (empty = valid).
    """
    import jsonschema

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors = [
        f"{'/'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in validator.iter_errors(plan_to_dict(plan))
    ]
    return sorted(errors)


# ---------------------------------------------------------------------------
# Adapter interface for third-party engines
# ---------------------------------------------------------------------------

class EngineAdapter(ABC):
    """Integration point for an external animation engine.

    Subclass and implement :meth:`convert` to translate the normalized,
    engine-agnostic plan into your engine's representation (keyframe lists,
    bone curves, etc.).  Nothing in this package depends on a concrete
    engine; adapters live on the consumer side or in plugins.
    """

    #: Human-readable name of the target engine, e.g. "my-engine-2d".
    target: str = "abstract"

    @abstractmethod
    def convert(self, plan: MotionPlan) -> dict[str, Any]:
        """Convert a plan into the target engine's data structure."""

    def export(self, plan: MotionPlan, output_path: str | Path) -> Path:
        """Convert and write the result as JSON (convenience)."""
        payload = self.convert(plan)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out


class KeyframeListAdapter(EngineAdapter):
    """Reference adapter: emits a minimal, flat keyframe list per character."""

    target = "keyframe-list"

    def convert(self, plan: MotionPlan) -> dict[str, Any]:
        chars: dict[str, list[dict[str, Any]]] = {}
        for frame in plan.keyframes:
            for cid, st in frame.characters.items():
                chars.setdefault(cid, []).append(
                    {
                        "t": round(frame.time, 6),
                        "x": round(st.position.x, 6),
                        "y": round(st.position.y, 6),
                        "rot": round(st.rotation, 3),
                    }
                )
        return {
            "target": self.target,
            "schema_version": SCHEMA_VERSION,
            "fps": plan.fps,
            "duration_seconds": plan.duration_seconds,
            "tracks": chars,
        }
