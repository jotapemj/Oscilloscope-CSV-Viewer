"""Punto de entrada único del arnés.

    python tests/run.py          todas
    python tests/run.py csv      solo las que casen (id del test, sin mayúsculas)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# Backend sin ventana antes de que nadie importe matplotlib
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, ROOT)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def main(argv):
    # Consola Windows en cp1252 rompe acentos y µ
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    pattern = argv[1].lower() if len(argv) > 1 else ""
    loaded = unittest.TestLoader().discover(TESTS, pattern="test_*.py",
                                            top_level_dir=ROOT)
    tests = [t for t in _flatten(loaded) if pattern in t.id().lower()]
    if not tests:
        print(f"No tests match '{pattern}'.")
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
