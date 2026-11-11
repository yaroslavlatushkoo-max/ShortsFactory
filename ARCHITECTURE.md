# Architecture

## Общая схема

```
CLI / ask
  → NLP-разбор
  → Plan
  → Research (Wikipedia + пользовательские материалы)
  → Scripts (Ollama / extractive fallback)
  → Localization (RU → EN adaptation)
  → Assets (NASA / Commons / Openverse / Pillow-карточки)
  → Voice (Piper, кэш предложений)
  → Timeline (точные начала сцен и субтитров)
  → Editing (ASS/SRT, музыка, FFmpeg)
  → Rendering (1080×1920, H.264/AAC)
  → Metadata (title/description/tags/thumbnail)
  → Quality (technical + content + visual)
  → Reports
```

## Модули

| Модуль | Назначение |
|---|---|
| `cli.py` | argparse-команды, вывод, коды возврата |
| `nlp.py` | правила разбора свободного текста |
| `jobs.py` | оркестратор этапов, retry, resume, remake |
| `topics.py` | профили, нормализация тем, выбор без повторов |
| `research.py` | Wikipedia API, источники, corpus, отчёт |
| `scripts.py` | LLM-промпты, валидация, extractive fallback |
| `media.py` | поиск, фильтр лицензий, скачивание, атрибуция |
| `visuals.py` | Pillow-фоны и шаблоны сцен |
| `tts.py` | Piper, кэш, манифест предложений |
| `timeline.py` | тайминг сцен, narration, окна субтитров |
| `subtitles.py` | разбиение, переносы, ASS/SRT |
| `music.py` | процедурная музыка без сторонних сэмплов |
| `editing.py` | zoompan, xfade, ASS, аудиомикс, FFmpeg |
| `quality.py` | технический, содержательный и визуальный QC |
| `metadata.py` | названия, описания, хештеги, обложки |
| `reporting.py` | отчёт проекта и снимок окружения |
| `storage.py` | SQLite, этапы, события, восстановление |
| `doctor.py` | проверка окружения |
| `config.py` | TOML-конфигурация и пути |
| `animation/scene.py` | схема v1/v2, события привязки, зависимости, валидация |
| `animation/looks.py` | загрузка и валидация JSON-ассетов внешности |
| `animation/characters/*.json` | версионируемые внешности персонажей |
| `animation/rig.py` | cut-out риг `humanoid_v1`, прямая кинематика, точки привязки |
| `animation/motion.py` | библиотека поз, действия, смешивание, выражения |
| `animation/voice.py` | реплики TTS и RMS-огибающая для липсинка |
| `animation/renderer.py` | кадры Pillow, параллакс-слои, камера, привязка объектов, FFmpeg |
| `animation/engine.py` | рендер сцены, доказательства движения и привязки, отчёт |
| `animation/art.py` | декларативные SVG-ассеты частей тела и лиц |
| `animation/svg.py` | небольшой SVG-рендерер без внешних зависимостей |
| `animation/environments.py` | многослойные окружения и параллакс |
| `animation/storyboard.py` | компилятор структурированного сториборда в сцену v2 |
| `animation/storyboards/*.json` | версионируемые постановки (пилоты v2–v4) |

## Состояния

`QUEUED → PLANNING → RESEARCH → SCRIPTING → LOCALIZATION → ASSETS → VOICE →
EDITING → RENDERING → METADATA → QUALITY_CHECK → READY`

Дополнительно: `PARTIAL`, `FAILED`, `INTERRUPTED`.

Каждый этап имеет запись в таблице `stages` со статусом, временем и деталями.
После аварийного завершения `Storage.recover_interrupted()` переводит
незавершённые задания в `INTERRUPTED`, а `JobService.resume_interrupted()` —
продолжает с первого незавершённого этапа.

## Анимационный режим (Animation Engine v4)

Второй способ визуального производства, независимый от режима изображений.
Анимированный проект выбирается флагом `create --visual-mode animated
--storyboard <json>`: общий конвейер пропускает исследование, сценарий,
локализацию и озвучку, компилирует готовый сториборд и проводит его через
рендер, метаданные и QC. Прямые команды движка:

```
animate --scene <json> [--out DIR]
animate --storyboard <json> [--out DIR]
```

```
storyboard.json
  → compile_storyboard (биты → действия, события, реплики)
  → scene.compiled.json
  → load_scene / validate
  → build_voice_track (Piper → WAV 48 кГц → RMS-огибающая)
  → AnimationRenderer (30 fps, Pillow, параллакс-слои, камера, привязка)
  → rawvideo pipe → FFmpeg (H.264 + AAC, 1080×1920)
  → contact_sheet.jpg + report.md/json + characters.json
```

Персонаж v3 собирается из внешних SVG-ассетов: `art.py` читает блок
`graphics` во внешности, `svg.py` рендерит подмножество SVG, а `rig.py`
компонует части по прямой кинематике. Это заменяет рисование стилей кодом в
`rig.py`; при отсутствии ассетов остаётся совместимый legacy-режим
примитивов. Окружения (`environments.py`, `animation/environments/*.json`) —
декларативные манифесты из трёх слоёв (`back`, `mid`, `front`) с разной
глубиной; камера сдвигает слои по-разному, что даёт параллакс без 3D.

Кадр собирается из стадии, увеличенной в 1.25 раза. `_crop_box` выбирает окно
по центру и зуму камеры и никогда не выходит за стадию: при `zoom ≈ 1.0` окно
совпадает со стадией, при большем зуме — сужается вокруг центра. Поле `MARGIN`
(0.04 стадии) ограничивает максимальный сдвиг окна, поэтому широкие позы у
краёв сцены не обрезаются, а чёрных полос по краям не появляется.

`--storyboard` сохраняет исходник (`storyboard.source.json`) и скомпилированную
сцену (`scene.compiled.json`); сцена правится вручную и рендерится через
`--scene`. `--scene` без изменений принимает старые файлы `version: 1`.

Модель сцены:

- `version` — `1` (исходный формат) или `2` (события, зависимости, внешности).
  Неизвестная версия отклоняется с перечислением поддерживаемых.
- `characters[]` — `id`, `name`, `look`, `x/y`, `scale`, `facing`, `z`,
  `appear_at`, необязательная палитра-оверрайд.
- `objects[]` — `id`, форма (`orb`, `crate`, `book`, `lamp`, `gem`, `cup`),
  цвет, `z`, `appear_at`, ключевые кадры `x/y/scale/rotation`.
- `actions[]` — `character`, `action`, `start`, `duration`, `params`, а также
  `id`, `after[]` и `gap` (v2). `after` разрешается в абсолютные начала
  топологической сортировкой; циклы и неизвестные ссылки — ошибки.
- `events[]` (v2) — `attach`, `detach`, `transfer` с `t`, `object`,
  `character`/`target`, точкой рига, смещением, поворотом, масштабом,
  скоростью и вращением при отпускании.
- `camera` — ключевые кадры центра и зума; не влияет на позы.
- `voice` — язык, усиление и реплики `{character, text, start}`.
- `contact_times` — моменты для контактного листа.

Внешность (`looks.py`) — отдельный JSON-ассет: палитра, пропорции, причёска,
глаза, брови, нос, рот, одежда, аксессуары. Сцена ссылается на файл по имени
(`"look": "milo"`), риг знает только объявленные стили. Ассет и его версия
попадают в отчёт, поэтому старый ролик можно сопоставить с точной внешностью.
Без `look` используется палитра по умолчанию (совместимость с v1).

Риг `humanoid_v1`: таз → торс → голова; плечо → предплечье → кисть;
бедро → голень → стопа. Углы локальные, прямая кинематика даёт мировые точки.
Точки привязки — `hand_l`, `hand_r`, `forearm_l`, `forearm_r`, `head`, `chest`;
`rig.attachment_transform` возвращает позицию и угол родителя, поэтому предмет
поворачивается вместе с кистью, а не остаётся вертикальным.

Действия строятся из библиотеки поз `POSES` (`neutral`, `reach_forward`,
`offer`, `carry`, `point`, `surprise`, `joy`, `confused`, `annoyed`, `stop`).
Поза задаёт согласованный набор углов; интенсивность масштабирует её к
нейтральной, `ramp_in`/`ramp_out` дают плавный вход и выход. Действия:
`idle`, `walk`, `stop`, `turn`, `wave`, `reach`, `offer`, `carry`, `point`,
`react`, `look_around`, `shrug`, `laugh`, `stumble`, `surprise`, `joy`,
`confused`, `annoyed`. Выражение лица
(`neutral`/`surprise`/`joy`/`confused`/`annoyed`), моргание и форма рта
передаются в отрисовку отдельными каналами.

Привязка в рендерере: пока объект удерживается, его позиция каждый кадр
выводится из точки рига с учётом смещения и поворота; ключевые кадры объекта
игнорируются. `transfer` атомарно меняет владельца, `detach` освобождает
объект, после чего он движется с постоянной скоростью и вращением (детерминизм,
без физического солвера). Удерживаемый предмет получает `z` выше персонажа,
поэтому кисть не перекрывает его неправильно. Валидация запрещает повторный
захват, отпускание без захвата, передачу самому себе, неизвестные точки рига,
два события одного объекта в один момент и события в сцене `version: 1`.

Сториборд (`storyboard.py`) — типизированный JSON с ограниченным набором
команд (`walk_to`, `stop`, `turn_to`, `reach`, `take`, `offer`, `give`,
`carry`, `drop`, `point`, `wave`, `react`, `look_around`, `shrug`, `laugh`,
`stumble`, `surprise`, `joy`, `confused`, `annoyed`, `say`). Каждый бит имеет
`id`, `actor`, `duration` и `after`; `take` создаёт `attach`, `give` —
`transfer`, `drop` — `detach`, `say` — реплику. Неизвестная команда или
известная, но нереализованная, даёт русскую ошибку со списком поддерживаемых;
свободный текст не компилируется.

В общем конвейере (`jobs.py`) режим `animated` пропускает статические этапы
(`ANIMATED_STAGES`), компилирует сториборд, рендерит сцену, пишет SRT по
репликам и прогоняет обязательные проверки: декодирование, движение
конечностей, привязку предметов, кадрирование, контроль поз, загрузку
SVG-ассетов и липсинк.
Сейчас поддерживается только русская версия; английская адаптация — следующий
этап.

Детерминизм: нет случайных чисел вне `seed`, нет обращений к сети при рендере;
повторный запуск даёт те же кадры, тот же звук и тот же SHA-256 файла.

Проверки (`engine.py`):

- `verify_motion` — разница кадров при замороженной камере (> 1.0),
  перемещение корня (порог 30 px), кистей/стоп/головы (порог 8 px),
  перемещение объектов.
- `verify_attachment` — для каждого интервала удержания расстояние от центра
  предмета до точки рига должно быть постоянно (разброс ≤ 3 px) и совпадать
  с заданным смещением; скачок при передаче ≤ 160 px, при отпускании ≤ 40 px.
- `verify_lip_sync` — рот открывается во время реплики (максимум > 0.2),
  меняется (разброс > 0.05) и закрыт после неё (< 0.2).
- `verify_framing` — голова, кисти и стопы остаются внутри кадра на каждом
  кадре сцены: точки рига проецируются через окно камеры, для кистей оставлен
  запас 48 px, для головы и стоп — 24 px (на 1080×1920).
- `verify_pose_quality` — ищет перекрещенные через корпус руки: если локоть
  вынесен за ширину корпуса, а кисть ушла на другую сторону и попала в полосу
  тела, кадр считается suspect. Ходьба и бег исключаются, потому что там
  махи рук законны.
- `asset_report` — описывает SVG-ассеты, выражения, слои и ракурсы каждого
  персонажа; отсутствие ассетов попадает в предупреждения.
- `camera_shots` — классифицирует ключевые кадры камеры (`wide`/`medium`/
  `close`) и попадает в отчёт.

## Контрольные точки

- `workspace/<project_id>/project.json` — снимок проекта.
- `plan.json`, `request.json`, `command.txt` — входные параметры.
- `research/sources.json`, `research/corpus.txt`.
- `scripts/<lang>.json` и `.md`.
- `assets/<lang>/media_manifest.json`, `visuals.json`.
- `audio/<lang>/manifest.json`, `timeline.json`, `narration.wav`.
- `edit/<lang>.json`, `renders/<lang>.mp4`.
- `quality/<lang>.json`, `output/<project>/reports/*`.
- `output/_animation_engine_v1/` — первый пилот: `pilot.mp4`, `frames/`,
  `audio/`, `contact_sheet.jpg`, `report.*`, `pilot_scene.json`,
  `characters.json`.
- `output/_animation_engine_v2/` — пилот v2: `pilot.mp4`,
  `contact_sheet.jpg`, `report.*`, `scene.compiled.json`,
  `storyboard.source.json`, `pilot_scene.json`, `characters.json`,
  `audio/voice.wav`, `frames/`.
- `output/_animation_engine_v3/` — пилот v3 (19 с): те же артефакты плюс
  отчёт о SVG-ассетах, слоях окружения и кадрах камеры; исходники —
  `animation/storyboards/pilot_room_v3.json`,
  `src/shortsfactory/animation/art/parts/*.svg`,
  `src/shortsfactory/animation/environments/*.json`.
- `output/_animation_engine_v4/` — пилот v4 (20 с, SHA-256
  `F79A566C…3479A17`): те же артефакты плюс покадровый контроль кадрирования
  и контроль поз в `report.json`; исходник —
  `animation/storyboards/pilot_room_v4.json`.
- SQLite: `database/shortsfactory.db` (WAL).

Этап пропускается только если он отмечен `done` **и** его ключевые артефакты
существуют. Это защищает от ложного пропуска после ручного удаления файлов.

## Тайминг

Для сцены `i`:

```
d_i = lead + speech_i + tail_i
v_i = v_{i-1} + d_{i-1} - transition
audio_start_i = v_i + lead
```

Последняя сцена получает больший `tail`, при необходимости добавляется удержание
кадра. Речь не ускоряется ниже допустимого предела; если она длиннее цели, это
явно фиксируется в `fit_note`.

## QC

- **Technical (mandatory):** файл, контейнер, потоки, длительность, разрешение,
  fps, профиль, полное декодирование, громкость, чёрные начало/конец, геометрия
  ASS, отсутствие временных файлов.
- **Content (mandatory):** сценарий, соответствие теме (с учётом языка),
  источники, озвучка, повторы.
- **Visual:** контактный лист, тёмные кадры (advisory), дубликаты (advisory),
  композиция, читаемость (advisory + ручная проверка).

`READY` ставится только при отсутствии обязательных ошибок. Если файлы есть, но
проверки не пройдены — `PARTIAL`.

## Конфигурация

- `config/config.toml` — видео, TTS, LLM, исследование, медиа, QC.
- `config/profiles.toml` — 9 тематических профилей с сидами.
- Переменные окружения: `SHORTSFACTORY_ROOT`, `SHORTSFACTORY_CONFIG`,
  `SHORTSFACTORY_PROFILES`, `SHORTSFACTORY_FFMPEG`, `SHORTSFACTORY_FFPROBE`.
