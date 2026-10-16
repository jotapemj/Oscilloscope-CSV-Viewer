"""Carga inteligente de CSV: separador, cabeceras, columna de tiempo."""
import os
import tempfile
import unittest

import numpy as np

from ocv.core import csv_io


class CsvSmart(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def write(self, text, name="data.csv", encoding="utf-8"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        return path

    @staticmethod
    def rows(n=50, sep=",", dec=".", t0=0.0, dt=1e-6):
        out = []
        for i in range(n):
            t = f"{t0 + i * dt:.9e}".replace(".", dec)
            a = f"{np.sin(i / 5):.6f}".replace(".", dec)
            b = f"{i % 2:.1f}".replace(".", dec)
            out.append(sep.join([t, a, b]))
        return "\n".join(out) + "\n"

    def test_plain_header(self):
        """Cabecera simple: tiempo detectado con confianza alta."""
        p = self.write("Time,CH1,CH2\n" + self.rows())
        r = csv_io._read_csv_smart(p)
        self.assertEqual(r["separator"], ",")
        self.assertEqual(r["time_confidence"], "high")
        self.assertEqual(r["time_col_index"], 0)
        self.assertEqual(r["col_names"], ["CH1", "CH2"])
        self.assertEqual(len(r["time"]), 50)
        self.assertAlmostEqual(r["time"][1] - r["time"][0], 1e-6)

    def test_european(self):
        """Separador ';' con coma decimal."""
        p = self.write("Time;CH1;CH2\n" + self.rows(sep=";", dec=","))
        r = csv_io._read_csv_smart(p)
        self.assertEqual(r["separator"], ";")
        self.assertEqual(r["time_confidence"], "high")
        self.assertAlmostEqual(r["columns"][0][5], np.sin(1.0), places=5)

    def test_tab(self):
        p = self.write("Time\tCH1\tCH2\n" + self.rows(sep="\t"))
        self.assertEqual(csv_io._read_csv_smart(p)["separator"], "\t")

    def test_metadata_header(self):
        """Cabecera tipo Tek con metadata numérica antes de los datos."""
        head = ("Model,MSO44\n"
                "Record Length,50\n"
                "Sample Interval,1e-06\n"
                "Trigger Point,0\n"
                "\n"
                "TIME,CH1,CH2\n")
        p = self.write(head + self.rows())
        r = csv_io._read_csv_smart(p)
        self.assertEqual(r["n_metadata_lines"], 6)
        self.assertEqual(r["brand"], "Tektronix")
        self.assertEqual(r["metadata"].get("x_increment"), 1e-6)
        self.assertEqual(r["col_names"], ["CH1", "CH2"])
        self.assertEqual(len(r["time"]), 50)

    def test_time_not_first_column(self):
        """La columna de tiempo gana por score, no por posición."""
        lines = ["CH1,Time"]
        for i in range(50):
            lines.append(f"{np.sin(i):.6f},{i * 1e-3:.6e}")
        p = self.write("\n".join(lines) + "\n")
        r = csv_io._read_csv_smart(p)
        self.assertEqual(r["time_col_index"], 1)
        self.assertEqual(r["col_names"], ["CH1"])

    def test_peak_detect_pairs(self):
        """Bug #19: export peak detect (cada instante dos veces, mín/máx)
        se reconoce como tiempo sin preguntar."""
        lines = ["in s,C1 in V"]
        for i in range(60):
            t = -1e-3 + i * 9.28e-8
            lines += [f"{t:.8E},{12.69 + i % 3 * 0.01:.5E}",
                      f"{t:.8E},{13.03 + i % 2 * 0.01:.5E}"]
        r = csv_io._read_csv_smart(self.write("\n".join(lines) + "\n"))
        self.assertEqual(r["time_confidence"], "high")
        self.assertEqual(r["time_col_index"], 0)
        self.assertEqual(len(r["time"]), 120)

    def test_staircase_is_not_time(self):
        """Columna escalonada (contador, runs largos) no es tiempo."""
        s = csv_io._score_time_column(np.repeat(np.arange(20.0), 5))
        self.assertEqual(s["score"], 0.0)
        s = csv_io._score_time_column(np.repeat(np.arange(20.0), 2))
        self.assertGreater(s["score"], 0.85)

    def test_no_time_uses_metadata(self):
        """Sin columna de tiempo: se reconstruye con XIncrement/XStart."""
        head = "XIncrement,2e-6\nXStart,-1e-4\nCH1,CH2\n"
        body = "\n".join(f"{np.sin(i):.6f},{np.cos(i):.6f}" for i in range(40))
        p = self.write(head + body + "\n")
        r = csv_io._read_csv_smart(p)
        self.assertEqual(r["time_confidence"], "none")
        self.assertIsNotNone(r["time"])
        self.assertAlmostEqual(r["time"][0], -1e-4)
        self.assertAlmostEqual(r["time"][1] - r["time"][0], 2e-6)
        self.assertEqual(len(r["columns"]), 2)

    def test_bom_and_crlf(self):
        """BOM UTF-8 y finales CRLF no rompen la cabecera."""
        text = ("﻿Time,CH1,CH2\n" + self.rows()).replace("\n", "\r\n")
        r = csv_io._read_csv_smart(self.write(text))
        self.assertEqual(r["all_col_names"][0], "Time")

    def test_trailing_empty_columns(self):
        """Columnas vacías al final (',,') se descartan."""
        body = self.rows().replace("\n", ",,\n")
        r = csv_io._read_csv_smart(self.write("Time,CH1,CH2,,\n" + body))
        self.assertEqual(len(r["all_columns"]), 3)

    def test_errors(self):
        """Vacío o sin números → ValueError."""
        for text in ["", "hello\nworld\n"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    csv_io._read_csv_smart(self.write(text))


class Brand(unittest.TestCase):
    """Marca del scope a partir de la cabecera."""

    def check(self, header, expected):
        got = csv_io._detect_scope_brand(header)
        self.assertEqual(got, expected, f"{header!r} -> {got!r}")

    def test_brand_names(self):
        """Nombre de marca explícito gana siempre."""
        self.check(["Model,RIGOL MSO5074"], "Rigol")
        self.check(["Agilent Technologies"], "Keysight")
        self.check(["Rohde&Schwarz,RTB2004"], "Rohde & Schwarz")
        self.check(["TEKTRONIX,MSO44"], "Tektronix")

    def test_models(self):
        """Modelos inequívocos sin nombre de marca."""
        self.check(["Model,MSO46"], "Tektronix")
        self.check(["Model,MSO58B"], "Tektronix")
        self.check(["Model,DPO4054"], "Tektronix")
        self.check(["Model,DS1054Z"], "Rigol")
        self.check(["Model,RTB2004"], "Rohde & Schwarz")
        self.check(["Model,SDS2104X Plus"], "Siglent")

    def test_ambiguous(self):
        """MSO + 4 dígitos existe en Tek y Rigol → sin marca."""
        self.check(["Model,MSO5074"], None)

    def test_no_false_positives(self):
        """Subcadenas dentro de palabras no cuentan."""
        self.check(["Data converted by tool"], None)
        self.check(["Probe attenuation,10"], None)
        self.check(["Source,CH1", "Vertical Units,V"], None)


class Metadata(unittest.TestCase):

    def test_offset_is_not_fs(self):
        """'Vertical Offset' no es sample rate."""
        meta = csv_io._extract_metadata(["Vertical Offset,0.5"], ",")
        self.assertNotIn("sample_rate", meta)

    def test_fs_exact(self):
        meta = csv_io._extract_metadata(["Fs,1e9", "Sample Rate,2e9"], ",")
        self.assertEqual(meta.get("sample_rate"), 1e9)


if __name__ == "__main__":
    unittest.main()
