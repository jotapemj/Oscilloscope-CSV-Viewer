"""Geometría de la ventana principal: parseo de 'WxH+X+Y'."""
import unittest

from ocv.ui.window_geom import _parse_geometry_string as parse


class ParseGeometry(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(parse("1536x960+100+50"), (1536, 960, 100, 50))
        self.assertEqual(parse("800x600-50+20"), (800, 600, -50, 20))
        self.assertEqual(parse("800x600+20-50"), (800, 600, 20, -50))

    def test_invalid(self):
        for s in (None, "", "1536x960", "abc", "x+1+2", "10x20+1"):
            with self.subTest(s=s):
                self.assertIsNone(parse(s))

    # Bug #13 (bitácora): x/y negativas llegan como '+-N' y no se parsean
    @unittest.expectedFailure
    def test_monitor_left_of_primary(self):
        """Tk en Windows da '+-1920+0' para x negativa (monitor a la izq.)."""
        self.assertEqual(parse("1536x960+-1920+0"), (1536, 960, -1920, 0))
        self.assertEqual(parse("1536x960+100+-1080"), (1536, 960, 100, -1080))


if __name__ == "__main__":
    unittest.main()
