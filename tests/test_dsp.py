"""Decimado min/max, ventanas FFT y escalas del eje Y del FFT."""
import unittest

import numpy as np

from ocv.core import dsp


class Decimate(unittest.TestCase):

    def test_passthrough(self):
        """Por debajo del umbral devuelve los mismos arrays."""
        t = np.arange(100.0)
        v = np.sin(t)
        dt, dv = dsp.minmax_decimate(t, v, 100)
        self.assertIs(dt, t)
        self.assertIs(dv, v)

    def test_keeps_peaks(self):
        """Un glitch de 1 muestra sobrevive al decimado."""
        rng = np.random.default_rng(0)
        n = 200_000
        t = np.arange(n) * 1e-6
        v = rng.normal(0, 0.01, n)
        v[123_457] = 5.0
        v[54_321] = -4.0
        dt, dv = dsp.minmax_decimate(t, v, 500)
        self.assertLessEqual(len(dv), 4 * 500)
        self.assertEqual(dv.max(), 5.0)
        self.assertEqual(dv.min(), -4.0)
        self.assertTrue(np.all(np.diff(dt) > 0), "time not increasing")
        self.assertEqual(dt[0], t[0])
        self.assertEqual(dt[-1], t[-1])


class MergeRepeatedTimes(unittest.TestCase):
    """Bug #22: peak detect → un valor (media) por instante."""

    def test_pairs_averaged(self):
        t = np.repeat(np.arange(5) * 1e-6, 2)
        v = np.tile([1.0, 3.0], 5)
        tu, vu = dsp.merge_repeated_times(t, v)
        np.testing.assert_allclose(tu, np.arange(5) * 1e-6)
        np.testing.assert_allclose(vu, 2.0)

    def test_strict_unchanged(self):
        t = np.arange(10.0)
        v = np.sin(t)
        tu, vu = dsp.merge_repeated_times(t, v)
        self.assertIs(tu, t)
        self.assertIs(vu, v)


class Window(unittest.TestCase):

    def test_coherent_gain(self):
        """Ganancia coherente ≈ tabla FFT_WINDOWS."""
        v = np.ones(4096)
        for name, info in dsp.FFT_WINDOWS.items():
            with self.subTest(window=name):
                _, cg = dsp.apply_window(v, name)
                self.assertAlmostEqual(cg, info["coherent_gain"], delta=2e-3)

    def test_unknown_is_rectangular(self):
        v = np.arange(8.0)
        out, cg = dsp.apply_window(v, "Nope")
        np.testing.assert_array_equal(out, v)
        self.assertEqual(cg, 1.0)


class YScale(unittest.TestCase):

    def test_conversions(self):
        """1 Vrms = 0 dBV = 120 dBµV = 13.01 dBm (50 Ω)."""
        one = np.array([1.0])
        self.assertAlmostEqual(dsp.vrms_to_scale(one, "dBV")[0], 0.0)
        self.assertAlmostEqual(dsp.vrms_to_scale(one, "dBµV")[0], 120.0)
        self.assertAlmostEqual(dsp.vrms_to_scale(one, "dBm (50Ω)")[0],
                               13.0103, delta=1e-3)

    def test_zero_is_finite(self):
        """0 V no da -inf en escalas dB."""
        out = dsp.vrms_to_scale(np.array([0.0]), "dBV")
        self.assertTrue(np.isfinite(out[0]))


if __name__ == "__main__":
    unittest.main()
