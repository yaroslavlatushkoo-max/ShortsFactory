@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
    echo ShortsFactory Pro
    echo Опишите задачу обычным текстом или оставьте пустым для справки.
    set /p "REQUEST=Задача: "
    if not defined REQUEST (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" --help
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" ask "%REQUEST%"
    )
    echo.
    pause
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
