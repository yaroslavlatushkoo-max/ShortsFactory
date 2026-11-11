$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Не найдено виртуальное окружение: $Python" -ForegroundColor Red
    Write-Host "Создайте его: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

Push-Location $Root
try {
    & $Python -m shortsfactory @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
