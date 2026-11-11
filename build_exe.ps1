# Сборка десктоп-приложения ShortsFactory Pro в exe.
# Использование: powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = "python" }

Push-Location $root
try {
    # 1. pyinstaller в venv (dev-зависимость [desktop])
    & $venvPython -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[build] installing pyinstaller into venv..."
        & $venvPython -m pip install pyinstaller | ForEach-Object { Write-Host "  $_" }
    }

    # 2. иконка (детерминированная, без внешних ассетов)
    & $venvPython make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "make_icon.py failed" }

    # 3. сборка onedir
    & $venvPython -m PyInstaller --noconfirm --clean ShortsFactoryDesktop.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $exe = Join-Path $root "dist\ShortsFactoryDesktop\ShortsFactoryDesktop.exe"
    if (-not (Test-Path $exe)) { throw "exe not found: $exe" }
    $sizeMB = [math]::Round((Get-ChildItem (Join-Path $root "dist\ShortsFactoryDesktop") -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host "[build] OK: $exe ($sizeMB MB)"
} finally {
    Pop-Location
}
