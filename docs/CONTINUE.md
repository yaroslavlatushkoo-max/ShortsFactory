# Продолжение работы

**Обновлено:** 2026-10-26
**Текущая фаза:** приёмка пилота Animation Engine v4 (режиссура сцены, QC
кадрирования и поз, проверенная передача предмета).
**Версия:** 1.0.0 (метка движка в отчёте — `animation-engine-v4`)

## Указатель: F2 — автопостановка взаимодействий (2026-10-31)

Сеанс аудита F1 + внедрение F2-фиксов (аудит: [BLOCKY_FILM_AUDIT.md](BLOCKY_FILM_AUDIT.md)):
- `blocky_scene.py`: куб/ключ/сундук покоятся на земле без явного z; пресеты
  `key`, `chest` (+ metallic в шаблоне); бокс хранит логическое имя пропса (`prop`).
- `blocky_story.py`: промпт = полный словарь движка (42 глагола) + правила
  взаимодействий; `stage_interactions()` — привязка действий рук к предметам,
  придвижение в зону досягаемости, авто-carry, авто-открытие двери;
  `ensure_door_event()` — идея «открыть дверь» гарантирует событие; repair-loop
  (материализация пропсов из params, синонимы глаголов, ремонт актёров, немые сцены).
- `blocky_continuity.py` + `blocky_production.py`: исчезновение актёра —
  фиксируемая проблема; continuity-issues -> PARTIAL (гейт).
- `blocky_props.py`: подход предмета к кисти масштабируется дистанцией
  (MAX_APPROACH_SPEED 1.2 м/с), окно подлёта заканчивается в момент grab;
  release < 0 = «нести до конца сцены».
- `blocky_qc.py`: hover_warn 0.08 / drift_warn 0.03 (калибровка по реальной
  походке), props-шаг считается на кадр (не на кейфрейм).

Фильмы: `output/_f1_acceptance/run_fixed/film.mp4` (дверь открывается по событию,
PARTIAL — честный гейт по некогерентной LLM-истории), `output/blocky_llm_after/film.mp4`
(LLM-история с сундуком: carry работает), `output/blocky_new_idea_v3/film.mp4`
(новая идея: сундук+ключ+куб, оба актёра). Тесты зелёные (~320 без integration).

## Указатель: аудит трёх blocky-фильмов (2026-10-30)

Доказательный аудит «Двери в пещере» RU/EN (45 с) и генеративного «Огонёк» (24 с):
24 проблемы (4 CRITICAL), 6 корневых причин, программа миссий F1–F7:
[BLOCKY_FILM_AUDIT.md](BLOCKY_FILM_AUDIT.md). Ключевое: дверь открывается в каждой
сцене (дефолт open_frame), куб генеративного фильма не виден в кадре, C9 не начат.

## Указатель: универсальный blocky-движок с диалогом, Mission 1 (2026-10-30)

Программа из 15 миссий по фазам A–E (фундамент → диалог → режиссура →
автоматизация → валидация) и контракты модулей: [`MASTER_ENGINE_ARCHITECTURE.md`](MASTER_ENGINE_ARCHITECTURE.md).

Mission 1 (A1) сделано: у блочных персонажей появилось настоящее лицо —
полость рта на `head`, пластина челюсти на `jaw`, уголки на `lip_l`/`lip_r`,
брови на `brow_l`/`brow_r` (материал `brow`); `BlockyScene` получил поле
`dialogue` (реплика → `voice_lines` персонажа → виземы) и пишет
`subtitles/<lang>.srt` через `Cue`/`write_srt`. Демо-сцена
`scenes/blocky/dialogue_demo.json` («Странная дверь», 12 с, 6 реплик,
эмоции worried/surprised/confused/angry/happy), рендер
`output/_quality_investigation/blocky_m1/run1/` — status OK, 0.66 с/кадр,
дрейф стоп 0.0, 0 пустых кадров. Озвучки пока нет: длительности реплик
заданы вручную (Mission 2 — TTS до рендера).

## Указатель: голос в blocky — Mission 2 (A2, 2026-10-30)

Реплики озвучиваются **до** рендера: `BlockyScene.render()` синтезирует их
через Piper, подставляет измеренные длительности и только потом строит кадры,
поэтому рот, субтитры и звук живут на одной шкале времени. Два голоса:
Steve — `ru_RU-dmitri-medium` (+1.5 полутона), Alex — `ru_RU-irina-medium`
(медленнее, тише, словарь «что→што»); для EN — `en_US-ryan-high` и
`en_US-libritts_r` (speaker 12). `schedule_dialogue(gap=0.18)` не даёт репликам
накладываться (авторские слоты короче синтеза). Микс: голоса + процедурный SFX
(0.45) + амбиент forest/cave/meadow (0.16), лимитер `alimiter=...:level=false`
(иначе пик снова 1.0) и обязательный `apad` (иначе `-shortest` обрезал видео).
Демо-сцена `scenes/blocky/voice_engine_demo.json` («Странный звук», 16.6 с,
6 реплик, 4 плана), рендер `output/_quality_investigation/blocky_m2/run2/` —
status OK, 0.63 с/кадр, peak 0.95 без клиппинга, видео и аудио ровно 16.6 с,
6 неперекрывающихся субтитров. 49 тестов зелёные. Ограничение: липсинк пока
текстовый (виземы из букв) — Mission 3 (A3) добавит амплитуду из звука.

## Указатель: lip-sync по реальному аудио — Mission 3 (A3, 2026-10-30)

`blocky_voice.py` измеряет **настоящий WAV**: `speech_envelope` даёт громкость
каждые 20 мс, `speech_segments` — речевые спаны. Эти данные едут в реплику
(`apply_timings`), затем в `voice_lines` персонажа (`_distribute_dialogue`) и в
`apply_visemes`, который распределяет графемные виземы **только внутри спанов**
(`viseme_track(..., segments=...)`) и масштабирует рот по громкости
(`gain = 0 при уровне < 0.06`). Рот закрыт в паузах записи, открыт на речи,
эмоция не стирается (каналы аддитивные). Демо `scenes/blocky/lipsync_demo.json`
(«Оно дышит», 3 реплики, 4 плана) → `output/_quality_investigation/blocky_m3/run1/`:
status OK, 360 кадров, 0.5988 с/кадр, peak 0.95 без клиппинга, видео и аудио
12.000 с, 3 субтитра без наложений. На кадрах 42/66 (паузы внутри реплики)
`jaw_open = 0`, на 50/80 — открыт; в третьей реплике 26 из 53 кадров с
`jaw > 0.05`, пик 0.242. 96 тестов зелёные. Ограничение: виземы графемные —
фонемного анализа нет, аудио даёт только тайминг, паузы и громкость.

## Указатель: миссии B4–B6 (эмоции и сцена), C7–C9 (камера), D10–D11 (сториборд, пропсы), E13–E14 (QC, регрессия) — 2026-10-30

- B4–B6: `auto_act` (эмоция реплики + реакции слушающего в свободные окна),
  8 эмоций, `listen_nod`/`direction`; демо `acting_demo.json` →
  `output/_quality_investigation/blocky_m5/run1/` (420 кадров, dialogue_qc ok).
- C7–C8: `blocky_camera.py` (`plan_shot_list`, `_clamp_moves`,
  `validate_shot_list`), `auto_camera`, `move` в шаблоне; демо
  `camera_demo.json` → `output/_quality_investigation/blocky_m6/run1/`
  (10 планов, camera_qc ok). C9 — частично (ритм из речи).
- D10: `blocky_story.py` — текст → сториборд через Ollama (`--model`,
  `num_predict` ≥ 1200) с детерминированным fallback; 10 тестов.
- D11: `blocky_props.py` — привязка пропса к кисти, гравитация, пол, отскок;
  демо `props_demo.json` → `output/_quality_investigation/blocky_m7/run5/` —
  **QC READY** (зазор силуэтов 0.0836 м, шаг привязки 0.186 м/кадр, min z
  0.110, синхрон 0.000, SRT 2/2). run1–run4 сохранены.
- E13: `blocky_qc.py` + CLI `blocky-qc` — READY/PARTIAL/FAILED, таймкоды;
  14 тестов; реальный гейт пропускает только READY.
- E14: `blocky_regress.py` + CLI `blocky-regress` — эталон
  `scenes/blocky/regression.json` (10 сцен), PASS; 8 тестов.
- LLM-путь D12: `qwen3:4b` возвращает пустой ответ с `format=json`;
  рабочий — `qwen2.5-coder:7b` + `story_num_predict=3000` (прогон run2 в
  рендере).

## Указатель: blocky-мультфильм по референсу «Медовый переполох» (2026-10-30)

Первый фильм по разбору референса SlamaShort: 28 с, 8 планов, синхронизация
пропсов с действиями (`sync` в JSON — дверь/пчела/зонт открываются в момент
взаимодействия), пресет `umbrella`, тест независимости актёров. Видео:
`output/blocky_next_film/final_audio.mp4`; сцена `scenes/blocky/honey_trouble.json`;
отчёт `output/blocky_next_film/REPORT.md`.

## Указатель: блочные мультфильмы из JSON-сцен (2026-10-29)

Данные-сцены вместо кода: `python -m shortsfactory blocky-film --scene scenes/blocky/xxx.json`.
Формат, пресеты окружений/пропсов, валидация: [`scenes/blocky/README.md`](../scenes/blocky/README.md),
модуль `generative/blocky_scene.py`. Готовые фильмы: «Потерянный алмаз»
(`output/blocky_lost_diamond/final_audio.mp4`), «Коварная дверь»
(`output/blocky_tricky_door/final_audio.mp4`); первый прототип —
`output/blocky_first_film/` (регрессионный пример `first_film_scene()`).

## Указатель: первый Minecraft-style мультфильм (2026-10-29)

Прототип «Необычный куб» (Стив и Алекс, блочный стиль, 10 с, 3 плана камеры,
SFX): видео `output/blocky_first_film/final_audio.mp4`, отчёт
`output/blocky_first_film/REPORT.md`. Код: `animation_pipeline/blocky_hero.py`
(blocky-стиль поверх skinned-движка, опциональные `props`/`camera_cuts` в шаблоне).
265 тестов зелёные, skinned-режим не изменён.

## Указатель: независимая валидация M17–M22 и roadmap M23+ (2026-10-29)

Валидация миссий M17–M22 (вердикт: приняты; аудио 9.347 с — ожидаемо; QC-гейты —
неприменимые 2D-проверки): [INDEPENDENT_VALIDATION_M17_M22_2026-10-29.md](INDEPENDENT_VALIDATION_M17_M22_2026-10-29.md).
План развития: [MASTER_ROADMAP_M23_PLUS.md](MASTER_ROADMAP_M23_PLUS.md); следующая миссия — M23 (audio-first тайминги).

## Указатель: аудит анимации (2026-10-28)

Полный аудит анимационной подсистемы и вердикт (миссии 11–16, сравнение ригов,
план M17–M22): [ANIMATION_AUDIT_2026-10-28.md](ANIMATION_AUDIT_2026-10-28.md).
Ключевой вывод: развивать Mission 16 (`skinned_hero`) как слой персонажей.

## Выполнено

- Каркас, конфигурация, БД, состояния и возобновление.
- Все модули статического конвейера и CLI.
- Два реальных E2E-проекта: «Почему у Сатурна есть кольца» и «Древний Рим»
  (`20261023-065517-drevniy rim`), RU/EN, QC READY, полное декодирование.
- `remake --latest --pace fast` проверен: сценарии, медиа, визуалы и озвучка
  переиспользуются, видео и отчёты обновляются, QC проходит повторно.

### Animation Engine v1 (13 с)

- Пакет `src/shortsfactory/animation/`: сцена, риг, действия, голос, рендерер,
  движок, отчёты. 2D cut-out, независимые части тела, моргание, липсинк.

### Animation Engine v2 (17 с)

- Схема сцены `version: 2`: `attach` / `detach` / `transfer`, зависимости
  действий `id` + `after` + `gap`. Привязка предмета к точкам рига, библиотека
  поз, выражения лица, JSON-внешности, компилятор сториборда.
- Пилот `output/_animation_engine_v2/pilot.mp4`.

### Animation Engine v3 (19 с)

- `art.py` + `svg.py` + `animation/art/parts/*.svg`: части тела и лица вынесены
  в декларативные SVG-ассеты; внешности `characters/*.json` содержат `graphics`
  (размеры, якоря, слои, выражения, ракурсы, outfit-оверрайды, лимиты рига).
- `environments.py` + `animation/environments/{room,street}.json`: три слоя
  `back`/`mid`/`front` с параллаксом.
- Пилот `animation/storyboards/pilot_room_v3.json` — 19.0 с, 570 кадров,
  5.68 МиБ, SHA-256 `86924E38…8EE070`. Полный `pytest -q`: 104 passed.

### Animation Engine v4 (20 с) — текущий пилот

- Сториборд `animation/storyboards/pilot_room_v4.json` — «Светящийся шарик».
  5 RU-реплик, 26 битов, 2 привязки (`attach`), 2 передачи (`transfer`),
  1 отпускание (`detach`).
- Сюжет: Milo находит на столе светящийся орб, показывает Nina; передаёт его ей;
  орб выскальзывает и медленно уплывает по сцене; Milo бежит за ним, ловит;
  несёт обратно и отдаёт Nina; оба радуются.
- Реплики (старт, длительность): 2.90 «Смотри, что я нашёл! Оно светится!»
  (2.53 с), 5.20 «Ух ты! Дай посмотреть!» (1.66 с), 8.20 «Ой! Оно выскользнуло!»
  (1.49 с), 13.70 «Ага! Поймал!» (1.07 с), 16.60 «Теперь держу крепко!»
  (1.32 с). Всего речи 8.069 с.
- Пилот: `output/_animation_engine_v4/pilot.mp4` — 600 кадров, 20.00 с,
  1080×1920, 30 fps, H.264 (High, yuv420p) + AAC, 2 923 743 байта (2.79 МиБ),
  SHA-256 `F79A566CCA4304D1C1F1A0D50BEC0B1D76829C2D931809BACDD5AAB733479A17`,
  сгенерирован 2026-10-26T15:45:05+03:00.
- Контактный лист `output/_animation_engine_v4/contact_sheet.jpg` (297 286 байт,
  заголовок «ANIMATION ENGINE v4»). Рядом: `report.json`, `report.md`,
  `scene.compiled.json`, `storyboard.source.json`, `pilot_scene.json`,
  `characters.json`, `audio/`, `frames/`.

#### Что изменено в v4

| Область | Файл | Изменение |
| --- | --- | --- |
| Кадрирование | `animation/renderer.py` | `_crop_box`: `MARGIN` 0.11 → 0.04 и запрет выхода кропа за стадию (раньше при `zoom ≈ 1.02` слева появлялась чёрная полоса шириной до 28 px стадии) |
| Кадрирование (QC) | `animation/engine.py` | `verify_framing` переписан: проверяется каждый кадр, а не 9 моментов; запас 48 px для кистей и 24 px для головы и стоп |
| Позы (QC) | `animation/engine.py` | новый `verify_pose_quality`: ловит перекрещенные через корпус руки; ходьба и бег пропускаются |
| Привязка | `pilot_room_v4.json` | момент передачи перенесён в минимум расстояния кистей (`transfer_at` 0.25 и 0.31); скачок при передаче 54.24 px вместо 530–697 px |
| Траектория предмета | `pilot_room_v4.json` | `velocity` отпускания `[-0.058, 0.008]` вместо `[-0.28, 0.10]`: орб остаётся в кадре и приходит в руку без телепорта |
| Блокинг | `pilot_room_v4.json` | Milo `x 0.16 / scale 0.85`, Nina `x 0.72 / scale 0.84` — фигуры больше не накладываются; ширина поз `surprise`/`joy` снижена до `intensity 0.6` |
| Поза | `animation/motion.py` | восстановлен сломанный блок `POSES["look"]`; `reach_forward.forearm_r` −0.15 → −0.28 |
| Цвета | `animation/renderer.py` | четыре hex-цвета получили `#` |
| Метки версии | `cli.py`, `engine.py`, `renderer.py` | консоль, докстринги, заголовок контактного листа и раздел отчёта — v4 |
| Тесты | `tests/test_animation.py` | +4 регрессионных теста v4 (пересечение отрезков, выход кисти за кадр, чистота сториборда, чистота поз, плавность передач) |

#### Доказательства QC (report.json)

- Кадрирование: 1200 сэмплов, 0 нарушений (`framing.ok = true`).
- Позы: 1200 сэмплов, 0 подозрительных пересечений.
- Привязка: `verified = true`; скачок при передаче 54.24 px, при отпускании
  6.31 px (пороги 160 / 40); все удержания `follows_hand`, разброс 0.0.
- Липсинк: 5 реплик, максимум раскрытия рта 0.88 / 0.93 / 0.81 / 0.89 / 0.52,
  после реплики рот закрыт.
- Движение без камеры: разница кадров 8.452 / 10.296 / 7.928 / 4.427 (порог
  1.0); Milo прошёл 65.5 px, кисти 205–323 px, стопы 253–298 px; орб 394.17 px.
  Камера статична, поэтому `camera_on_diff` (7.149) — только справка.
- Детерминизм: 3 из 3 повторов идентичны. Полное декодирование: OK.

#### Визуальная проверка

Просмотрены кадры 0.5, 3.5, 6.4, 8.1, 9.6, 11.2, 11.8, 15.2, 18.2 с
(`workspace/frames_v4/`): фигуры разведены и не перекрываются; орб лежит на
столе, обе передачи читаются (мяч ровно в точке встречи ладоней), после
отпускания орб остаётся в кадре и подходит к руке Milo, в 11.8 с оказывается
в кисти, финал с поднятыми руками не обрезан.

#### Тесты

```text
.\.venv\Scripts\python.exe -m pytest -q   → 109 passed (exit code 0)
tests/test_animation.py                   → 66 passed
```

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m shortsfactory doctor
.\.venv\Scripts\python.exe -m shortsfactory status
.\.venv\Scripts\python.exe -m shortsfactory animate --storyboard animation/storyboards/pilot_room_v4.json --out output/_animation_engine_v4
.\.venv\Scripts\python.exe -m shortsfactory animate --scene output/_animation_engine_v4/scene.compiled.json
ffprobe -v error -show_streams -show_format output\_animation_engine_v4\pilot.mp4
ffmpeg -v error -i output\_animation_engine_v4\pilot.mp4 -f null -
Get-FileHash output\_animation_engine_v4\pilot.mp4 -Algorithm SHA256
```

Ожидаемый SHA-256: `F79A566CCA4304D1C1F1A0D50BEC0B1D76829C2D931809BACDD5AAB733479A17`.

## Известные проблемы

1. qwen2.5:7b нестабилен: возможны повторные попытки RU/EN и fallback.
   Редакторская проверка сценария обязательна.
2. В финальном RU-сценарии «Древнего Рима» есть «внутренние борцы»; в EN —
   историческая неточность про республику и империю в 509 г. до н. э.
3. Визуалы статических проектов: повторяющиеся карты и неточные ассоциации;
   `duplicate_frames=0` не ловит смысловые дубли.
4. Субтитры статических проектов короткие (25/19 cue); разрыв `н. э.` исправлен.
5. EN-источники в метаданных перечислены с русскими названиями.
6. `models/piper/en_US-ryan-high.onnx` — CC BY-NC-SA, не используется.
7. Старые проекты `20261022-105832-drevnem rime dlitelnost 40` и
   `20261022-215755-marianskoy vpadine` — `PARTIAL`; v6 —
   `INTERRUPTED`. Это исторические прогоны, не приёмка.
8. Анимация v4 — производственный прототип: персонажи остаются векторными
   фронтальными манекенами, профиля и трёх четвертей нет. Кисти — круги,
   пальцев и двуручного захвата нет.
9. Камера в пилоте v4 статична (zoom 1.02). Динамическая камера технически
   работает, но при широких позах у краёв кадра выводит кисти за границу,
   поэтому в приёмке не используется. Нарезка на планы (`camera_shots`) пока
   всегда `wide`.
10. QC не проверяет: наложение силуэтов персонажей друг на друга, обрезку
    плеча и предплечья (только точки кистей, головы и стоп), скачок при
    `attach`. Эти дефекты в v4 найдены визуально и устранены вручную.
11. Привязка кинематическая: отпущенный предмет движется линейно
    (`velocity`), без гравитации, пола и отскока. Двуручного захвата нет.
12. Липсинк амплитудный (RMS), без фонем.
13. Сториборд — типизированный JSON, пишется вручную; генерации из текста нет.
    `create --visual-mode animated` требует готовый authored-JSON и создаёт
    только RU-версию. Standalone `animate` субтитры (SRT) не пишет — SRT
    появляется только в проекте, созданном через `create --visual-mode animated`.

## Следующий конкретный шаг

1. Посмотреть `output/_animation_engine_v4/pilot.mp4` и `contact_sheet.jpg`,
   подтвердить или отклонить направление v4.
2. Если направление принято — вертикальный срез **text → storyboard**:
   генератор, который из короткого текста/сценария собирает валидный
   `pilot_room_v4`-подобный JSON (биты, тайминги, камера, привязки) и прогоняет
   его через `animate` без ручного authored-файла. Критерий: один и тот же
   сценарий воспроизводим, все QC-гейты зелёные.
3. Добавить в QC проверку наложения силуэтов персонажей и скачка при `attach`,
   чтобы визуальные дефекты ловились автоматически.
4. Затем: двуручный захват, пальцы, гравитация/пол для отпущенных предметов,
   очередь реплик с паузами, EN-адаптация animated-проекта.
5. Диагностические файлы `workspace/probe_v4.py`, `probe_ball.py`,
   `probe_hand.py`, `tune_v4.py`, `cam_scan.py`, `scan_fit*.py` — черновики
   замеров, можно удалять. Остальные `workspace/_probe*`, `_e2e*.log`,
   `_anim*` не удалять до подтверждения.
6. Поддерживать `RESULTS.md`, `STATUS.md`, `ROADMAP.md` и этот файл в
   актуальном состоянии.

## Где смотреть состояние

```powershell
.\run.ps1 status
.\run.ps1 inspect --latest
.\run.ps1 report --latest
```

SQLite: `database/shortsfactory.db`; журнал: `logs/shortsfactory.log`.

## F1 — указатель (2026-10-30)
- Реализация: `src/shortsfactory/generative/blocky_continuity.py`; QC-гейты в `blocky_scene.render` и `blocky_production.produce_film`; фикс `cli._run_blocky_create` (--storyboard).
- Тесты: `tests/test_f1_integration.py` (15). Полный набор затронутых — 83 passed.
- Фильм/отчёт: `output/_f1_acceptance/run_new/film.mp4`, `film_report.json`; QC: `blocky-qc --film --report output/_f1_acceptance/run_new/film_report.json`.
- См. «## 19. F1» в MASTER_ENGINE_ARCHITECTURE.md и «## I. F1» в BLOCKY_FILM_AUDIT.md.
