"""format_si / parse_si: formato y lectura con prefijos SI."""
import unittest

from ocv.core import si_format as si


class FormatSI(unittest.TestCase):

    def test_prefixes(self):
        """Escalado a G/M/k/-/m/µ/n/p."""
        cases = [
            (2.5e9, "Hz", "2.5 GHz"),
            (120e3, "Hz", "120 kHz"),
            (1.0, "V", "1 V"),
            (1.5e-3, "s", "1.5 ms"),
            (33.3e-6, "s", "33.3 µs"),
            (4.7e-9, "s", "4.7 ns"),
            (10e-12, "F", "10 pF"),
            (-3.3, "V", "-3.3 V"),
        ]
        for value, unit, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(si.format_si(value, unit), expected)

    def test_sig_figs(self):
        """sig_figs=3 recorta etiquetas de ticks."""
        self.assertEqual(si.format_si(33.3332e-6, "s", sig_figs=3), "33.3 µs")

    def test_rounding_crosses_prefix(self):
        """Bug #23: si el redondeo llega a 1000, sube de prefijo.
        Antes: '1e+03 µs' en el tick de 1 ms."""
        f = si.format_si
        self.assertEqual(f(999.96e-6, "s", sig_figs=3), "1 ms")
        self.assertEqual(f(-999.96e-6, "s", sig_figs=3), "-1 ms")
        self.assertEqual(f(1e-3 * (1 - 1e-12), "s"), "1 ms")
        self.assertEqual(f(0.9999999, "V", sig_figs=3), "1 V")
        self.assertEqual(f(999999.9, "Hz", sig_figs=3), "1 MHz")
        self.assertEqual(f(999.4e-6, "s", sig_figs=3), "999 µs")

    def test_tick_relative_to_step(self):
        """Bug #31: tick a 26 ns del cero con 1 ms/div salía '26.1 ns'.
        La precisión del tick va con el paso de la grilla."""
        f = si.format_tick
        self.assertEqual(f(26.1e-9, 1e-3, "s"), "0 s")
        self.assertEqual(f(-1.99997e-3, 1e-3, "s"), "-2 ms")
        self.assertEqual(f(1.00003e-3, 1e-3, "s"), "1 ms")
        self.assertEqual(f(2.5e-6, 1e-6, "s"), "2.5 µs")
        self.assertEqual(f(26.1e-9, 10e-9, "s"), "26.1 ns")

    def test_zero_noise(self):
        """|x| < 1e-15 se muestra como 0."""
        self.assertEqual(si.format_si(1e-16, "V"), "0 V")
        self.assertEqual(si.format_si(-1e-16, "—"), "0")

    def test_no_prefix_units(self):
        """°C, dB, % no se escalan; % con 2 decimales; — sin sufijo."""
        self.assertEqual(si.format_si(1500, "°C"), "1500 °C")
        self.assertEqual(si.format_si(0.002, "dB"), "0.002 dB")
        self.assertEqual(si.format_si(32, "%"), "32.00 %")
        self.assertEqual(si.format_si(1500, "—"), "1500")


class ParseSI(unittest.TestCase):

    def test_values(self):
        """Prefijos, espacios, unidad opcional, notación exponencial."""
        cases = [
            ("240k", "", 240e3),
            ("240 kHz", "Hz", 240e3),
            ("1.5M", "", 1.5e6),
            ("20 us", "s", 20e-6),
            ("20 µs", "s", 20e-6),
            (".5m", "", 0.5e-3),
            ("-3.3", "V", -3.3),
            ("1e-3", "", 1e-3),
            ("100 mV", "V", 0.1),
            ("5 v", "V", 5.0),
            ("1.", "", 1.0),
            ("2.k", "", 2e3),
        ]
        for text, unit, expected in cases:
            with self.subTest(text=text):
                got = si.parse_si(text, unit)
                self.assertIsNotNone(got)
                self.assertAlmostEqual(got, expected, delta=abs(expected) * 1e-12)

    def test_case_sensitive(self):
        """m = mili, M = mega."""
        self.assertAlmostEqual(si.parse_si("1m"), 1e-3)
        self.assertAlmostEqual(si.parse_si("1M"), 1e6)

    def test_invalid(self):
        """Basura → None."""
        for text in [None, "", "abc", "1.2.3", "5 X", "1 kk"]:
            with self.subTest(text=text):
                self.assertIsNone(si.parse_si(text))

    def test_roundtrip(self):
        """format_si → parse_si recupera el valor."""
        for value in [2.2e9, 47e3, 1.0, 3.3e-3, 12e-6, 5e-9]:
            with self.subTest(value=value):
                text = si.format_si(value, "s")
                self.assertAlmostEqual(si.parse_si(text, "s"), value,
                                       delta=value * 1e-9)


if __name__ == "__main__":
    unittest.main()
