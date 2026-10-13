"""Generadores de señales sintéticas para los tests."""
import numpy as np


def trapezoid(period=1e-3, high=3.3, low=0.0, rise=20e-6, fall=20e-6,
              width=300e-6, n_periods=3, fs=10e6, t0=-100e-6):
    """Tren de pulsos trapezoidal.

    Flanco de subida empieza en k*period, dura `rise`. Nivel alto dura
    `width`, luego flanco de bajada `fall`. Ancho medido a mid-level =
    width + rise/2 + fall/2.
    """
    t = t0 + np.arange(int(n_periods * period * fs)) / fs
    ph = np.mod(t, period)
    knots_t = [0.0, rise, rise + width, rise + width + fall, period]
    knots_v = [low, high, high, low, low]
    return t, np.interp(ph, knots_t, knots_v)


def sine(freq=1e3, amp=1.0, dc=0.0, phase_deg=0.0, n_periods=5, fs=1e6):
    t = np.arange(int(n_periods * fs / freq)) / fs
    v = dc + amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))
    return t, v
