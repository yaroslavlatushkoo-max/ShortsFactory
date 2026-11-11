# -*- mode: python ; coding: utf-8 -*-
# Сборка ShortsFactoryDesktop: onedir + данные config/fonts + иконка.
# Запуск: .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ShortsFactoryDesktop.spec
from pathlib import Path

ROOT = Path(SPECPATH)
datas = [
    (str(ROOT / 'config' / 'config.toml'), 'config'),
    (str(ROOT / 'config' / 'profiles.toml'), 'config'),
    (str(ROOT / 'assets' / 'icon.ico'), 'assets'),
]
for ttf in sorted((ROOT / 'assets' / 'fonts').glob('*.ttf')):
    datas.append((str(ttf), 'assets/fonts'))

a = Analysis(
    ['desktop_app.py'],
    pathex=[str(ROOT / 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=['gui_worker'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ShortsFactoryDesktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,
    icon=str(ROOT / 'assets' / 'icon.ico'),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ShortsFactoryDesktop',
)
