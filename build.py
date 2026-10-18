"""Build the Windows .exe and its release .zip.

    python build.py            build dist/<name>/ and dist/OCV-vX.Y.Z-win64.zip
    python build.py --smoke    also launch the built .exe with a sample CSV
                               and fail if it crashes or does not close

Requires the dev dependencies: pip install -r requirements-dev.txt
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "Oscilloscope CSV Viewer"
DIST = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST, NAME)
EXE = os.path.join(APP_DIR, NAME + ".exe")


def version():
    """Lee __version__ sin importar el paquete (evita cargar Tk)."""
    ns = {}
    with open(os.path.join(ROOT, "ocv", "__init__.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    return ns["__version__"]


def build():
    # Limpieza completa: un build viejo no debe colarse en el zip
    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    "--clean", os.path.join(ROOT, "ocv.spec")],
                   cwd=ROOT, check=True)
    if not os.path.isfile(EXE):
        sys.exit(f"build failed: {EXE} not found")


def package():
    """dist/OCV-vX.Y.Z-win64.zip con la carpeta de la app dentro."""
    base = os.path.join(DIST, f"OCV-v{version()}-win64")
    path = shutil.make_archive(base, "zip", root_dir=DIST, base_dir=NAME)
    print(f"package: {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
    return path


def smoke(timeout=90):
    """Arranca el .exe con config temporal y un CSV sintético; la app
    se cierra sola (OCV_SMOKE_EXIT) y su código de salida dice si fue bien."""
    with tempfile.TemporaryDirectory() as tmp:
        csv = os.path.join(tmp, "smoke.csv")
        t = np.arange(2000) / 1e6
        with open(csv, "w", encoding="utf-8") as fh:
            fh.write("Time,CH1\n")
            for ti, v in zip(t, np.sin(2 * np.pi * 5e3 * t)):
                fh.write(f"{ti:.9e},{v:.6e}\n")
        env = dict(os.environ, OCV_CONFIG_DIR=tmp, OCV_SMOKE_EXIT="8000",
                   OCV_SMOKE_CSV=csv)
        try:
            code = subprocess.run([EXE], env=env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            sys.exit(f"smoke: .exe did not close within {timeout} s")
    if code != 0:
        sys.exit(f"smoke: .exe exited with code {code}")
    print("smoke: OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--smoke", action="store_true",
                    help="launch the built .exe and check it runs")
    args = ap.parse_args()
    build()
    if args.smoke:
        smoke()
    package()


if __name__ == "__main__":
    main()
