"""Parser y evaluador de canales MATH."""
import unittest

import numpy as np

from ocv.core import math_expr as mx


def run(expr, ch=None, m=None, time=None):
    time = np.linspace(0.0, 1.0, 11) if time is None else time
    return mx.math_eval(mx.math_parse(expr), time, ch or {}, m or {})


class Parse(unittest.TestCase):

    def test_precedence(self):
        """* antes que +, paréntesis mandan, menos unario."""
        np.testing.assert_allclose(run("2+3*4"), 14.0)
        np.testing.assert_allclose(run("(2+3)*4"), 20.0)
        np.testing.assert_allclose(run("-2*-3"), 6.0)
        np.testing.assert_allclose(run("10-4-3"), 3.0)

    def test_case_and_spaces(self):
        """Minúsculas y espacios aceptados."""
        a = np.arange(11.0)
        np.testing.assert_allclose(run(" abs( ch1 ) ", ch={0: -a}), a)

    def test_deps(self):
        """Refs 1-based → índices 0-based."""
        ast = mx.math_parse("CH1*CH3 + M2")
        self.assertEqual(mx.math_deps(ast), ({0, 2}, {1}))

    def test_errors(self):
        """Expresiones inválidas lanzan ValueError."""
        for expr in ["", "CH1+", "FOO(CH1)", "CH0", "(CH1", "MAX(CH1)",
                     "CH1 CH2", "CH1 % 2"]:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    run(expr, ch={0: np.zeros(11), 1: np.zeros(11)})


class Eval(unittest.TestCase):

    def test_div_by_zero(self):
        """División por cero → 0, sin warnings."""
        a = np.ones(11)
        b = np.zeros(11)
        np.testing.assert_allclose(run("CH1/CH2", ch={0: a, 1: b}), 0.0)

    def test_power(self):
        """POW = cuadrado, SQRT recorta negativos a 0."""
        a = np.linspace(-2, 2, 11)
        np.testing.assert_allclose(run("POW(CH1)", ch={0: a}), a * a)
        np.testing.assert_allclose(run("SQRT(CH1)", ch={0: a}),
                                   np.sqrt(np.maximum(a, 0)))

    def test_min_max(self):
        a = np.arange(11.0)
        b = 10 - a
        np.testing.assert_allclose(run("MIN(CH1,CH2)", ch={0: a, 1: b}),
                                   np.minimum(a, b))
        np.testing.assert_allclose(run("MAX(CH1,CH2)", ch={0: a, 1: b}),
                                   np.maximum(a, b))

    def test_int_diff(self):
        """INT de constante 2 = rampa 2t; DIFF de 3t = 3."""
        t = np.linspace(0, 1, 101)
        np.testing.assert_allclose(run("INT(CH1)", ch={0: np.full(101, 2.0)},
                                       time=t), 2 * t, atol=1e-12)
        np.testing.assert_allclose(run("DIFF(CH1)", ch={0: 3 * t}, time=t),
                                   3.0, atol=1e-9)

    def test_math_ref(self):
        """M1 se resuelve desde m_arrays."""
        a = np.arange(11.0)
        np.testing.assert_allclose(run("M1*2", m={0: a}), 2 * a)

    def test_missing_ref(self):
        with self.assertRaises(ValueError):
            run("CH5")


class ResolvedRefs(unittest.TestCase):
    """Camino de la app: refs ya resueltas a ('ref_real', idx)."""

    @staticmethod
    def resolve(n, mapping):
        # Imita OscilloscopeApp._resolve_refs
        if n[0] == "ref":
            return ("ref_real", mapping[(n[1], n[2])])
        if n[0] == "num":
            return n
        if n[0] == "neg":
            return ("neg", ResolvedRefs.resolve(n[1], mapping))
        return (n[0], n[1]) + tuple(ResolvedRefs.resolve(c, mapping)
                                    for c in n[2:])

    def test_same_as_dict_eval(self):
        """math_eval_with(ref_real) == math_eval(dicts) en expresión compleja."""
        t = np.linspace(0, 1e-3, 501)
        a, b = np.sin(2e4 * t), np.cos(3e4 * t) + 0.5
        expr = "INT(CH1*CH2) - DIFF(M1)/1E4 + MAX(ABS(CH1), SQRT(CH2))"
        ast = mx.math_parse(expr)
        ref = mx.math_eval(ast, t, {0: a, 1: b}, {0: a + b})
        channels = {7: a, 9: b, 4: a + b}
        res = self.resolve(ast, {("CH", 0): 7, ("CH", 1): 9, ("M", 0): 4})
        got = mx.math_eval_with(res, t, lambda n: channels[n[1]])
        np.testing.assert_allclose(got, ref)


if __name__ == "__main__":
    unittest.main()
