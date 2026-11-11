# MOTION_FORMAT

Версия формата: **1.0.0** (`schema_version`). JSON Schema:
`schemas/motion_plan.schema.json`. Единицы зафиксированы в поле `units`
каждого экспорта.

## Единицы

| Поле | Единица |
|---|---|
| позиции (`position`, контрольные точки) | нормализованные единицы сцены, `[0,1]` по обеим осям, начало — левый верхний угол, Y вниз |
| скорости (`root_velocity`, `velocity`) | единиц сцены в секунду |
| время (`start_time`, `end_time`, `time`, `Keyframe.time`) | секунды |
| повороты (`rotation`, лимиты суставов) | градусы, положительные — по часовой на экране |
| `fps` | кадров в секунду (1–240) |

Пиксели появляются только при отрисовке; в формате их нет.

## Структура MotionPlan

```jsonc
{
  "schema_version": "1.0.0",
  "name": "glowing_orb_handoff",
  "fps": 30,
  "duration_seconds": 6.0,
  "units": { "position": "normalized_scene_units [0,1]", "time": "seconds", ... },
  "characters":   { "<id>": CharacterState, ... },
  "props":        { "<id>": PropState, ... },
  "motion_segments": [ MotionSegment, ... ],
  "interactions":    [ Interaction, ... ],
  "keyframes":       [ Keyframe, ... ],   // сетка duration*fps + 1 кадров
  "timeline":        [ Keyframe, ... ],   // дублирует keyframes для движков
  "validation_report": { "status": "pass|warnings|fail", "issues": [ ... ] }
}
```

### CharacterState
`character_id`, `position{x,y}`, `rotation`, `root_velocity{x,y}`, `scale`,
`emotion` (neutral|happy|sad|surprised|angry|afraid|curious), `gaze_target`,
`current_action`, `pose_id`, `visibility` (visible|hidden|faded).

### MotionSegment
`id`, `character_id`, `action_type` (idle|walk|run|turn|reach|hold|speak|
react|jump|fall|land|sit|stand|step_back|handoff), `start_time`, `end_time`,
`start_state`/`end_state` (полные CharacterState), `trajectory{kind,
control_points, easing, max_speed}`, `constraints{max_speed,
max_acceleration, keep_in_bounds}`, `dependencies`.

Виды траекторий: `linear`, `bezier3` (1–2 контрольные точки), `waypoints`,
`hold`, `arc`. Easing: `linear`, `ease_in`, `ease_out`, `ease_in_out`.

### PropState
`prop_id`, `position`, `rotation`, `owner_id` (null для свободного),
`attachment_point` (например `hand_r`), `velocity`, `state`
(free|approaching|attached|transferring|released), `glowing`.

Инварианты: `attached`/`transferring` требуют `owner_id`; `free` запрещает
владельца.

### Interaction
`id`, `kind` (pickup|attach|transfer|detach|release|handoff), `time`,
`actor_id`, `prop_id`, `receiver_id` (обязателен для transfer),
`attachment_point`, `preconditions[]`, `expected_prop_state_after`,
`expected_owner_after`.

### Keyframe
`time`, `label`, `characters{id: CharacterState}`, `props{id: PropState}`.

### ValidationIssue
`severity` (info|warning|error|critical), `code`, `message`, `time`,
`subject_id`, `subject_kind`, `related_events[]`, `suggestion`.

## Коды валидатора

| Код | Severity | Смысл |
|---|---|---|
| POSITION_JUMP | error | скачок позиции между кадрами |
| SPEED_LIMIT_EXCEEDED | warning | превышение max_speed сегмента |
| SUSPICIOUS_ACCELERATION | warning | подозрительный разгон/торможение |
| TRAJECTORY_DISCONTINUITY | error | разрыв позиции на стыке сегментов |
| ILLEGAL_STATE_TRANSITION | error | переход вне таблицы FSM |
| UNBACKED_EVENT_TRANSITION | error | событийный переход без события-владения |
| OUT_OF_BOUNDS | error | выход за границы сцены |
| DOUBLE_OWNERSHIP | critical | два владельца предмета в один момент |
| TARGET_UNREACHABLE | error | предмет дальше досягаемости руки |
| MISSING_KEYFRAMES | error/warning | неполная сетка кадров |
| TIMELINE_ORDER | error | время кадров не монотонно |
| TIMING_DEPENDENCY | error | сегмент/событие за пределами длительности |
| INCOMPATIBLE_ACTIONS | error | перекрытие сегментов одного персонажа |
| CHARACTER_COLLISION / PROXIMITY_WARNING | error/warning | пересечение/близость тел |
| EMPTY_PLAN | warning | нет сегментов движения |

## Round-trip

`exporter.import_json` принимает только файлы с совпадающим
`schema_version`; расхождение версии даёт `ExportError`. Пример полного
экспорта: `examples/motion_report.json`.
