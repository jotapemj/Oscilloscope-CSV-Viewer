"""Medidas automáticas sobre señales sintéticas con valor conocido."""
import unittest

import numpy as np

import main as ocv
from tests.signals import trapezoid, sine


class PulseTrain(unittest.TestCase):
    """Trapecio 0..3.3 V, 1 kHz, flancos 20 µs, ancho a mid = 320 µs."""

    @classmethod
    def setUpClass(cls):
        cls.t, cls.v = trapezoid()

    def check(self, name, got, expected, tol):
        self.assertIsNotNone(got, f"{name}: None")
        self.assertAlmostEqual(got, expected, delta=tol,
                               msg=f"{name}: got {got!r}, expected {expected!r}")

    def test_levels(self):
        """Top/Base/Amplitude cerca de 3.3 / 0 / 3.3 V."""
        self.check("top", ocv.meas_top_level(self.v), 3.3, 0.02)
        self.check("base", ocv.meas_base_level(self.v), 0.0, 0.02)
        self.check("amplitude", ocv.meas_amplitude(self.v), 3.3, 0.04)
        self.check("pk-pk", ocv.meas_peak_to_peak(self.v), 3.3, 1e-9)

    def test_period_frequency(self):
        """Periodo 1 ms, frecuencia 1 kHz."""
        self.check("period", ocv.meas_period(self.t, self.v), 1e-3, 1e-7)
        self.check("freq", ocv.meas_frequency(self.t, self.v), 1e3, 0.1)

    def test_edges(self):
        """Rise/Fall 10-90 % de un flanco lineal de 20 µs = 16 µs."""
        self.check("rise", ocv.meas_rise_time(self.t, self.v), 16e-6, 0.5e-6)
        self.check("fall", ocv.meas_fall_time(self.t, self.v), 16e-6, 0.5e-6)

    def test_slew(self):
        """Slew = 3.3 V / 20 µs = 165 kV/s; bajada negativa."""
        self.check("rise slew", ocv.meas_rising_slew_rate(self.t, self.v),
                   165e3, 5e3)
        self.check("fall slew", ocv.meas_falling_slew_rate(self.t, self.v),
                   -165e3, 5e3)

    def test_widths_and_duty(self):
        """Ancho + 320 µs, ancho - 680 µs, duty 32 % / 68 %."""
        self.check("+width", ocv.meas_positive_width(self.t, self.v),
                   320e-6, 0.5e-6)
        self.check("-width", ocv.meas_negative_width(self.t, self.v),
                   680e-6, 0.5e-6)
        self.check("+duty", ocv.meas_positive_duty(self.t, self.v), 32.0, 0.1)
        self.check("-duty", ocv.meas_negative_duty(self.t, self.v), 68.0, 0.1)

    def test_high_low_time(self):
        """Ventana de 3 periodos exactos: high 960 µs, low 2040 µs."""
        t, v = trapezoid(t0=0.0)
        self.check("high", ocv.meas_high_time(t, v), 960e-6, 2e-6)
        self.check("low", ocv.meas_low_time(t, v), 2040e-6, 2e-6)

    def test_no_overshoot(self):
        """Sin overshoot: ambos overshoots ~0 %."""
        self.check("+os", ocv.meas_positive_overshoot(self.v), 0.0, 0.5)
        self.check("-os", ocv.meas_negative_overshoot(self.v), 0.0, 0.5)

    def test_overshoot(self):
        """Pico de 3.63 V sobre top 3.3 V = 10 % overshoot."""
        v = self.v.copy()
        v[np.argmax(v)] = 3.63
        self.check("+os", ocv.meas_positive_overshoot(v), 10.0, 0.7)


class Sine(unittest.TestCase):

    def test_rms(self):
        """RMS de seno 1 V pico = 1/√2."""
        _, v = sine(amp=1.0)
        self.assertAlmostEqual(ocv.meas_rms(v), 1 / np.sqrt(2), delta=1e-3)

    def test_frequency_with_dc(self):
        """Seno 1 kHz con 2.5 V de DC: frecuencia sigue en 1 kHz."""
        t, v = sine(dc=2.5)
        self.assertAlmostEqual(ocv.meas_frequency(t, v), 1e3, delta=0.5)

    def test_phase_lag(self):
        """CH2 retrasado 90° respecto a CH1 → fase +90°."""
        t, v1 = sine()
        _, v2 = sine(phase_deg=-90)
        got = ocv.meas_phase_hilbert(t, v1, v2)
        self.assertAlmostEqual(got, 90.0, delta=1.0, msg=f"got {got}")

    def test_phase_crossings_fallback(self):
        """Fallback por cruces (sin scipy) da el mismo signo y valor."""
        t, v1 = sine()
        _, v2 = sine(phase_deg=-90)
        got = ocv._meas_phase_crossings(t, v1, v2)
        self.assertAlmostEqual(got, 90.0, delta=1.0, msg=f"got {got}")


class Degenerate(unittest.TestCase):
    """Entradas vacías o planas: None, nunca excepción."""

    def test_empty(self):
        e = np.array([])
        self.assertIsNone(ocv.meas_peak_to_peak(e))
        self.assertIsNone(ocv.meas_rms(e))
        self.assertIsNone(ocv.meas_period(e, e))

    def test_flat(self):
        t = np.arange(100.0)
        v = np.full(100, 1.0)
        self.assertIsNone(ocv.meas_period(t, v))
        self.assertIsNone(ocv.meas_rise_time(t, v))
        self.assertIsNone(ocv.meas_positive_overshoot(v))
        self.assertEqual(ocv.meas_peak_to_peak(v), 0.0)


if __name__ == "__main__":
    unittest.main()
