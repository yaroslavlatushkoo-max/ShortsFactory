# INTEGRATION_GUIDE

Как подключить Animation Intelligence Lab к стороннему анимационному движку.

## Границы

Модуль **не привязан** к какому-либо движку: он выдаёт версионированный JSON
(`schema_version: "1.0.0"`) в универсальном формате из `docs/MOTION_FORMAT.md`
и предоставляет адаптерный интерфейс. Он не рендерит финальное видео и не
гарантирует отсутствия всех визуальных дефектов — он рассчитывает и проверяет
движение до передачи в рендерер.

## Вариант 1. Файловый обмен (самый простой)

1. Внешний редактор пишет описание сцены в формате `SceneDescription`
   (см. `examples/object_handoff.json`).
2. `animation-lab validate` + `animation-lab report` — проверка до рендера.
3. `animation-lab export --format json` — машинно-читаемый план;
   `animation-lab export --format csv` — покадровая таблица позиций.
4. Движок читает JSON: `characters` — начальные состояния, `keyframes` —
   сетка состояний всей сцены с шагом `1/fps`, `interactions` — события
   владения, `motion_segments` — сегменты с траекториями и easing.

Соответствие кадров: кадр `n` соответствует времени `n / fps` секунды;
последний кадр — `duration_seconds`.

## Вариант 2. Адаптер на стороне потребителя

Подкласс `animation_lab.exporter.EngineAdapter` переводит план в структуру
вашего движка:

```python
from animation_lab.exporter import EngineAdapter, import_json

class MyEngineAdapter(EngineAdapter):
    target = "my-engine-2d"

    def convert(self, plan):
        # Пример: спрайтовые треки с якорями и событиями.
        return {
            "target": self.target,
            "fps": plan.fps,
            "sprites": {
                cid: [
                    {"frame": round(f.time * plan.fps),
                     "x": f.characters[cid].position.x,
                     "y": f.characters[cid].position.y,
                     "rot_deg": f.characters[cid].rotation}
                    for f in plan.keyframes
                ]
                for cid in plan.characters
            },
            "events": [
                {"frame": round(i.time * plan.fps), "kind": i.kind,
                 "prop": i.prop_id}
                for i in plan.interactions
            ],
        }

plan = import_json("output/handoff_plan.json")
MyEngineAdapter().export(plan, "output/my_engine.json")
```

Встроенный референс-адаптер: `KeyframeListAdapter` (`target =
"keyframe-list"`). Команда `animation-lab adapters` перечисляет адаптеры.

## Вариант 3. Прямое использование как библиотеки

```python
from animation_lab.exporter import import_scene
from animation_lab.motion_planner import build_plan
from animation_lab.simulation import Simulator
from animation_lab.validation import validate_plan

scene = import_scene("scene.json")
plan, log = build_plan(scene, fps=60)          # план + журнал планирования
report = validate_plan(plan)                   # отчёт о качестве
if report.has_errors:
    raise SystemExit("план содержит ошибки")

sim = Simulator(plan)
characters, props = sim.state_at(3.42)         # состояние в произвольный момент
result = sim.run()                             # вся шкала + события
```

`Simulator.state_at` детерминирован: одинаковый план и время дают одинаковое
состояние — можно вызывать из игрового цикла вместо предрасчёта.

## Правила стыковки координат

- Движок с экранной конвенцией (Y вниз): значения передаются как есть.
- Движок с математической конвенцией (Y вверх): инвертируйте Y —
  `y_engine = 1 - y_plan`, и знак поворота.
- Пиксели: `x_px = x * width`, `y_px = y * height`
  (`vectors.normalized_to_pixels`).
- Скорости: единиц сцены/секунду; умножьте на ширину/высоту холста в пикселях
  для пиксельных скоростей.
- Повороты: градусы по часовой; для радиан используйте
  `math.radians`, для математического направления — инвертируйте знак.

## Контракт версий

- Несовпадение `schema_version` при импорте — ошибка (`ExportError`), а не
  предупреждение: формат меняется только с обновлением схемы и CHANGELOG.
- Поле `units` в экспорте всегда описывает единицы; считывайте его, а не
  предполагайте.
