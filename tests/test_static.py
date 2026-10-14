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


def shadowed_imports(path):
    """Funciones que asignan como local un nombre importado a nivel de
    módulo y además lo leen: Python lo trata como local en TODA la función
    y la lectura del módulo da UnboundLocalError (p.ej. theme = {...})."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    imported = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                imported.add((a.asname or a.name).split(".")[0])
    found = []
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    for fn in ast.walk(tree):
        if not isinstance(fn, scopes):
            continue
        stores, loads, declared = set(), set(), set()
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            c = stack.pop()
            if isinstance(c, scopes + (ast.ClassDef,)):
                continue                  # scope anidado: aparte
            if isinstance(c, (ast.Global, ast.Nonlocal)):
                declared.update(c.names)
            elif isinstance(c, ast.Name):
                (loads if isinstance(c.ctx, ast.Load) else stores).add(c.id)
            elif isinstance(c, ast.arg):
                stores.add(c.arg)
            stack.extend(ast.iter_child_nodes(c))
        for name in (stores & loads & imported) - declared:
            found.append((getattr(fn, "name", "<lambda>"), fn.lineno, name))
    return sorted(found)


class UndefinedNames(unittest.TestCase):

    def test_sources(self):
        """main.py y ocv/**: ningún nombre sin definir."""
        files = [os.path.join(ROOT, "main.py")]
        files += glob.glob(os.path.join(ROOT, "ocv", "**", "*.py"),
                           recursive=True)
        for path in files:
            with self.subTest(file=os.path.relpath(path, ROOT)):
                self.assertEqual(undefined_names(path), [])

    def test_no_shadowed_imports(self):
        """Ningún módulo importado se reutiliza como variable local."""
        files = [os.path.join(ROOT, "main.py")]
        files += glob.glob(os.path.join(ROOT, "ocv", "**", "*.py"),
                           recursive=True)
        for path in files:
            with self.subTest(file=os.path.relpath(path, ROOT)):
                self.assertEqual(shadowed_imports(path), [])


if __name__ == "__main__":
    unittest.main()
