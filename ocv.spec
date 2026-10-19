# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: build with `python build.py` (or `pyinstaller ocv.spec`).
# --onedir: carpeta con el .exe + dependencias (arranque rápido y menos
# falsos positivos de antivirus que --onefile). Sin UPX por lo mismo.
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = SPECPATH
NAME = "Oscilloscope CSV Viewer"
ICON = os.path.join(ROOT, "assets", "icon.ico")
SPLASH_MAX = (760, 480)   # = max_img_size por defecto de Splash


def make_splash():
    """Splash = splash_base.png + 'Version X.Y.Z' leído de __version__.
    Posición y tamaño calibrados con el rótulo hecho a mano (Segoe UI,
    blanco, esquina sup. izq. de la tinta en 711.5, 609.1)."""
    ns = {}
    with open(os.path.join(ROOT, "ocv", "__init__.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                             "Fonts", "segoeui.ttf")
    if not os.path.isfile(font_path):
        raise SystemExit(f"splash: font not found: {font_path}")
    font = ImageFont.truetype(font_path, 41)
    text = f"Version {ns['__version__']}"
    img = Image.open(os.path.join(ROOT, "assets", "splash_base.png"))
    img = img.convert("RGBA")
    x0, y0 = font.getbbox(text)[:2]
    ImageDraw.Draw(img).text((711.5 - x0, 609.1 - y0), text, font=font,
                             fill=(255, 255, 255, 255))
    # PyInstaller reduce a max_img_size (760x480) y hace la transparencia
    # con magenta como color clave: al reducir, y con alfa parcial, sale
    # orla magenta. Reducimos aquí (alfa premultiplicado) y dejamos el
    # alfa binario: o opaco o transparente.
    img.thumbnail(SPLASH_MAX, Image.LANCZOS)
    img.putalpha(img.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
    out = os.path.join(workpath, "splash.png")
    os.makedirs(workpath, exist_ok=True)
    img.save(out)
    return out


SPLASH = make_splash()

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
