# INTEGRATION_PLAN — blend3d → ShortsFactory pipeline

**Дата:** 2026-11-01 · **Статус:** host-слой изолирован и протестирован; точки подключения определены; в производственную цепочку пока ничего не переключено.

## Текущая производственная цепочка ShortsFactory

```
CLI / ask → NLP → Plan → Research → Scripts (Ollama) → Localization
  → Assets → Voice (Piper) → Timeline → Editing (FFmpeg) → Rendering
  → Metadata → Quality → Reports          (2D-режим: create --visual-mode animated
                                           blocky-create → blocky-движок v4 на Pillow)
```

blend3d НЕ заменяет ни одну из этих ступеней. Это **альтернативный визуальный
движок** (3D/Blender) и **общий QC-слой** для вертикальных 3D-короткометражек.

## Точки подключения (точные)

| # | Точка | Модуль ShortsFactory | Что подключаем | Статус |
|---|---|---|---|---|
| 1 | Приём идеи | `nlp.py` / `topics.py` | `blend3d.host.film_spec.plan_from_idea` как extractive-fallback планировщика битов | prepared, изолировано протестировано |
| 2 | LLM-планирование | `llm.py` (Ollama qwen) | промпт → JSON FilmSpec ( beats + действия); plan_from_idea остаётся fallback'ом | planned |
| 3 | Сцены/драйвер | новая ступень `RENDER_3D` в `jobs.py` | `blend3d.host.render_job.build_*_argv` → subprocess blender (по образцу существующих запусков) | prepared (argv-строители протестированы) |
| 4 | Кадрирование/камера | `animation/…` (blocky C7) | `CamRig` + `two_shot_override` — авто-кадрирование по позициям актёров (порт принципов C7 в 3D) | proven в производстве «Алмаз» |
| 5 | Звук | `music.py` / `blend3d.host.sound` | numpy-генератор перенесён в пакет: `render_events` + `events_from_timings` (конвертер timings → события) | done, 3 теста |
| 6 | QC | `quality.py` / `blocky_qc.py` | `blend3d.host.qc.verdict` — гейты READY/PARTIAL/FAILED для кадров/WAV | prepared, 12 тестов |
| 7 | Отчёты | `reporting.py` | секция blend3d в report.md (ссылки на кадры/логи) | planned |

## Адаптеры

- **Driver-скрипт**: Blender не импортирует shortsfactory — `blender_lib` полностью
  автономен (bpy). Мост — сгенерированный/выбранный драйвер-скрипт
  (`film_scene → film_bake → film_bake2`, см. производство «Алмаз»), запускаемый
  `blender -b -P driver.py -- build|preview|render`.
- **Timings.json → звук**: DONE (2026-11-01). `scripts/timings.json` — единый
  источник событий (шаги, удары кирки, трещины, обвал, искры); звуковой
  генератор читает его вместо дублирующих констант. Верифицировано: микс из
  timings.json бит-в-бит идентичен миксу поставленного фильма (md5 PCM совпадает).
  В пакете: `host.sound.events_from_timings(timings, chords)` + тесты.
- **FilmSpec ↔ storyboard**: формат сознательно похож на
  `animation/storyboards/*.json`; конвертер FilmSpec → blocky-storyboard —
  будущая работа, если потребуется единая форма.

## Зависимости

- Blender ≥ 5.2 (внешний, `D:\Blender\blender.exe`, переменная `BLENDER_EXE`).
- numpy/Pillow — уже есть в venv ShortsFactory. Новых зависимостей НЕ добавлено.
- subprocess для запуска Blender — только в jobs.py/CLI (по конвенциям проекта);
  `render_job` отдаёт валидированный argv.

## Статус интеграции

- **Готово и протестировано изолированно:** host-модули (spec/plan, argv, QC),
  пресеты, 22 pytest-теста, Blender smoke-сцена, два реальных производства.
- **Не подключено к pipeline:** ничего. Существующие режимы (image + animated
  blocky) не изменены; `pytest tests/test_blend3d_toolkit.py` — новый
  независимый набор (запускать его, а не полный suite).

## Риски совместимости

1. Blender-специфичные API (5.2) — зафиксировать версию в конфиге джоба.
2. Длительность рендера 3D (~3.5–5 с/кадр на RTX 3050 Ti для ~700-блочной сцены)
   — вертикальный шорт на 50 с ≈ 1.5–2 ч; для pipeline нужен очередь-менеджмент
   (frame-чанки + resume по счётчику кадров — `frames_done()` уже готов).
3. Кириллические пути — драйверы автора с литеральными путями; для pipeline
   передавать пути ASCII-параметром в argv (уже поддержано: `-o`, `--` mode).
4. Дублирование формул motion в тестах (mathutil-подход) — при изменении
   blender_lib/motion.py синхронизировать тесты (отмечено в докстринге).

## Дальнейшие шаги (по возрастанию усилий)

1. ~~timings.json из драйвера → звуковой генератор~~ — DONE (2026-11-01, бит-идентичная верификация).
2. ~~blend3d.host.sound — перенос numpy-генератора~~ — DONE (render_events + events_from_timings + 3 теста).
3. LLM-планировщик поверх plan_from_idea (через существующий llm.py).
4. Ступень RENDER_3D в jobs.py с resume по кадрам и preview-гейтом.
5. Конвертер FilmSpec → blocky-storyboard для совместного использования сцен.
