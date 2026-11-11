# Блочные сцены — формат описания (blocky-film)

Мультфильм в блочной стилистике описывается **JSON-файлом** и рендерится одной
командой — без изменения Python-кода:

```powershell
cd D:\ShortsFactory
.venv\Scripts\python.exe -m shortsfactory blocky-film --scene scenes\blocky\my_scene.json --output output\my_film
```

Примеры: [`lost_diamond.json`](lost_diamond.json), [`tricky_door.json`](tricky_door.json).
Валидация строгая: неизвестный персонаж/действие/SFX, действие за границей сцены,
склейки камеры не по возрастанию, ссылка на несуществующий пропс — понятная ошибка
с именем поля, рендер не запускается.

## Схема сцены

```jsonc
{
  "title": "Название",            // используется в имени сценария Blender и slug папки
  "duration": 10.0,               // секунды
  "fps": 30, "width": 1080, "height": 1920, "samples": 32,
  "environment": "forest",        // пресет: forest | meadow | cave — или явный словарь
                                  // {sky_rgb, sky_strength, ground_rgb, sun_energy}
  "actors": [
    {
      "character": "steve",       // steve | alex | milo | nina
      "origin": [-0.35, 0.9, 0.0],
      "actions": [ {"action": "walk", "start": 0.3, "duration": 2.0,
                    "params": {"prop": "Diamond", "angle": 3.14,
                               "emotion": "surprised", "emotion_intensity": 1.0}} ],
      "hops": [ {"start": 5.5, "duration": 0.45, "height": 0.17, "back": -0.26} ]
    }
  ],
  "props": [ /* пресеты или явные боксы, см. ниже */ ],
  "camera_cuts": [
    {"frame": 1,   "center": [0.0, -0.5, 0.95], "height": 3.4, "lens": 42.0, "fit": 0.85},
    {"frame": 111, "center": [-0.66, -2.15, 0.8], "height": 1.35, "lens": 50.0, "yaw": 0.5}
  ],
  "sfx_events": [[5.6, "sparkle"], [6.5, "boing"], [7.1, "pop"]]
}
```

### Действия (`action`)

Все глаголы скин-рига: `idle, walk, run, crouch, reach, pickup, interact, use,
react, surprise, joy, celebrate, gesture, wave, talk, turn, turn_in_place, stop`
и эмоции `neutral, happy, sad, angry, confused, worried, surprised`
(полный список — `PLAN_ACTION_STATES` в `skinned_hero.py`; неизвестный глагол →
ошибка валидации). Пропуски между действиями заполняются idle автоматически.

- `params.angle` — угол `turn_in_place` (радианы; π — разворот на 180°).
- `params.prop` — имя пропса, к которому относится действие (проверяется).
- `params.emotion` / `emotion_intensity` — эмоция поверх любого состояния.

### Прыжки (`hops`)

Комичный подскок: всё тело по параболе, `back` — сдвиг по мировой оси Y
(отрицательный — к камере). Персонаж остаётся на новой позиции.

### Пропсы

Явный бокс: `{"name", "center":[x,y,z], "size":[sx,sy,sz], "rgb":[r,g,b],
"roughness", "emissive", "emission_rgb", "emission_track": [[кадр, сила]],
"pos_track": [[кадр,[x,y,z]]], "rot_track": [[кадр,[rx,ry,rz]]],
"origin_offset": [x,y,z]}` (сдвиг меша относительно оси — шарнир для `rot_track`).
Свет: `{"type": "light", "name", "center", "rgb", "energy_track": [[кадр, W]]}`.

Пресеты (развёртываются в боксы): `tree {x,y}`, `rock {x,y}`, `grass {x,y}`,
`stump {x,y,height}`, `glowing_cube {x,y,z,size,flash_frame,emission_track…}`,
`door {x,y,open_frame,angle,end_frame}` (шарнир слева, петля-rotation),
`spring_box {x,y,pos_track,rgb}`.

### Камера

Склейки — жёсткие монтажные переходы: с кадра `frame` до следующей склейки камера
неподвижна. `height` — видимая высота мира (персонаж ~1.85 м); в вертикальном
кадре 9:16 ограничивающим обычно является **горизонтальный** охват (~0.56×height).
`yaw` (радианы) — наклонный план 3/4: камера обходит центр по орбите.
Первая склейка обязана быть на кадре 1.

### Звук

`sparkle`, `boing`, `pop` — процедурный синтез (numpy), лицензионно чистый.
Дорожка длится ровно `duration` — звук и видео всегда совпадают.

## Результат рендера

`<output>/final_audio.mp4` (H.264 1080×1920@30 + AAC), `contact_sheet.jpg`,
`render/<сценарий>_diagnostics.json` (pose_error, дрейф стоп, пустые кадры,
тайминги), `scene_report.json`. Кадры рендера — в `<output>/render/frames/`.
