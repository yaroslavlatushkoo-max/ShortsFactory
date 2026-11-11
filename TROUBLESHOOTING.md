# Troubleshooting

## `shortsfactory` не найден

Запускайте через `.\run.ps1` или `.\.venv\Scripts\python.exe -m shortsfactory`.
Проверьте, что `.venv` существует:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

## FFmpeg не найден

`doctor` покажет ошибку. Установите FFmpeg и добавьте `bin` в `PATH`, либо задайте:

```powershell
$env:SHORTSFACTORY_FFMPEG = "C:\path\to\ffmpeg.exe"
$env:SHORTSFACTORY_FFPROBE = "C:\path\to\ffprobe.exe"
```

## FFmpeg слишком старый

FFmpeg >= 4.4 обязателен: конвейер использует `adelay=all=1`,
`amix=normalize` и `alimiter=level`. На старых версиях (например 4.2) рендер
падает с ошибкой `Option 'all' not found` или тихо выдаёт «пустой» микс.

Проверьте версию:

```powershell
ffmpeg -version
```

Если она меньше 4.4 — обновите FFmpeg (рекомендуется 6+): https://ffmpeg.org/download.html

## Ollama недоступен или сценарий слишком короткий

Проверьте:

```powershell
ollama list
ollama serve
```

Модель по умолчанию — `qwen2.5:7b`. Если она недоступна, сценарий будет собран
экстрактивным резервным способом; это допустимо, но качество ниже. В отчёте
будет предупреждение `extractive-fallback`.

## Русский текст в консоли выглядит как `????`

Это кодировка консоли, а не файлов. Используйте `run.ps1` (он включает UTF-8) или:

```powershell
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

## Openverse предупреждает о недоступности

Основные источники (Wikipedia, NASA, Commons) продолжают работать. Система
использует карточки Pillow, если изображение не найдено. Это не блокирует выпуск.

## Задание прервалось

```powershell
.\run.ps1 status
.\run.ps1 resume
```

Или конкретное:

```powershell
.\run.ps1 retry --latest --from VOICE
```

Уже завершённые этапы и их файлы сохраняются.

## QC `PARTIAL`

Откройте `output/<project>/reports/quality_<lang>.md`. Обязательные ошибки
перечислены в таблице. Исправьте причину и повторите:

```powershell
.\run.ps1 retry --latest --from QUALITY_CHECK
```

Если проблема в монтаже/озвучке — начните с `EDITING` или `VOICE`.

## Субтитры выглядят слишком дробно

Это стилистическое ограничение текущего разбиения. Проверьте `subtitles/*.srt`;
при необходимости уменьшите длину фраз в сценарии или настройте `_chunk_sentence`
в `subtitles.py` и пересоберите с `--from EDITING`.

## Некоммерческая модель Piper

`en_US-ryan-high.onnx` имеет лицензию CC BY-NC-SA и **не используется**. Не
меняйте `voice_en` на эту модель для монетизируемых видео. Рабочая модель —
`en_US-libritts_r-medium.onnx` (CC BY 4.0).

## Нужно поменять стиль/темп/голос

```powershell
.\run.ps1 remake --latest --pace fast --style cinematic
.\run.ps1 remake --latest --voice-ru ru_RU-dmitri-medium.onnx
```

При изменении длительности старые сценарии архивируются в
`workspace/<project>/archive/scripts/` и генерируются заново.

## Где смотреть, что именно проверялось

- `output/<project>/reports/report.md` — сводка, источники, медиа, лицензии.
- `output/<project>/reports/quality_<lang>.md` — все проверки.
- `output/<project>/reports/report.json` — машиночитаемые данные.
- `logs/shortsfactory.log` и `workspace/<project>/logs/pipeline.log`.
