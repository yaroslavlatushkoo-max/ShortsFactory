# Voxel Animation Engine v1 — архитектура и запуск

Режим `free_local`. Новый процедурный 3D-движок для оригинальных кубических
мультфильмов (стиль «voxel art», полностью собственная геометрия/материалы —
без ассетов Minecraft). Рендеринг — локальный Blender (EEVEE). Облачные API
не используются.

## Стек
- **Blender** (установлен: `D:\Blender\blender.exe`, версия 5.2.3 LTS).
- **FFmpeg** — кодирование кадров в MP4.
- **Ollama** — расширение идеи в сценарий (локально).
- **Piper TTS** — озвучка русских реплик.
- Остальное — Python/NumPy (стандартные библиотеки проекта).

## Что это такое
`Voxel Engine` потребляет общий `AnimationPlan` (как и другие движки) и
генерирует **один самодостаточный Blender-скрипт**, который полностью строит
сцену, персонажа, анимацию, камеру и взаимодействия, рендерит
PNG-последовательность и кодирует вертикальный MP4. Никакого сетевого
доступа и никаких платных вызовов.

## Модули (`src/shortsfactory/generative/voxel_engine/`)
| Модуль | Назначение |
|---|---|
| `voxel_materials.py` | палитра + код материалов (Principled BSDF, emission) |
| `voxel_rig.py` | иерархия рига: root/torso/head/arm_l/arm_r/leg_l/leg_r |
| `voxel_scene.py` | кубический мир: земля, трава, деревья-блоки, камни, свет, фон |
| `voxel_character.py` | сборка `VoxelHero`: голова/туловище/руки/ноги, глаза/брови/рот |
| `voxel_animation.py` | библиотека действий + ключевые кадры |
| `voxel_interactions.py` | объекты, захват/прикрепление к руке, свечение |
| `voxel_camera.py` | камера 9:16, планы wide/medium/close, сопровождение |
| `voxel_renderer.py` | сборка единого скрипта, запуск Blender, ffmpeg |
| `voxel_validation.py` | валидация плана до рендера + пост-QC |

## Поддерживаемые действия
`idle`, `walk`, `run`, `jump`, `turn`, `bend`, `reach`, `pickup`, `hold`,
`react`, `fall`, `recover`. Анимация строится на ключевых кадрах и
интерполяции; зависят от длительности, позиции и параметров. Движение
конечностей и тела — реальное (не «слайд-шоу» камеры).

## Интеграция
- `animation_plan.py`: добавлен движок `ENGINE_VOXEL="voxel"` в
  `ENGINE_CHOICES` (обратно совместимо).
- `engine_dispatcher.py`: `render_plan(..., engine="voxel")` маршрутизирует
  на voxel-рендерер. Если Blender недоступен → честный `PARTIAL` (без
  скрытого fallback).
- `freeanim.py`: для `voxel`/`blender` после рендера добавляется озвучка
  Piper (`mix_audio`) в итоговый `final_audio.mp4`.
- `cli.py`: `free-create --engine voxel` (выбор появляется автоматически).

## Запуск демо
```powershell
cd D:\ShortsFactory
$env:PYTHONPATH='src'
python -m shortsfactory.cli free-create `
  --script "Майя находит светящийся куб в блочном лесу" `
  --title "Необычный светящийся куб" `
  --output "output\_voxel_engine_v1" `
  --duration 15 --engine voxel
```

Для быстрой проверки используйте `--duration 6` и при желании уменьшите
разрешение в плане (по умолчанию 1080×1920).

## Выбор движка
- `--engine procedural` — старый 2D-движок (всегда доступен, офлайн).
- `--engine blender` — прежний Blender-прототип (примитивный humanoid).
- `--engine voxel` — новый кубический 3D-движок (эта миссия).
- `--engine auto` — Blender, если доступен, иначе 2D.

## Артефакты
- `render/final.mp4` — видео до озвучки;
- `final_audio.mp4` — итоговое видео (H.264+AAC);
- `render/frames/` — PNG-кадры;
- `render/contact_sheet.jpg` — контактный лист;
- `animation_plan.json` — план сцен;
- `subtitles/ru.srt`, `subtitles/ru.ass`;
- `free_report.json` / `free_report.md` — отчёт.
