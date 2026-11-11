"""Voxel engine — validation.

Plan-level validation (pure Python, no Blender) plus a lightweight post-render
report.  Extends the base :class:`AnimationPlan` checks with voxel-specific
rules (object references, attach points, capture world mapping).
"""

from __future__ import annotations

from ..animation_plan import PlanError

#: Valid hand attachment points used by the voxel rig.
VALID_ATTACH_POINTS = {"hand_r", "hand_l", "root", "head"}


def validate_voxel_plan(plan) -> list[str]:
    """Return a list of voxel-specific plan errors (empty when valid)."""
    errors: list[str] = []
    obj_ids = {o.id for o in plan.objects}
    char_ids = {c.id for c in plan.characters}
    for ev in plan.events:
        if ev.object not in obj_ids:
            errors.append(f"Событие ссылается на неизвестный объект: {ev.object}.")
        if ev.character and ev.character not in char_ids:
            errors.append(f"Событие ссылается на неизвестного персонажа: {ev.character}.")
        if ev.kind in ("attach", "transfer") and ev.point not in VALID_ATTACH_POINTS:
            errors.append(f"Некорректная точка захвата «{ev.point}» в событии {ev.t}.")
        if ev.t < 0 or ev.t > plan.duration + 1e-6:
            errors.append(f"Событие в {ev.t} выходит за границы сцены (длительность {plan.duration}).")
    for obj in plan.objects:
        for kf in obj.keyframes:
            if kf.get("t", 0) < 0:
                errors.append(f"Отрицательное время в keyframe объекта {obj.id}.")
    # camera must be present and finite
    if not plan.camera:
        errors.append("Нет ни одного ключевого кадра камеры (voxel).")
    return errors


def ensure_voxel_plan(plan) -> "plan":
    """Run both base and voxel validation; raise PlanError on any error."""
    base = plan.validate()
    extra = validate_voxel_plan(plan)
    all_err = base + extra
    if all_err:
        raise PlanError("План некорректн (voxel):\n- " + "\n- ".join(all_err))
    return plan
