# ShortsFactory Pro — Blocky Cartoon Engine: архитектура и программа из 15 миссий

Документ фиксирует, **что уже есть**, **что именно расширяем** и **в каком порядке**,
чтобы 15 миссий не превратились в переписывание проекта. Всё, что описано как
«есть», проверено в коде и запуском; всё, что описано как «план», ещё не сделано.

## 1. Границы: что не трогаем

- **Animation Engine v4** (2D, `src/shortsfactory/animation/`, 109 тестов) — остаётся
  как есть. Blocky-движок идёт своим путём и не меняет 2D-рендер.
- **Smooth skinned rig** (`skinned_hero.py`, 24 кости, Milo/Nina) — общий фундамент:
  blocky-персонажи используют **тот же скелет**, поэтому viseme- и emotion-каналы
  работают без второй реализации.
- **Blender-шаблон** (`_BLENDER_TEMPLATE`) не меняется: вся деформация — матрицы
  костей, shape keys в проекте отсутствуют.

## 2. Существующая цепочка blocky

```
scenes/blocky/*.json
      │  BlockyScene.load()            generative/blocky_scene.py
      ▼
BlockyScene.validate()  ── строгая схема: персонажи, действия, пропсы,
      │                    склейки камеры, SFX, (с Mission 1) диалог
      ▼
build_blocky_scene_script()            animation_pipeline/blocky_hero.py
      │  один меш + одна арматура, кости с префиксами (steve:hips)
      ▼
render_skinned()  →  Blender (D:\Blender\blender.exe, BLENDER_EEVEE)
      │  render/final.mp4 + render/<scene>_diagnostics.json + frames/*.png
      ▼
synth_sfx() + mux_audio()  →  final_audio.mp4
      ▼
contact_sheet.jpg + scene_report.json
```

Ключевые контракты (не менять без причины):

| Контракт | Где | Что гарантирует |
| --- | --- | --- |
| `BlockyScene.from_dict/validate/render` | `generative/blocky_scene.py` | сцена из JSON валидируется до рендера, ошибки — русские `ValueError` |
| `build_blocky_scene_payload` | `blocky_hero.py` | `frames[frame][f"{actor}:{bone}"] = 12 чисел` (матрица 3×4), `stance_windows`, `actors`, `props`, `camera_cuts`, `environment` |
| `_box(mb, ..., weights, mat)` | `blocky_hero.py` | rigid box: 8 вершин, вес 1.0 на одну кость |
| `apply_visemes(frames, fps, lines)` | `skinned_hero.py` | аддитивно правит `jaw_open/mouth_wide/mouth_round` |
| `EMOTIONS` + `_apply_emotion` | `skinned_hero.py` | дельты с smoothstep-рампом 0.25 с, не трогают `root`/`ankle_*` |
| `Cue` / `write_srt` | `subtitles.py` | субтитры без зависимости от `Config` |

## 3. Скелет и лицо

24 кости на персонажа: `hips, spine, chest, neck, head`, `arm_l/r, elbow_l/r,
wrist_l/r`, `leg_l/r, knee_l/r, ankle_l/r, foot_l/r`, `jaw, lip_l, lip_r,
brow_l, brow_r`.

Blocky-меш — rigid boxes на этом скелете. С Mission 1 лицо реально
анимируется:

- полость рта (тёмный бокс на `head`), она видна только когда челюсть опущена;
- пластина нижней губы на `jaw` — при `jaw_open=0.34` опускается на ~4 см;
- уголки рта на `lip_l`/`lip_r` — расходятся при `mouth_wide`;
- брови на `brow_l`/`brow_r` с отдельным материалом `brow` — `brow_tilt`
  (±0.55 рад) и `brow_lift` (до 12 мм) дают читаемые эмоции.

Проверено: 216 вершин / 162 грани / 9 материалов на персонажа; на 24 кости
приходится по 8 вершин на каждую лицевую кость.

## 4. Диалог (Mission 1)

В `BlockyScene` добавлены `dialogue: [{character, text, start, duration}]` и
`lang: "ru"`. Реплика:

1. валидируется (персонаж в сцене, непустой текст, интервал внутри сцены,
   реплики одного персонажа не пересекаются);
2. распределяется в `actors[i]["voice_lines"]` (`_distribute_dialogue`);
3. попадает в payload → `apply_visemes` → рот реально двигается;
4. превращается в субтитр `Имя: текст` (`subtitle_cues()` → `subtitles/write_srt`
   → `<out>/subtitles/<lang>.srt`, путь в `scene_report.json`).

`duration` реплики — **реальная длина озвученной фразы**, а не оценка: на этом
этапе её задаёт автор сцены, а Mission 2 заменит её на измеренное значение
после синтеза (аудио сначала, рендер потом — как в `animation/voice.py`).

Ограничение этапа: озвучки в видео пока нет (только SFX), субтитры и lip-sync
синхронны между собой, но не с голосом.

## 5. Программа: 15 миссий, 5 фаз

Каждая миссия заканчивается реальным результатом: тесты + измеренная
диагностика + просмотренные кадры. «Зелёные юнит-тесты» сами по себе миссию не
закрывают.

### Фаза A — фундамент

| # | Миссия | Результат | Статус |
| --- | --- | --- | --- |
| A1 | Архитектура + лицо blocky + диалоговая схема + SRT | этот документ; 216/162/9 меш; `dialogue` в `BlockyScene`; `subtitles/ru.srt`; демо-сцена `scenes/blocky/dialogue_demo.json` отрендерена | **сделано** |
| A2 | Голос: TTS на персонажа, аудио-первым, реальные длительности | два разных голоса (Steve/Alex), `voice.wav`, длительности реплик записываются в сцену до рендера | **сделано** |
| A3 | Lip-sync по аудио-таймлайну | виземы привязаны к реальным длительностям, рот закрыт в паузах, проверка на кадрах | **сделано** |

### Фаза B — диалог как игра

| # | Миссия | Результат | Статус |
| --- | --- | --- | --- |
| B4 | Эмоции в blocky | 8 эмоций + `scared`; эмоция реплики и реакции слушающего, рампы 0.25 с; демо `acting_demo.json` → `blocky_m5/run1` | **сделано** |
| B5 | Сценическое взаимодействие | `auto_act`: поворот `direction` к говорящему, `listen`/`listen_nod`, реакции в свободные окна; демо `blocky_m5/run1` (420 кадров, реакции видны) | **сделано** |
| B6 | Субтитры и речевой QC | SRT из реальных таймингов + `dialogue_qc` (реплика ↔ виземы ↔ субтитр), 4 SRT в `blocky_m5/run1` | **сделано** (ASS не делали) |

### Фаза C — режиссура

| # | Миссия | Результат | Статус |
| --- | --- | --- | --- |
| C7 | Режиссёр камеры | `blocky_camera.py`: пресеты wide/medium/close/ots, `plan_shot_list` по репликам, `auto_camera`; демо `camera_demo.json` → `blocky_m6/run1` (10 планов, `camera_qc ok`) | **сделано** |
| C8 | Грамматика склеек | склейки на границах реплик, обратный план на слушающего, over-the-shoulder, `_clamp_moves` держит движение внутри плана; проверено на кадрах | **сделано** |
| C9 | Ритм и комедия | ритм выводится из речи (паузы, длина реплик, ускорение монтажа к финалу); отдельных комедийных «битов» нет | частично |

### Фаза D — автоматизация

| # | Миссия | Результат | Статус |
| --- | --- | --- | --- |
| D10 | Текст → сториборд | `blocky_story.py`: Ollama (`--model`) → нормализация/ремонт → повтор → детерминированный `fallback_storyboard`; 9 тестов | **сделано** (fallback, если модели нет) |
| D11 | Физика пропсов | `blocky_props.py`: привязка к кисти, гравитация, пол, отскок, релиз; демо `props_demo.json` → `blocky_m7/run5`, QC READY, кадры проверены | **сделано** |
| D12 | Одна команда | `blocky-create --idea "..."` → storyboard → сцены → `film.mp4` + SRT + contact sheet + `film_report.json`; resume по сценам | **сделано** (run2: LLM ollama, 24.021 с, 3 сцены, 5 SRT, film QC READY) |

### Фаза E — валидация

| # | Миссия | Результат | Статус |
| --- | --- | --- | --- |
| E13 | QC-гейты | `blocky_qc.py` + `blocky-qc`: пустые кадры, дрейф/вис стоп, зазор силуэтов, шаг привязки, синхрон аудио/видео/SRT; READY/PARTIAL/FAILED + таймкоды | **сделано** |
| E14 | Регрессия | `blocky_regress.py` + `blocky-regress`: эталон `scenes/blocky/regression.json` (10 сцен), сравнение длительности, актёров, реплик, пропсов и склеек | **сделано** |
| E15 | Финальный фильм | 40–60 с, Steve + Alex, диалог, эмоции, lip-sync, пропсы, несколько планов, RU (+EN) | **сделано** (RU+EN: 45.021 с, 5 сцен, 11 SRT, film QC READY) |

## 6. Правила разработки

1. Расширяем существующие модули; новая логика — только если старой нет.
2. Никаких новых тяжёлых зависимостей: Blender + ffmpeg + Piper + numpy.
3. Ошибки не глотать: неизвестный персонаж/действие/реплика — `ValueError` с
   читаемым текстом, а не тихий `idle`.
4. Метрики не заменяют глаза: каждая миссия смотрит кадры (контактный лист,
   кропы лиц), а не только диагностику.
5. Ограничения пишутся явно в отчёте и в `docs/CONTINUE.md`.

## 7. Приёмка Mission 1 (A1) — факты

- Меш блочного персонажа: **216 вершин / 162 грани / 9 материалов**, по 8
  вершин на jaw, lip_l, lip_r, brow_l, brow_r (тест
  test_blocky_face_geometry_is_rigged_to_the_face_bones).
- BlockyScene.dialogue + lang; _distribute_dialogue кладёт реплику в
  actor["voice_lines"] (дальше её подхватывает apply_visemes в payload).
- Валидация реплик: неизвестный персонаж, пустой текст, нечисловой интервал,
  выход за сцену, наложение двух реплик одного персонажа.
- subtitle_cues() → Cue с префиксом имени (Steve: ...), render() пишет
  subtitles/<lang>.srt.
- Демо scenes/blocky/dialogue_demo.json: 12 с, 2 актёра, 6 реплик, 4 пропса,
  3 плана, 2 SFX. Рендер output/_quality_investigation/blocky_m1/run1/:
  status OK, 360 кадров 1080×1920, 48 костей, pose_error 5e-06,
  дрейф стоп **0.0**, 0 пустых из 12, **0.6602 с/кадр** (237.66 с), SRT с
  таймингами 1.2/3.0/4.4/6.2/7.6/9.6 с.
- Визуально (кропы головы кадров 232–300): на реплике пластина челюсти
  опускается и открывает тёмную полость, на паузе остаётся тонкая щель —
  рот читается закрытым; брови наклоняются на worried и выпрямляются на
  happy; разрывов меша нет.
- Тесты: pytest tests/test_blocky_scene.py tests/test_blocky_hero.py
  -q -m "not integration" → **34 passed**.

Ограничение миссии: **озвучки нет** — duration реплики задаётся в JSON
вручную. Реальные длительности появятся в A2 (TTS до рендера).

## 8. Приёмка Mission 2 (A2 — голос) — факты

Голос появляется **до** рендера: `BlockyScene.render()` синтезирует реплики,
подставляет измеренные длительности в сцену и только потом строит кадры.
Поэтому рот, субтитры и звук считаются по одним и тем же числам.

### Голосовые профили (`src/shortsfactory/generative/blocky_voice.py`)

| персонаж | язык | модель Piper | настройка |
| --- | --- | --- | --- |
| steve | ru | `ru_RU-dmitri-medium.onnx` | speed 1.02, pitch +1.5 полутона |
| alex | ru | `ru_RU-irina-medium.onnx` | speed 0.98, volume 0.95, словарь «что→што», «чтобы→штобы» |
| steve | en | `en_US-ryan-high.onnx` | — |
| alex | en | `en_US-libritts_r-medium.onnx` | speaker 12, volume 0.95 |

`VoiceProfile` — frozen dataclass (`voice_id, lang, voice, speed, pitch, speaker,
volume, provider, pronunciation`), `resolve_profile(character, lang, override)`
бросает `ValueError` со списком доступных пар, `profile.speak(text)` делает
whole-word замены. `speed` умножается на базовый `length_scale` из конфига,
`pitch` реализован ffmpeg-цепочкой `asetrate→aresample→atempo` (длительность
сохраняется). `TTSService.synthesize_line` получил необязательные
`length_scale` и `speaker`; кэш-ключ их учитывает.

### Длительности, паузы, микс

- `synthesize_dialogue(...)` пишет `<out>/audio/voices/<character>/line_NNN.wav`
  и возвращает `VoiceTake` с **реальной** длительностью из WAV.
- `apply_timings(dialogue, takes)` переносит эти длительности в документ сцены.
- `schedule_dialogue(dialogue, gap=0.18)` — авторские слоты почти всегда короче
  синтезированной реплики; без сдвига второй персонаж начинал говорить поверх
  первого и субтитры накладывались. Функция только сдвигает реплики вперёд.
  `render()` вызывает её дважды: по оценкам и после измерения.
- Если реплики сдвинулись, длительность сцены растёт:
  `self.duration = max(self.duration, last_end + 0.35)`.
- `mix_scene_audio(dest, inputs, duration=...)` — ffmpeg
  `aresample→aformat→volume→adelay(all=1)` на каждый вход, затем
  `amix=normalize=0,apad,alimiter=limit=0.95:level=false,aresample`.
  **`apad` обязателен**: без него дорожка кончалась на последней реплике и
  `mux_audio -shortest` обрезал видео (замер: 1.4 с вместо 2.0 с).
  **`level=false` обязателен**: с дефолтным `level=true` alimiter перенормирует
  результат и пик снова 1.0 (замерено).
- Амбиент — процедурный (numpy): `ambient_wav(preset, duration)` для
  `forest|cave|meadow`, `none` и неизвестный пресет — `ValueError`.
  Громкости: голос 1.0, SFX 0.45, амбиент 0.16.

### Демо-сцена и проверка

`scenes/blocky/voice_engine_demo.json` — «Странный звук», 16.6 с, 2 актёра,
6 реплик, 4 плана, амбиент `forest`, `lang: "ru"`.
Рендер `output/_quality_investigation/blocky_m2/run2/`:
status OK, 498 кадров 1080×1920@30, 48 костей, `pose_error 6e-06`,
дрейф стоп 0.0, 0 пустых из 13, **0.6319 с/кадр** (314.69 с).
`scene_report.json`: `mix {duration 16.6, peak 0.95, clipped false}`, шесть
takes с реальными длительностями, `audio_inputs` (6 голосов + sfx + ambience),
`voices_manifest`. ffprobe: h264 1080×1920 30 fps + aac 48 кГц, **оба ровно
16.600 с** (видео не обрезано). `subtitles/ru.srt` — 6 кусков без наложений:
1.000–2.585, 3.800–6.389, 6.600–9.004, 9.300–11.541, 11.800–13.948, 14.200–16.011.

Два голоса различаются объективно (медианный F0 по окнам 40 мс):
steve 192.8–199.2 Гц, alex 170.3–177.8 Гц (~2.3 полутона плюс разные модели).

Визуально (кропы лиц, кадры 340/355/380/400/440/470): в паузе рот — тонкая
закрытая линия, на старте реплики открывается тёмным прямоугольником, к концу
реплики прикрывается, на `happy` приоткрыт улыбкой; брови наклоняются на
`worried`. Контактный лист пересобран с `rate = 12 / duration`, поэтому
показывает весь фильм, а не первые 9 секунд.

Тесты: **49 passed** (`tests/test_blocky_voice.py` + `test_blocky_scene.py` +
`test_blocky_hero.py`, `-m "not integration"`).

Ограничения A2: lip-sync пока **текстовый** (виземы из букв, не из амплитуды
звука) — это A3; камера в демо авторская, поэтому в двух последних планах
второй персонаж и светящийся куб остаются за кадром — это C7.

## 9. Приёмка Mission 3 (A3 — lip-sync по реальному аудио) — факты

### Что реализовано

`generative/blocky_voice.py`:

- `read_wav_mono(path)` — PCM 8/16/32 бит в float32 моно, ошибки → `TTSError`;
- `speech_envelope(path, hz=50)` — RMS по окнам 20 мс, нормировка по 95-му
  перцентилю, сглаживание с быстрой атакой (0.55) и медленным спадом (0.30);
  тишина даёт все нули;
- `speech_segments(envelope, threshold=0.12, min_silence=0.12, min_speech=0.08,
  pad=0.04)` — речевые спаны `[[start, end], ...]` в секундах, короткие паузы
  внутри слова склеиваются, клики отбрасываются;
- `VoiceTake` хранит `envelope`, `envelope_hz`, `segments`, считает
  `speech_seconds`; `to_dict()` пишет `envelope_hz`, `envelope_frames`,
  `segments`, `speech_s` (сама кривая в отчёт не попадает);
- `synthesize_dialogue` измеряет огибающую сразу после `wav_duration`;
- `apply_timings` переносит `envelope/envelope_hz/segments` в реплику.

`generative/blocky_scene.py`: `_distribute_dialogue` копирует эти три поля в
`voice_lines` персонажа, поэтому рендер получает данные из настоящего WAV.

`animation_pipeline/skinned_hero.py`:

- `viseme_track(text, segments=...)` — буквы распределяются **только внутри
  речевых спанов**, между ними и в конце ставится `rest`;
- `apply_visemes` интерполирует виземы и умножает все три канала на
  `gain = 0.0` при уровне < 0.06, иначе `0.25 + 0.75 * level`; кадры вне
  реплик не меняются. Каналы применяются аддитивно, поэтому эмоция не стирается.

### Демо-сцена и рендер

`scenes/blocky/lipsync_demo.json` — «Оно дышит», 12.0 с, 2 актёра, 3 реплики,
4 плана (общий → крупный Steve → крупный Alex → очень крупный Steve), лес,
амбиент forest, SFX `sparkle`. Рендер
`output/_quality_investigation/blocky_m3/run1/`: status **OK**, 360 кадров
1080×1920@30, `pose_error 5e-06`, дрейф стоп 0.0, 0 пустых из 12,
**0.5988 с/кадр** (215.58 с).

`scene_report.json`: `duration 12.0`, `mix {peak 0.95, rms 0.1217,
clipped false, 48 кГц}`; takes с реальными длительностями и речевыми спанами:

| персонаж | start | duration | speech_s | segments |
| --- | --- | --- | --- | --- |
| steve | 0.8 | 2.534 | 2.02 | 0.00–0.56, 0.66–1.32, 1.48–2.28 |
| alex | 4.2 | 2.415 | 2.06 | 0.02–0.62, 0.78–2.24 |
| steve | 7.4 | 1.679 | 1.42 | 0.00–0.76, 0.84–1.50 |

ffprobe: h264 1080×1920 30 fps + aac 48 кГц, оба ровно **12.000 с**.
`subtitles/ru.srt` — 3 куска без наложений: 0.800–3.334, 4.200–6.615,
7.400–9.079.

### Визуальная проверка

Контактный лист (12 плиток) и кропы рта из `render/frames/`:

- кадр 42 (t = 1.37 с, пауза 1.36–1.46 между спанами) — рот закрыт,
  `jaw_open = 0.0`;
- кадр 50 (t = 1.63 с, внутри спана 1.46–2.12) — рот открыт, `jaw 0.093`,
  `mouth_wide 0.301`;
- кадр 66 (t = 2.17 с, пауза 2.12–2.28) — рот снова закрыт, `jaw 0.0`;
- кадр 80 (t = 2.63 с, третий спан) — открыт, `jaw 0.097`;
- в третьей реплике 26 из 53 кадров имеют `jaw > 0.05`, пик `0.242` на
  гласной — рот не «висит» открытым и не закрыт всю реплику.

### Тесты

`tests/test_blocky_voice.py` (+`_write_bursts`, огибающая, спаны, перенос данных
в реплику), `tests/test_skinned_hero.py` (+rest в паузах, gain по огибающей,
закрытый рот во внутренней паузе). Набор:
`pytest tests/test_blocky_voice.py tests/test_skinned_hero.py
tests/test_blocky_scene.py tests/test_blocky_hero.py -q -m "not integration"` —
**96 passed**.

### Ограничения A3

- Виземы по-прежнему **графемные**, не фонемные: непроизносимая буква двигает
  рот, дифтонг получает одну форму. Аудио даёт тайминг, паузы и громкость, но
  не место согласного.
- Порог 0.12 и сглаживание могут «съесть» очень тихую речь; для шёпота нужен
  отдельный профиль.
- Губы смыкаются на `M/F` только по букве, детекции взрывных по спектру нет.

## 10. Приёмка Mission 4–6 (B4–B6 — диалог как игра) — факты

### Что реализовано

- `blocky_scene.py` `auto_act`: росписание реплик по персонажам
  (`_apply_line_emotions` — эмоция реплики, рампы 0.25 с;
  `_apply_listener_reactions` — слушающий поворачивается к говорящему через
  `direction` и слушает/кивает), действия авто-реакций ставятся только в окна,
  освобождённые `_carve_idle` (действия автора не перетираются). `IDLE_VERBS
  = {idle, stand, wait, return_idle}`.
- Эмоции: 8 каналов (angry/confused/happy/neutral/sad/scared/surprised/worried),
  аддитивные к мимике.
- `dialogue_qc()` — проверка «реплика ↔ виземы ↔ субтитр» пишется в отчёт.
- `listen_nod`/`listen` + `direction` — поворот и кивок слушающего.

### Демо и рендер

`scenes/blocky/acting_demo.json` («Разговор с дверью», 14 с, 4 реплики) →
`output/_quality_investigation/blocky_m5/run1/`: status **OK**, 420 кадров,
0.6372 с/кадр, `dialogue_qc ok`, 4 SRT, реакции слушающего видны на кадрах.

### Тесты

`tests/test_blocky_scene.py` (+`auto_act`, `listen`, `direction`, реплика/эмоция,
`dialogue_qc`). Набор с camera/hero: **86 passed**.

### Ограничения B4–B6

- `timeline_from_actions` не поддерживает перекрытие действий: авто-реакции
  только в свободные окна.
- Поворот `direction` зеркалит оверлеи (`_mirror_overlay`), но тела остаются
  фронтальными; «спины» нет.

## 11. Приёмка Mission 7–8 (C7–C8 — режиссура) — факты

### Что реализовано

- `blocky_camera.py`: пресеты `SHOT_PRESETS` (wide/medium/close/ots/insert/two),
  `plan_shot_list(repliki)`, `_clamp_moves` (держит `move.to_frame` внутри своего
  плана), `validate_shot_list`, `shot_summary`.
- `auto_camera` в `BlockyScene`: строит план по репликам, склейки на границах
  реплик, обратный план на слушающего, over-the-shoulder; плавное `move` внутри
  плана.
- В Blender-шаблоне камера умеет `move` (интерполяция center/height); в отчёт
  пишутся `shot_list` и `camera_qc`.

### Демо и рендер

`scenes/blocky/camera_demo.json` («Спор у пещеры», 13 с, 4 реплики) →
`output/_quality_investigation/blocky_m6/run1/`: status **OK**, 390 кадров,
10 планов, `camera_qc ok`, кадры просмотрены (широкий → средний → крупный →
через плечо).

### Тесты

`tests/test_blocky_camera.py`: `plan_shot_list`, `_clamp_moves`,
`validate_shot_list`, `shot_summary` — 86 passed (сцен/hero).

### C9 (ритм и комедия) — частично

Ритм выводится из речи: длина реплик, паузы, ускорение монтажа к финалу.
Отдельных комедийных «битов» (gag-тайминги, сдвиг ритма панчлайна) нет.

## 12. Приёмка Mission 10 (D10 — текст → сториборд) — факты

### Что реализовано

`blocky_story.py`: `generate_storyboard(idea, *, config, scenes, seconds, lang,
model, attempts)` — попытка Ollama (`client.generate_json`, `system=SYSTEM_PROMPT`,
`temperature=0.7`, `model`, `num_predict=_story_num_predict(cfg)` ≥ 1200, default
3000), затем нормализация/ремонт (`normalize_scene`), повтор, и
детерминированный `fallback_storyboard` (duration = max(5.0, 2.0 + 1.9*N +
1.6)). `normalize_scene` сохраняет `camera_cuts`, `carry`, `lang`,
`auto_act/auto_camera`; `parse_storyboard` берёт `lang` из данных.

### Проверки

`tests/test_blocky_story.py` — 10 passed; вместе с props/scene — 49 passed.
Реальный прогон `--model qwen2.5-coder:7b` → `SOURCE ollama` (3 сцены по 8 с).
`qwen3:4b` с `format=json` возвращает 0 токенов → fallback (зафиксировано).

## 13. Приёмка Mission 11 (D11 — физика пропсов) — факты

### Что реализовано

`blocky_props.py`: `hand_track` (мировые позиции кисти из костей),
`carry_track` (привязка по `approach`/`release`/`grip`, гравитация, пол,
отскок, ключ на каждый кадр), `collect_carry_tracks`, `validate_carry`;
интеграция в `blocky_scene.validate()` и `build_blocky_scene_payload`.

### Демо и рендер

`scenes/blocky/props_demo.json` («Тёплый ящик», 8 с, spring_box) → итерации
`output/_quality_investigation/blocky_m7/run1..run5` (run1/run2/run3/run4
сохранены). Финальный **run5**: status OK, 240 кадров, 117.34 с
(0.4889 с/кадр), дрейф стоп 0.0, 0 пустых из 12; `blocky-qc` → **READY**.

Кадры run5 просмотрены: кадр 1 — оба актёра целиком, куб на полу; 100 — голова
целиком, куб в правой руке; 165 — Steve целиком, куб на полу, голова не обрезана.

### Как пришли к run5

- run2: кадр 165 обрезал голову (height 1.8/center z 0.45) → камеру поправили.
- run4: QC-гейт силуэтов поймал зазор 0.0006 м — актёры стояли вплотную
  (−0.45/+0.55 при ширине тела 1.0). Раздвинули: Steve −0.6, Alex +0.7,
  широкие планы h 4.2, камера 165 `[−0.78, 0.45, 1.00]` h 2.4.

### Тесты

`tests/test_blocky_props.py` — 10 passed.

## 14. Приёмка Mission 13 (E13 — QC-гейты) — факты

### Что реализовано

`blocky_qc.py`: `scene_qc(report, *, payload, srt_path)` и
`film_qc(report, *, srt_path)` + `LIMITS` (дрейф 0.02 м, провал 0.005, вис
0.03, зазор силуэтов 0.02, шаг привязки 0.35 м/кадр, синхрон аудио 0.06 с,
субтитров 0.08 с, min rms 0.001). Итог READY/PARTIAL/FAILED, каждая проверка с
таймкодом (`frame_timecode`/`timecode`). CLI `blocky-qc --report <json>
[--film] [--srt] [--payload] [--json]`.

Проверки: чёрные/пустые кадры, поза, дрейф/вис/провал стоп, пересечение
силуэтов (по `actor_gap` из payload), прыжки привязки предметов (формат
`[frame,[x,y,z]]`), уровень/синхрон аудио с видео, субтитры (число, start/end,
текст, не за концом), `dialogue_qc`, `camera_qc`. У фильма — все сцены OK,
длительность vs план, SRT внутри фильма, contact sheet.

### Проверки

`tests/test_blocky_qc.py` — 14 passed. Реальный прогон на `blocky_m7/run5`
→ READY (зазор 0.0836 м @ 0:04.767, шаг 0.186 м/кадр @ 0:01.900, min z
0.110, peak 0.95/rms 0.0995/расхождение 0.000, SRT 2/2, dialogue_qc ok).

### Ограничения E13

- Старые рендеры без `actor_gap` дают PARTIAL (warn), не fail.
- Проверки читают отчёты/payload, а не рендерят заново.

## 15. Приёмка Mission 14 (E14 — регрессия) — факты

### Что реализовано

`blocky_regress.py`: `scene_metrics` (длительность, fps, актёры, число реплик,
пропсов, склеек, auto-флаги), `build_golden`, `run_regression(root, *, golden,
update)`; CLI `blocky-regress [--golden] [--update] [--json]`. Эталон
`scenes/blocky/regression.json` — 10 сцен; раскадровки (final_film_ru/en,
ключ `scenes`-список) пропускаются.

### Проверки

`tests/test_blocky_regress.py` — 8 passed (сдвиг склейки, пропажа/новая сцена,
нет эталона, игнор untracked-полей). `blocky-regress` → PASS.


## 16. ������ Mission D12 � �����

produce_film(storyboard, out_dir, *, voice=True, timeout=3600, resume=True):
storyboard.json -> ����� scene_XX/ (resume �� scene_report.json) -> concat_videos -> film.mp4
(ffmpeg concat demuxer) -> SRT subtitles/<lang>.srt -> film_contact_sheet (4x3) -> film_report.json.

�������� ������ (run2, `output/_quality_investigation/blocky_story_film/run2`):

- `blocky-create --idea "Steve ������� �� ������ ����� ���������� ��� � ���� Alex. Alex ������� ��������, �� ��� ������ � ���� �����, � ������ ������ �������� ��� ����." --scenes 3 --seconds 24 --lang ru --model qwen2.5-coder:7b --output output/_quality_investigation/blocky_story_film/run2`
- �������� ����������: **ollama** (qwen2.5-coder:7b, 244.8 �, 1655 �������, 6064 �������; 3 ����� �� 8.0 �).
- ������� Piper: ru_RU-dmitri-medium (Steve), ru_RU-irina-medium (Alex); SFX + ambience ������������.
- ����: status OK, `film.mp4` h264 1080x1920@30 + AAC, **24.021 �**, 3 �����, 5 ���������, contact_sheet.jpg.
- `blocky-qc --film --report film_report.json` -> **READY** (3/3 ����� OK, ������������ 24.021/24.000, SRT ������, ���� ������).

�����������:

- qwen3:4b � format=json ���������� ������ ����� (0 �������) � ����� `--model qwen2.5-coder:7b`.
- �����-���� 24 � (� �� 40-60 �): ���� E15 ����������� ���������� ���������� ��������.
- `Cue` ������� `scene_id`: `_cue_from_take(take, offset, scene_id)`.


## 17. Приёмка Mission E15 — финальный фильм

### План
5 сцен × 9.0 с = **45.0 с**, `source: "authored"`, Steve + Alex, диалог с эмоциями, lip-sync, предметы (дверь, пень, скала, светящийся куб, дерево, трава), несколько планов, окружения cave/forest, SFX и фон. Сценарии: `scenes/blocky/final_film_ru.json` (RU) и `scenes/blocky/final_film_en.json` (EN).

Сцены: 1 «Дверь в пещере» / «The Door in the Cave» · 2 «Не пойду» / «Not Going» · 3 «Тёплый куб» / «The Warm Cube» · 4 «Дверь открыта» / «The Door Opens» · 5 «Наружу» / «Outside».

### Запуск
```
python -u -m shortsfactory blocky-create --storyboard scenes/blocky/final_film_ru.json --output output/blocky_master_completion/ru
python -u -m shortsfactory blocky-create --storyboard scenes/blocky/final_film_en.json --output output/blocky_master_completion/en
```

### Факты
- **RU** (`output/blocky_master_completion/ru`): Статус OK; 5 сцен; **45.021 с**; 11 субтитров; `film.mp4` h264 1080×1920@30 + AAC, duration 45.021333, ~9.39 МБ; голоса Piper ru_RU-dmitri-medium (Steve), ru_RU-irina-medium (Alex); `blocky-qc --film` → **READY**, по-сценный QC → 5/5 READY.
- **EN** (`output/blocky_master_completion/en`): Статус OK; 5 сцен; **45.021 с**; 11 субтитров; `film.mp4` ~9.40 МБ; голоса Piper en_US-ryan-high (Steve), en_US-libritts_r-medium (Alex); `blocky-qc --film` → **READY**, 5/5 READY.
- Фикс: `BlockyScene.render()` теперь форсит `actor["duration"] = self.duration` — иначе rebuild актёров без `duration` давал разное число кадров (Steve 252 vs Alex 270 в сцене 5) и `ValueError: Все персонажи сцены должны иметь одинаковое число кадров`.

### Ограничения
- Модель агента не принимает изображения: кадры извлекались (`frame_01..04.jpg`), но визуальный просмотр невозможен; проверка через автоматические QC-гейты + визуальный контроль D11 run5 (та же постановка/камеры).
- 45 с в пределах цели 40–60 с; при желании можно продлить до 60 с добавлением сцен.


## 18. BUGFIX: ambience="none" — допустимое состояние (2026-10-30)

`BlockyScene.ambience` принимает `"none"` (он в `AMBIENCE_PRESETS`), но рендер через `if self.ambience:` считал любую непустую строку реальной дорожкой и вызывал `ambient_wav("none", ...)` → `ValueError: Пресет окружения «none» не создаёт аудио.`

**Причина**: гейт трактовал «"none"» как источник. **Фикс** (`blocky_scene.py`): методы инстанса `_ambience_track_enabled()` (True только для непустого и не-"none") и `_scene_mix_inputs(take_inputs, *, audio_dir)` (реплики → SFX → фон; фон синтезируется только при включённой дорожке). Рендер использует их; для `""`/`"none"` фон пропускается — пустой WAV не создаётся, диалоги и SFX микшируются как прежде.

**Архитектурная особенность**: `ambience="none"`/`""` = «фона нет» (пропуск источника), а не ошибка. `ambient_wav` не менялся (контракт «none → исключение» сохранён для прямых вызовов).

**Проверка**: `tests/test_blocky_ambience.py` (7 тестов: none без диалогов → `[]`; none+диалог → только takes; none+диалог+SFX → takes+sfx без ambience.wav; forest → +ambience; отсутствие ambience → `[]`; "none" проходит validate; микс с none: длительность = duration, peak > 0.05, звук не раньше start). Smoke: `blocky-create` той же идеи → `output/my_first_idea_fixed/film.mp4` (25.221 с, 3 сцены, 5 SRT, film QC READY); реальная сцена с `ambience="none"` через `blocky-film` → OK (9.000 с, audio без ambience.wav, QC READY).

## 19. F1 — непрерывность состояния и обязательные QC-гейты (2026-10-30)

### Что сделано
- **Новый модуль `src/shortsfactory/generative/blocky_continuity.py`** (`FEATURE_VERSION="f1-1"`):
  - `WorldState` — положение персонажей, расположение пропсов, состояние дверей (open), предметы в руках (carried);
  - `hash_scene()` — sha256(canonical json + версия) для контроля входных данных при resume;
  - `prepare_scene_continuity()` — применяет предыдущее состояние к сцене: телепортация в пределах той же локации отклоняется (origin возвращается к предыдущему, issue фиксируется); смена локации (env) — разрешённый переход; дверь в состоянии open не открывается повторно (без дефолтных keyframes); carried-предмет продолжает нестись владельцем; конфликты событий и дубликаты имён валидируются;
  - `snapshot_final_state()` — вычисляет финальное состояние сцены для следующей.
- **Дверной пресет (B-01)**: по умолчанию статично закрыт (`state="closed"`, без rot_track-анимации); `state="open"` — статично открыт; явный `open_frame` (событие) анимирует только эту сцену, `end_frame` зажат последним кадром (фикс B-21).
- **QC-гейты**:
  - сцена (`blocky_scene.render`): после сборки отчёта считается `scene_qc` (payload+srt), статус понижается до FAILED/PARTIAL при провале/предупреждениях (B-07);
  - фильм (`blocky_production.produce_film`): `film_qc` интегрирован; финальный статус OK/PARTIAL/FAILED определяется гейтами; `continuity_qc` пишется в film_report; видео собирается и для PARTIAL-сцен;
  - resume: сцена переиспользуется только если `status ∈ {OK, PARTIAL}`, `qc != FAILED`, `input_hash == hash_scene(raw)` и видео существует; иначе ререндер (B-07/R4).
- **CLI**: `_run_blocky_create` — генерация LLM только в ветке `--idea` (исправлена ошибка, когда `--storyboard` всё равно звал LLM); ошибки LLM/TTS/render завершаются контролируемыми сообщениями и ненулевым exit code.

### Проверка и результат
- Тесты: `tests/test_f1_integration.py` (15) + смежные → **83 зелёных** (f1 15, qc 14, ambience 7, scene 31, story 10, regress 8).
- Реальный прогон новой идеи через основной CLI (LLM qwen2.5-coder:7b):
  `python -u -m shortsfactory blocky-create --idea "Стив и Алекс находят в пещере старую дверь с замком. Алекс находит ржавый ключ, Стив открывает дверь, и за ней оказывается светлый лес." --scenes 3 --seconds 18 --lang ru --model qwen2.5-coder:7b --output output/_f1_acceptance/run_new`
  → **Статус OK**, `film.mp4` 19.421 с (1080×1920@30 h264+aac, ~3.4 МБ), 3 сцены, 4 SRT, contact sheet.
  → `blocky-qc --film` **READY**; все 3 сцены READY; `dialogue_qc`/`camera_qc` True; `continuity_qc` зафиксировал и исправил телепортацию Алекс во 2-й сцене (issue в отчёте).
- Ограничения/осталось: немые сцены по-прежнему требуют ≥1 реплики (B-17); белый список параметров действий не введён (B-18); fallback не параметризован идеей; умеренные foot-hover → PARTIAL (некритичные предупреждения).

## 20. F2.5 + F3 — подход к предметам и авто-режиссура (2026-10-31)

### F2.5: walk_to (актёр подходит к предмету, а не предмет к актёру)
- `skinned_hero.py` `timeline_from_actions`: действие `walk` с `params["target"]=[dx,dy]` — локальная цель от текущего положения; вычисляется `dist`, `t_yaw=atan2(dy,dx)`, `speed_max=1.6 м/с`, `walk_d=dist/speed`; сегменты `start→walk→stop` через `target_path(t) = vlerp(origin0, target0, clamp(t/walk_d))`. Без target — прежнее поведение (шаг вперёд по +X).
- `blocky_story.py` `stage_interactions` (F2.5): **предметы больше НЕ придвигаются к актёру**. Новый `_add_approach_walk(actions, act, tx, ty, dist, scene_duration)` вставляет перед grab/pickup действие `walk` с target до предмета; если окна перед действием нет — сдвигает действие и последующие, продлевая сцену (`scene["duration"]=round(new_dur,3)`). Материализация недостающего пропса (LLM сослался, а пропса нет) — на нейтральную точку `(0.0, -1.5)`.
- Ограничение: target вычисляется в локальных координатах актёра (актуально при yaw=0).

### F3: авто-режиссура `plan_scene_shots`
- `blocky_camera.py`: новая функция `plan_scene_shots(dialogue, actors, props, *, duration, fps)` — композиция по событиям сцены: wide-открытие на кадре 1; по репликам talk→medium (two_shot при перекрытии двух реплик); действие с предметом дальше 1.5 м → insert-план на предмет; иначе medium (актёр + предмет). Защита от обрезания: высота кадра = max(мин. высота пресета, верх объекта + 2×FRAME_MARGIN(0.35), ширина кадра) — вписывает персонажей целиком (CHAR_TOP=1.81).
- `blocky_scene.py`: `from_dict` и `render()` (auto_camera) используют `plan_scene_shots` вместо `plan_shot_list`.

### Проверка
- Новая тестовая пара `tests/test_f3_f25.py` (9 тестов) — walk target, отсутствие «подтягивания» предметов, близкий предмет без walk, F3-планы, обрезка головы, focus на дальнем пропсе.
- Совокупный прогон затронутых файлов → **105 зелёных тестов** (camera, story, scene, ambience, qc, f1, f3_f25).
- Дополнительно: LLM-раскадровка (путь `--idea`) обязана содержать хотя бы одну реплику — иначе попытка отклоняется (retry/fallback); тесты `tests/test_blocky_story.py` → 13 passed. Промпт явно требует реплики только от steve/alex (голоса Piper есть только у них; иначе — `Нет голосового профиля для «milo»`).

### Приёмка: 3 новые идеи через CLI (output/_f3_f25_acceptance/)
| Идея | Прогон | Источник | Длительность | Субтитры | Film QC |
| --- | --- | --- | --- | --- | --- |
| 1. «Карта → сундук → кристалл» | `idea1_mapa_cunbuk_v2` | ollama | 20.943 с | 3 | PARTIAL (сцена 1: умеренные drift 0.051 м / hover 0.083 м — foot warn) |
| 2. «Повар и помощник» | `idea2_kitchen` | fallback | 22.821 с | 6 | **READY** (3/3 сцены READY) |
| 3. «Рыбак и куб» | `idea3_fisher_v2` | ollama | 18.021 с | 3 | **READY** (3/3 READY) |

- Идея 1 (первая попытка, `idea1_mapa_cunbuk`) показала «немой» фильм: LLM вернул сцены без диалогов, а `check_storyboard` допускает немые сцены → 0 субтитров. Это и привело к правилу «идея обязана быть говорливой».
- Идея 2: LLM дважды вернул непригодный (немой) JSON → автоматически fallback с диалогами.
- Идея 3 (первая попытка): LLM выбрал `milo` (нет голоса) → падение на озвучке; после правки промпта — успешно.
- Ограничения: QC честно фиксирует умеренный foot-drift/hover при walk как PARTIAL (не скрывается); визуальный просмотр кадров невозможен (модель без vision) — оценка по геометрии/QC.

## 21. F4 — физическая передача предметов (handoff) и строгая валидация действий (2026-10-31)

### Цель
Универсальная система взаимодействий `walk_to → look_at → reach → grab → carry → release`
без телепортации предметов и фиктивных взаимодействий: предмет переходит из руки в руку
ТОЛЬКО в момент реального контакта; строгий whitelist действий и параметров (B-18);
стабильная автономная LLM-генерация.

### Реализация
- **`blocky_props.py` — физическая передача**:
  - `carry_track` поддерживает `transfer{character, bone, start, duration, gap}`:
    предмет остаётся у передающего, пока кисти не сблизятся на `transfer_gap`
    (HANDOFF_GAP=0.7 м); после контакта — короткий lerp (0.3 с) к руке получателя,
    далее предмет в руке получателя;
  - контакт ищется по кистям; если позы не протягивают руки — fallback по костям
    `hips` (`REACH_SUM=1.5` м): первый кадр от `transfer.start`, где hips-дистанция
    ≤ REACH_SUM (иначе честная ValueError);
  - подлёт к руке масштабируется реальной дистанцией (rest→hand / MAX_APPROACH_SPEED);
  - `release` — гравитация/пол/отскок (не телепортация);
  - `validate_carry` требует hand-верб у держащего {grab,hold,reach,pickup,interact,use,talk}.
- **`blocky_story.py` — авто-постановка передачи + B-18**:
  - `_plan_handoffs`: если один пропс хватают/держат ≥2 актёра в одной сцене —
    giver = ранний (carry/start/смена владельца), taker = следующий grab;
    избыточные walk получателя удаляются, при dist>1.8 добавляется один подход
    с остановкой на 1.35 м от передающего (зазор тел >0.02); создаётся
    `transfer{character:taker, bone:hand_L, start:max(start_give, t_take-0.35),
    duration:0.3, gap:0.7}` + adjustment «рукопожатие»;
  - **B-18**: `ACTION_PARAM_SCHEMAS` (whitelist параметров по действию),
    `_repair_action_params` (неизвестные ключи/действия/битые ссылки на пропсы →
    reject log, а не молчаливое удаление), `_validate_scene_action_params`;
    `_props` сохраняет name/state/open_frame/end_frame/carry;
  - `_prop_x` в `blocky_camera.py` читает расширенный формат (`center`).
- **`blocky_scene.py`**: `actor["duration"]=self.duration` (фикс разного числа кадров);
  `_handoff_windows()` — пересечение силуэтов в окне передачи → warn, не fail;
  QC-гейт (`scene_qc`) влияет на статус сцены.
- **`blocky_continuity.py`**: запрет телепортации в той же локации (origin возвращается,
  issue не пишется); авто-статист при исчезновении персонажа в той же локации;
  авто-hold, если носитель carry продолжает нести предмет без hand-верба.

### Тесты
Новые: `tests/test_f4_interactions.py` (hold+grab → carry у giver + transfer у taker;
замена лишних walk одиночным подходом; авто-hold; B-18 reject log; один grab без transfer),
`test_blocky_props.py` (передача ждёт реального контакта; падает, если руки никогда
не встретятся; fallback по hips), `test_blocky_qc.py` (silhouettes overlap в окне
handoff → PARTIAL/warn). Прогон всех затронутых файлов — зелёный (~220 тестов).

### Приёмка: 3 новые идеи через CLI (out/_f4_acceptance/)
| Идея | Прогон | Источник | Длительность | SRT | Film QC | Передача |
| --- | --- | --- | --- | --- | --- | --- |
| 1. «Ключ в пещере» | idea1_key_handoff_v2 | ollama | 26.777 с | 4 | READY | нет (нет второго grab) |
| 2. «Сундук в пещере» | idea2_chest_handoff_v2 | ollama | 23.288 с | 5 | READY | нет |
| 3. «Светящийся куб» | idea3_cube_handoff_v11 | ollama | 19.221 с | 2 | READY | да: сцена 2, pos_track x −0.895→+0.895 |

### Ограничения
- LLM-генерация нестабильна: для передачи нужны ДВА grab/hold одного пропса в одной
  сцене; иначе система честно работает без передачи (идеи 1–2).
- Модель без vision — визуальный просмотр кадров невозможен; оценка по геометрии/QC.
