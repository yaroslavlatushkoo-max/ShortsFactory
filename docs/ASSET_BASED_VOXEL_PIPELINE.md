# Asset-Based Voxel Cartoon Pipeline — архитектура и запуск

Дата: 2026-10-27. Миссия 8: переход от «сборки случайной модели на каждом
запуске» к **качественному производству** на подготовленных, повторно
используемых ассетах и клипах, с Blender как 3D-движком и ShortsFactory как
автоматизатором.

## Решение по формату ассетов

Курсированный **собственный генерируемый формат** (Blender/`bpy`), а не
glTF/GLB/FBX:
- на машине нет Blockbench (проверено: не установлен), внешние экспортёры не
  проверены; скачивать непроверенные архивы и ставить тяжелые тулчейны не
  требовалось;
- импорт внешних форматов требует данных/лицензий, которых нет;
- **архитектура оставляет слот для будущего импорта**: ассеты описываются в
  каталоге (`catalog.json`) с полями `source`, `format`, `rig`, `status`;
  замена генератора на импортёр (например, glTF в будущем) не меняет
  остальной пайплайн.

Это зафиксировано в `docs/` и в метаданных каталога.

## Каталог ассетов (`assets/voxel/`)

```
assets/voxel/
├── catalog.json                 # каталог: персонажи/среды/пропсы/клипы/планы
└── animations/                  # метаданные клипов (idle, walk, turn, stop,
    ├── idle.json                #   bend, reach, pickup, hold, react, look_at)
    ├── walk.json
    └── ...
```

Каталог (`schema: voxel-asset-catalog-1.0`) содержит секции:
- `characters` — `hero_v1` (исходник, риг, палитра, статус);
- `environments` — `forest_blocky`;
- `props` — `cube`, `glow_cube`, `platform` (взаимодействие `hand_r`);
- `clips` — клипы с путём к файлу метаданных;
- `shots` — планы камеры (`wide/medium/closeup/three_quarter/follow/reaction`)
  с позицией/фокусом/смещением.

Каждый клип (`*.json`) описывает: `rig`, `transition {from,to}`, диапазон
длительности, допустимые параметры, статус. Это **проверяемые** ассеты, а не
случайные формулы времени рендера.

## Слой оркестрации

`src/shortsfactory/generative/voxel_engine/asset_pipeline.py`:
- `AssetCatalog.load()` — загрузка каталога; `validate()`/`validate_refs()` —
  проверка существования файлов и ссылок;
- `build_asset_plan(duration)` — детерминированный план эпизода, ссылающийся
  на id ассетов (заполняет `AnimationPlan.production`);
- `validate_asset_plan()` — базовая валидация плана + проверка ссылок на
  ассеты + проверка, что каждое действие — подготовленный клип;
- `render_asset()` — рендер через Voxel-движок с планами камеры из каталога.

`AnimationPlan` расширен полем `production` (необязательным, обратно
совместимо): `{character, environment, props, clips, shots}`.

## Выбор ассетов (первый персонаж)
`hero_v1` — оригинальный кубический герой: пропорциональная голова, корпус,
руки/ноги, читаемое лицо (глаза/брови/рот), риг на высоте бедра (стопы на
земле). Палитра согласована. Пригоден для повторного использования в серии.

## Анимационные клипы
Подготовлены и проверены по каталогу: `idle, walk, turn, stop, bend, reach,
pickup, hold, react, look_at`. Клипы описывают начальное/конечное состояние,
допустимые параметры длительности и совместимость с ригом `humanoid_v1`.
Переходы: idle → look_at → turn → walk → stop → bend → pickup → react → hold.

## Взаимодействие с предметом (pickup)
Последовательность: герой смотрит (`look_at`) → подходит (`walk`) → останавливается
(`stop`) → наклоняется (`bend`) → протягивает руку/берёт (`pickup`) → предмет
перемещается к точке захвата и **привязывается к кости руки** (событие `attach`,
`hand_r`) на отметке 6.9 с → поднимается/рассматривает (`react`) → финальная
поза (`hold`). Предмет — реальный объект сцены, остаётся в руке при движении.

## Система камеры
Библиотека проверенных планов в каталоге (`shots`). Для эталона — основной
`three_quarter` (0–80% времени) и финальный `closeup` (80–100%). Вертикальный
9:16 сенсор, `Track To` на грудь героя, безопасное кадрирование. Превью
ключевых кадров формируется до финального рендера.

## Интеграция в ShortsFactory
- `--engine asset` добавлен в `ENGINE_CHOICES` и в `engine_dispatcher` (через
  `render_asset`). Старые движки (`procedural/blender/voxel/auto`) не тронуты.
- `free-create --engine asset` доступен (выбор появляется автоматически).
- LLM формирует **структурированный план** (id ассетов), а не произвольный
  Blender Python-код. Неизвестные клипы/ассеты отклоняются валидацией.

## Запуск демо
```powershell
cd D:\ShortsFactory
$env:PYTHONPATH='src'
# превью 540×960
python -c "from pathlib import Path;from shortsfactory.config import load_config;from shortsfactory.generative.voxel_engine.asset_pipeline import build_asset_plan;from shortsfactory.generative.engine_dispatcher import render_plan;print(render_plan(build_asset_plan(duration=9,width=540,height=960),load_config(Path('.')),Path(r'output\_asset_based_voxel_v1\render_preview'),engine='asset').status)"
# финальный 1080×1920
python -c "from pathlib import Path;from shortsfactory.config import load_config;from shortsfactory.generative.voxel_engine.asset_pipeline import build_asset_plan;from shortsfactory.generative.engine_dispatcher import render_plan;print(render_plan(build_asset_plan(duration=9,width=1080,height=1920),load_config(Path('.')),Path(r'output\_asset_based_voxel_v1\render_final'),engine='asset').status)"
```

## Артефакты (`output/_asset_based_voxel_v1/`)
- `render_final/final.mp4` — итоговый ролик (1080×1920@30, H.264);
- `render_final/contact_sheet_final.jpg` — контактный лист;
- `render_final/render_voxel_asset_cube_01.py` — исходник Blender-сцены;
- `render_final_meta.json`, `animation_plan.json` — метаданные/план;
- `render_preview/` — превью 540×960.
