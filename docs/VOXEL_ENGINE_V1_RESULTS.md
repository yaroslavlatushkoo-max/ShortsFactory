# Voxel Animation Engine v1 — результаты

Дата: 2026-10-27.

## Что реализовано
Новый процедурный 3D-движок `voxel_engine` (см. `docs/VOXEL_ENGINE_V1.md`),
интегрированный в `free-create` как `--engine voxel`. Поддерживает настоящую
кубическую сцену, персонажа `VoxelHero`, действия, взаимодействие с предметом
и смену планов, рендеринг локальным Blender (EEVEE) и кодирование FFmpeg.

### Созданные файлы
- `src/shortsfactory/generative/voxel_engine/` — новый пакет
  (`voxel_materials.py`, `voxel_rig.py`, `voxel_scene.py`,
  `voxel_character.py`, `voxel_animation.py`, `voxel_interactions.py`,
  `voxel_camera.py`, `voxel_renderer.py`, `voxel_validation.py`,
  `__init__.py`).
- `src/shortsfactory/generative/animation_plan.py` — добавлен `ENGINE_VOXEL`.
- `src/shortsfactory/generative/engine_dispatcher.py` — маршрутизация `voxel`.
- `src/shortsfactory/generative/freeanim.py` — озвучка Piper для voxel/blender.
- `tests/test_voxel_engine.py` — 9 тестов.
- `docs/VOXEL_ENGINE_MISSION6_PLAN.md`, `docs/VOXEL_ENGINE_V1.md`,
  `docs/VOXEL_ENGINE_V1_RESULTS.md` (этот файл).

## Демонстрационный мультфильм
Сюжет: «Необычный светящийся куб» (Майя в блочном лесу находит светящийся
куб, подходит, наклоняется, поднимает, удерживает и удивляется).

**Итоговый файл:** `output\_voxel_engine_v1\final_audio.mp4`
- 1080×1920, 30 FPS, H.264 + AAC (аудио), длительность 15.0 с.
- Subтитры: `output\_voxel_engine_v1\subtitles\ru.srt` (+ ASS).
- План: `output\_voxel_engine_v1\animation_plan.json`.
- Рендер-кадры и контактный лист: `output\_voxel_engine_v1\render\`.
- Отчёт: `output\_voxel_engine_v1\free_report.json`/`free_report.md`.

Технический QC: `READY` (decode_ok=true, streams_ok=true, 1080×1920@30,
motion_ok=true, props_interacted=true, audio_present=true,
subtitles_parsed=true).

## Сравнение движков (по демо 15 с / 1080×1920)

| Показатель | Procedural 2D | Blender (старый) | Voxel (новый) |
|---|---|---|---|
| Подготовка сцены | мгновенно | скрипт | скрипт (детерминированно) |
| Время рендера | ~137 с | ~нано/сц (простой) | ~460 с (полный 3D, 450 кадров) |
| MP4 | H.264+AAC | H.264 | H.264+AAC |
| FPS / разрешение | 30 / 1080×1920 | 30 / заданное | 30 / 1080×1920 |
| Технический QC | READY | OK | READY |
| Настоящее движение | да (2D-риг) | да (примитивы) | да (кубический риг) |
| Ходьба | есть (проверяется авто) | есть | есть (визуально; авто-проверка не настроена) |
| Захват предмета | есть (проверка привязки) | есть | есть (прикрепление к руке) |
| Читаемость действий | средняя | низкая | высокая (объём, силуэт) |
| Повторяемость рендера | детерминированно | да | да (фикс. seed) |
| Потребление ресурсов | низкое | низкое | среднее (EEVEE, окна 4 ГБ VRAM) |

Честно: **voxel не всегда «лучше»**. Для быстрых простых сцен Procedural 2D
быстрее (~3×) и уже имеет авто-проверку ходьбы/захвата. Voxel даёт объёмную
3D-эстетику и лучше читаемые силуэты, но требует Blender, рендерит дольше, а
некоторые авто-проверки (walk, attachment) для него пока не построены.

## Проблемы / ограничения
- Авто-проверка «character_walks» и «attachment_verified» для voxel пока не
  реализована (для 2D-движка она есть). Для voxel это требует визуального
  осмотра; в отчёте помечено как `false`.
- `audio_video_match=false`: реплики (Piper) короче длительности видео, фоновая
  музыка отсутствует — это ожидаемо и честно отражено в QC.
- Камера/движение проверяются автоматически по уникальности кадров и валидации
  плана; качество «мультфильма» требует человеческого просмотра (субъективно).
- Взаимодействие «рука-предмет» нацелено на контролируемую точку захвата, а не
  на точный IK-контакт.

## Что является заглушкой
- `fall`/`recover` реализованы, но не используются в демо.
- Точный IK (контакт кисти с предметом) не реализован — используется
  управляемая точка захвата.
- Авто-проверка ходьбы/привязки для 3D (аналог 2D-проверок) не написана.

## Запуск тестов
```powershell
cd D:\ShortsFactory
$env:PYTHONPATH='src'
python -m pytest tests/test_voxel_engine.py -q   # 9 тестов
python -m pytest tests --tb=no -q                # полный набор (зелёный)
```
Полный набор тестов проекта проходит без регрессий (старые движки сохранены).
