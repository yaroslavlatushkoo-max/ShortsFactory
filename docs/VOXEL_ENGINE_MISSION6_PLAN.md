# MISSION 6 — Voxel Animation Engine v1 (план работ)

Дата: 2026-10-27. Проект: `D:\ShortsFactory`. Движок 3D: `D:\Blender\blender.exe` (5.2.3 LTS, установлен, адаптер уже находит его через пользовательский PATH).

## Цель
Добавить в ShortsFactory процедурный **Voxel Animation Engine v1**: настоящие 3D-мультфильмы в кубической эстетике (Minecraft-подобная, но полностью собственная геометрия/материалы). От идеи до вертикального MP4 (1080×1920, 30 fps, H.264+AAC) без платных API и облачной генерации.

## Действующие ограничения
- Не ломать Procedural 2D Engine, Blender Adapter, Animation Plan, free-create, Story Studio.
- `free_local`: никаких облачных вызовов, никаких секретов.
- Blender — единственный тяжёлый внешний инструмент (уже установлен); новые тяжёлые пакеты/модели не ставить.
- Честные статусы READY/PARTIAL/FAILED; не выдавать «готово» без проверки.

## Что сначала изучить (аудит — уже выполнен)
- `src/shortsfactory/generative/` — пакет generative.
- `animation_plan.py` — единый формат AnimationPlan (PlanCharacter/PlanObject/PlanAction/PlanEvent/PlanCameraKey/PlanVoiceLine). `ENGINE_CHOICES=(procedural,blender,auto)`. `validate()/ensure()`.
- `engine_dispatcher.py` — `render_plan(plan,config,out_dir,engine)` → DispatchResult(status,engine_used,video,…). Сейчас знает только proceduralural/blender/auto.
- `blender_adapter.py` — `blender_executable()/blender_available()`, `render_blender`, `build_blender_script` (простой примитивный humanoid). Его интерфейс переиспользуем; voxel сделаем отдельным, более богатым генератором скрипта.
- `freeanim.py` — `build_plan_for_idea` (сценарий «Майя и камень», timeline долями), `run_free_create(..., engine=ENGINE_AUTO)`, `run_qc`. `free_local` не создаёт облачный backend.
- `cli.py` — `free-create` (--script/--title/--output/--engine(choices=ENGINE_CHOICES)/…). Выбор движка уже пробрасывается в `run_free_create` → `render_plan`.
- Установленный Blender находит по PATH (`shutil.which("blender")` → `D:\Blender\blender.exe`). Проверено: рендер работает (smoke PNG + `final.mp4` через старый адаптер).

## Что создаётся / меняется

### Новый пакет `src/shortsfactory/generative/voxel_engine/`
- `__init__.py` — экспорт.
- `voxel_materials.py` — палитра + генерация B-кода материалов (Principled BSDF, emission).
- `voxel_rig.py` — константы / иерархия рига (root/torso/head/arm_l/arm_r/leg_l/leg_r).
- `voxel_scene.py` — генерация кода кубического мира: земля, трава, деревья-блоки, камни, освещение/фон. Детерминированно.
- `voxel_character.py` — генерация кода персонажа `VoxelHero` (кубические голова/туловище/руки/ноги, глаза/брови/рот).
- `voxel_animation.py` — библиотека действий: idle/walk/run/jump/turn/bend/reach/pickup/hold/react/fall (+recover). Ключевые кадры по `(start,duration,params)`.
- `voxel_interactions.py` — захват предмета: перемещение к точке захвата, reparent к руке, усиление свечения при реакции.
- `voxel_camera.py` — камера: планы wide/medium/close, сопровождение, 9:16 safe framing, маппинг PlanCameraKey.
- `voxel_renderer.py` — сборка единого самодостаточного Blender-скрипта из плана; запуск `blender --background --python`; ffmpeg-кодирование. Возвращает результат в формате DispatchResult.
- `voxel_validation.py` — доп. валидация плана до рендера (ссылки на объекты/персонажей, точки захвата, отрицательная длительность) + пост-рендер QC.

### Изменения существующих файлов
- `animation_plan.py`: `ENGINE_VOXEL="voxel"`, включить в `ENGINE_CHOICES` (обратно совместимо).
- `engine_dispatcher.py`: маршрутизация `engine=="voxel"` → `render_voxel`; недоступность Blender → честный PARTIAL (без скрытого fallback на облако/картинки).
- `freeanim.py`: автоматически подхватит новый engine через `ENGINE_CHOICES`; `run_qc` оставим движко-нейтральным.
- `cli.py`: выбор `--engine voxel` появится автоматически (help-текст обновим при необходимости).
- `docs/`: `VOXEL_ENGINE_V1.md` (архитектура+запуск), `VOXEL_ENGINE_V1_RESULTS.md` (результаты+сравнение движков).

### Демо `output/_voxel_engine_v1/`
Сюжет «Необычный светящийся куб» (12–18 с): герой на поляне → замечает светящийся куб → идёт → наклоняется → поднимает → удерживает → рассматривает → куб светится ярче → удивление → крупный план.
- Превью 360×640 или 540×960 → итог 1080×1920@30, H.264+AAC, Piper (ru), SRT/ASS.

### Тесты `tests/test_voxel_engine.py`
Mock-тесты (без запуска Blender): генерация скрипта, валидация плана, маршрутизация dispatcher, отказ без Blender, обратная совместимость. Отдельно — реальный интеграционный рендер через установленный Blender (демо).

## Порядок
1. Аудит/план (этот документ). 2. Модули voxel_engine. 3. Интеграция в animation_plan/dispatcher/cli. 4. Тесты. 5. Демо-рендер (превью → полный). 6. Документация. 7. Отчёт.
