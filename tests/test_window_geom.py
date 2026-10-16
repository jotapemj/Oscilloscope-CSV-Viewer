"""Geometría de la ventana principal: parseo de 'WxH+X+Y' y restauración."""
import unittest

from ocv.ui.window_geom import _parse_geometry_string as parse
from ocv.ui.window_geom import _position_main_window


class ParseGeometry(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(parse("1536x960+100+50"), (1536, 960, 100, 50))
        self.assertEqual(parse("800x600-50+20"), (800, 600, -50, 20))
        self.assertEqual(parse("800x600+20-50"), (800, 600, 20, -50))

    def test_invalid(self):
        for s in (None, "", "1536x960", "abc", "x+1+2", "10x20+1",
                  "10x20++1+2", "10x20+1+2junk"):
            with self.subTest(s=s):
                self.assertIsNone(parse(s))

    # Bug #13: x/y negativas llegan como '+-N' y no se parseaban
    def test_monitor_left_of_primary(self):
        """Tk en Windows da '+-1920+0' para x negativa (monitor a la izq.)."""
        self.assertEqual(parse("1536x960+-1920+0"), (1536, 960, -1920, 0))
        self.assertEqual(parse("1536x960+100+-1080"), (1536, 960, 100, -1080))


class FakeRoot:
    """Root mínimo: primaria 1920x1080 y escritorio virtual configurable."""

    def __init__(self, vroot=(0, 0, 1920, 1080)):
        self.vroot = vroot
        self.geom = None

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def winfo_vrootx(self):
        return self.vroot[0]

    def winfo_vrooty(self):
        return self.vroot[1]

    def winfo_vrootwidth(self):
        return self.vroot[2]

    def winfo_vrootheight(self):
        return self.vroot[3]

    def winfo_width(self):
        return 1536

    def winfo_height(self):
        return 960

    def update_idletasks(self):
        pass

    def geometry(self, g):
        self.geom = g


CENTERED = "1536x960+192+60"


class PositionMainWindow(unittest.TestCase):

    def place(self, saved, vroot=(0, 0, 1920, 1080)):
        root = FakeRoot(vroot)
        _position_main_window(root, {"main_window_geom": saved})
        return root.geom

    def test_primary_restored(self):
        self.assertEqual(self.place("1200x800+100+50"), "1200x800+100+50")

    def test_offscreen_centered(self):
        self.assertEqual(self.place("1200x800+5000+50"), CENTERED)

    def test_no_saved_centered(self):
        root = FakeRoot()
        self.assertTrue(_position_main_window(root, {}))
        self.assertEqual(root.geom, CENTERED)

    # Bug #14: solo se aceptaba la primaria
    def test_secondary_left_restored(self):
        left = (-1920, 0, 3840, 1080)
        self.assertEqual(self.place("1200x800+-1800+50", left),
                         "1200x800+-1800+50")

    def test_secondary_right_restored(self):
        right = (0, 0, 3840, 1080)
        self.assertEqual(self.place("1200x800+2100+50", right),
                         "1200x800+2100+50")

    def test_secondary_above_restored(self):
        above = (0, -1080, 1920, 2160)
        self.assertEqual(self.place("1200x800+100+-900", above),
                         "1200x800+100+-900")

    def test_monitor_gone_centered(self):
        """Monitor izquierdo desconectado: vuelve a la primaria."""
        self.assertEqual(self.place("1200x800+-1800+50"), CENTERED)


if __name__ == "__main__":
    unittest.main()
