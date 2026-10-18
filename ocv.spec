# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: build with `python build.py` (or `pyinstaller ocv.spec`).
# --onedir: carpeta con el .exe + dependencias (arranque rápido y menos
# falsos positivos de antivirus que --onefile). Sin UPX por lo mismo.
import os

ROOT = SPECPATH
NAME = "Oscilloscope CSV Viewer"
ICON = os.path.join(ROOT, "assets", "icon.ico")
SPLASH = os.path.join(ROOT, "assets", "splash.png")

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    # Icono en runtime (ventanas secundarias, _resource_path)
    datas=[(ICON, "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Backends/GUI que la app no usa (el hook de matplotlib ya mete TkAgg)
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "IPython",
              "jupyter", "notebook", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    SPLASH,
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    minify_script=True,
    always_on_top=True,
)
exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    splash.binaries,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=NAME,
)
