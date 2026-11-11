# Voxel Quality Reset — Mission 7 — дизайн и запуск

Дата: 2026-10-27. Цель: одна качественная короткая сцена в упрощённом
кубическом стиле, приятная для просмотра, а не «технически правильный» ролик.

## Что изменилось в движке (первопричины, а не маскировка)

### 1. Камера полностью переработана (приоритет №1)
Прежняя камера целилась в точку старта героя и не следовала за ним во время
ходьбы (герой уходил из кадра), а угол был случайным.

Новая система (`voxel_camera.py`) — **постановочная**:
- **Target** — empty, привязанный к груди героя (`CHEST_HEIGHT`), поэтому камера
  всегда держит лицо/торс в кадре, пока герой идёт.
- **Камера** использует `Track To` на target и позиционируется по **сегментам
  сцен** (SHOTS) с явными: смещение (offset), дистанция, высота, фокусное
  расстояние, угол, момент начала/конца.
- **9:16**: `sensor_fit='VERTICAL'`, `sensor_height=18`, вертикальные планы.
- **Главный план:** трёхчетвертной вид спереди-сбоку на уровне героя
  (смещение +X/+Y, высота у груди), видно лицо, торс, руки, ноги и предмет.
- **Финальный план:** ближе, выше, длиннофокусный (62 мм) — выразительный
  крупный план.
- Деревья/декор убраны в фон (-Y) и по краям, чтобы не перекрывать героя.

### 2. Риг и пропорции (`voxel_rig.py`, `voxel_character.py`)
Прежние ноги были короткими и уходили под землю. Теперь:
- Корень (`root`) стоит на высоте бедра `HIP_Z=0.80`, ноги (0.80) достают до
  земли, ступни (ботинки) лежат на поверхности.
- Пропорции: голова 0.38, торс 0.66×0.40×0.55, руки 0.18×0.55, ноги
  0.22×0.80 — цельный узнаваемый силуэт.
- Лицо выразительнее: крупные глаза, брови, рот на передней (+Y) грани.
- Палитра согласована (кожа/волосы/рубашка/штаны/ботинки/кант).

### 3. Анимация (`voxel_animation.py`)
- Базовая высота привязана к `HIP_Z` (ноги не тонут).
- Добавлены действия из брифа и поддерживаемые движком: `bend`, `hold`,
  `fall`, `recover` (для валидации плана — поле `extra_actions`).
- Плавная интерполяция, амплитуды снижены (меньше дёрганости), шаги
  согласованы (руки/ноги противофазно).

## Новая демо-сцена

План: `voxel_engine/quality_plan.py` → `build_quality_cube_plan(duration=9)`.
Сюжет «Герой находит загадочный куб» — один герой, один предмет, небольшая
локация, без диалогов.

Ритм (9 с):
- 0–2 с — idle (стоит);
- 2–3.2 с — turn (поворачивает голову/корпус к кубу);
- 3.2–4.6 с — walk (подходит к кубу);
- 4.6–5.8 с — bend (наклоняется);
- 5.8–7.2 с — pickup (+ attach куба к руке на 6.6 с);
- 7.2–8.4 с — react (удивление, руки вверх);
- 8.4–9 с — hold (финальная выразительная поза с кубом).

## Запуск

```powershell
cd D:\ShortsFactory
$env:PYTHONPATH='src'
# превью 540×960
python -c "from pathlib import Path;from shortsfactory.config import load_config;from shortsfactory.generative.voxel_engine.quality_plan import build_quality_cube_plan;from shortsfactory.generative.engine_dispatcher import render_plan;print(render_plan(build_quality_cube_plan(duration=9,width=540,height=960),load_config(Path('.')),Path(r'output\_voxel_quality_v1\render_preview'),engine='voxel').status)"
# финальный 1080×1920
python -c "from pathlib import Path;from shortsfactory.config import load_config;from shortsfactory.generative.voxel_engine.quality_plan import build_quality_cube_plan;from shortsfactory.generative.engine_dispatcher import render_plan;print(render_plan(build_quality_cube_plan(duration=9,width=1080,height=1920),load_config(Path('.')),Path(r'output\_voxel_quality_v1\render_final'),engine='voxel').status)"
```

Визуальный контроль: сначала превью + контактные листы (`render_preview/`,
`render_final/contact_sheet_final.jpg`), затем финальный рендер.

## Артефакты (output/_voxel_quality_v1/)
- `render_final/final.mp4` — итоговое видео (1080×1920@30, H.264);
- `render_final/contact_sheet_final.jpg` — контактный лист;
- `render_final/render_voxel_quality_cube_01.py` — исходник Blender-сцены;
- `render_preview/` — превью 540×960 + контактный лист;
- `render_final_meta.json`, `animation_plan.json` — метаданные/план.
