"""Análisis estático: nombres leídos que no se definen en ningún sitio.

Pilla imports rotos al mover código entre módulos (NameError latente en
ramas que los tests no ejecutan, p.ej. un diálogo de error).
"""
import ast
import builtins
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPLICIT = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def undefined_names(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    bound = set(IMPLICIT)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    return sorted({(n.id, n.lineno) for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id not in bound})


class UndefinedNames(unittest.TestCase):

    def test_sources(self):
        """main.py y ocv/**: ningún nombre sin definir."""
        files = [os.path.join(ROOT, "main.py")]
        files += glob.glob(os.path.join(ROOT, "ocv", "**", "*.py"),
                           recursive=True)
        for path in files:
            with self.subTest(file=os.path.relpath(path, ROOT)):
                self.assertEqual(undefined_names(path), [])


if __name__ == "__main__":
    unittest.main()
