"""Inspect a 3D model inside Blender (headless) and report its structure.

Works with glTF/GLB (native Blender import), FBX, OBJ.  Detects objects,
an armature (if any), imported Actions and their frame ranges.  Used by the
isolated animation pipeline prototype (Mission 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ModelInspectionError(RuntimeError):
    """Raised when import or inspection fails."""


@dataclass
class ClipInfo:
    name: str
    frame_start: float
    frame_end: float
    duration_seconds: float
    curve_count: int
    animated_targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "duration_seconds": self.duration_seconds,
            "curve_count": self.curve_count,
            "animated_targets": self.animated_targets,
        }


@dataclass
class ModelInspection:
    path: str
    format: str
    objects: list[str]
    mesh_count: int
    has_armature: bool
    armature_name: str
    clips: list[ClipInfo]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "objects": self.objects,
            "mesh_count": self.mesh_count,
            "has_armature": self.has_armature,
            "armature_name": self.armature_name,
            "clips": [c.to_dict() for c in self.clips],
            "notes": self.notes,
        }


def _import_script(model_path: str, out_json: str) -> str:
    """Blender-side script: import model, dump inspection JSON."""
    return f'''
import bpy, json, sys
model = r"{model_path}"
out = r"{out_json}"
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = model.rsplit(".", 1)[-1].lower()
notes = []
if ext in ("glb", "gltf"):
    bpy.ops.import_scene.gltf(filepath=model)
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=model)
elif ext == "obj":
    bpy.ops.wm.obj_import(filepath=model)
else:
    raise RuntimeError("Unsupported format: " + ext)
objects = [o.name for o in bpy.data.objects]
arms = [o.name for o in bpy.data.objects if o.type == "ARMATURE"]
clips = []
fps = bpy.context.scene.render.fps or 30
for a in bpy.data.actions:
    fr = a.frame_range
    dur = (fr[1] - fr[0]) / fps
    count = 0
    targets = []
    try:
        for lay in a.layers:
            for st in lay.strips:
                for bg in st.channelbags:
                    curves = bg.fcurves
                    count += len(curves)
                    if curves:
                        targets.append(str(getattr(bg, "slot", "")))
    except Exception:
        count = -1
    clips.append({{
        "name": a.name,
        "frame_start": round(float(fr[0]), 3),
        "frame_end": round(float(fr[1]), 3),
        "duration_seconds": round(float(dur), 3),
        "curve_count": count,
        "animated_targets": targets[:8],
    }})
result = {{
    "path": model,
    "format": ext,
    "objects": objects,
    "mesh_count": sum(1 for o in bpy.data.objects if o.type == "MESH"),
    "has_armature": bool(arms),
    "armature_name": arms[0] if arms else "",
    "clips": clips,
    "notes": notes,
}}
with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("INSPECT_OK")
'''


def inspect_model(model_path: str | Path, blender_exe: str | None = None) -> ModelInspection:
    """Import ``model_path`` in background Blender and return inspection.

    Raises ModelInspectionError on import failure or missing file.
    """
    from ..blender_adapter import blender_executable

    model_path = Path(model_path)
    if not model_path.exists():
        raise ModelInspectionError(f"Файл модели не найден: {model_path}")
    if model_path.suffix.lower() not in (".glb", ".gltf", ".fbx", ".obj"):
        raise ModelInspectionError(f"Неподдерживаемый формат: {model_path.suffix}")
    exe = blender_exe or blender_executable()
    if not exe:
        raise ModelInspectionError("Blender не найден (BLENDER_EXECUTABLE / PATH).")

    import json
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out_json = str(Path(td) / "inspect.json")
        script = Path(td) / "inspect.py"
        script.write_text(_import_script(str(model_path), out_json), encoding="utf-8")
        proc = subprocess.run(
            [exe, "--background", "--python", str(script)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise ModelInspectionError(
                "Импорт модели не удался: " + (proc.stderr or "")[-500:]
            )
        data = json.loads(Path(out_json).read_text(encoding="utf-8"))
    clips = [
        ClipInfo(**{k: v for k, v in c.items() if k in ClipInfo.__dataclass_fields__})
        for c in data.get("clips", [])
    ]
    return ModelInspection(
        path=str(model_path),
        format=data.get("format", ""),
        objects=data.get("objects", []),
        mesh_count=int(data.get("mesh_count", 0)),
        has_armature=bool(data.get("has_armature")),
        armature_name=str(data.get("armature_name", "")),
        clips=clips,
        notes=list(data.get("notes", [])),
    )
