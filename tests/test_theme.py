"""Temas: paleta completa, contraste medido y re-tematizado en vivo."""
import tkinter as tk
import unittest
from tkinter import ttk

from ocv.ui import theme

MIN_TEXT = 4.5       # WCAG AA texto normal


def _lum(hex_color):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in theme._hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    named = {"black": "#000000", "white": "#ffffff"}
    hi, lo = sorted((_lum(named.get(fg, fg)), _lum(named.get(bg, bg))),
                    reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Palette(unittest.TestCase):

    def test_same_roles(self):
        """Todos los temas definen los mismos roles."""
        roles = set(theme.THEMES["Dark"])
        for name, t in theme.THEMES.items():
            with self.subTest(theme=name):
                self.assertEqual(set(t), roles)

    def test_lerp(self):
        """Interpolación: extremos exactos, punto medio redondeado."""
        self.assertEqual(theme.lerp_color("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(theme.lerp_color("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(theme.lerp_color("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(theme.lerp_color("#fff", "#000000", 0), "#ffffff")

    def test_channel_colors_cycle(self):
        n = len(theme.CHANNEL_COLORS)
        self.assertEqual(theme.channel_color(n), theme.channel_color(0))


class Contrast(unittest.TestCase):
    """Contraste calculado (WCAG 2.x), por tema."""

    def check(self, pairs, minimum):
        for name, t in theme.THEMES.items():
            for fg, bg in pairs:
                with self.subTest(theme=name, pair=f"{fg}/{bg}"):
                    c = contrast(t[fg], t[bg])
                    self.assertGreaterEqual(
                        c, minimum, f"{name} {fg}/{bg} = {c:.2f}:1")

    def test_body_text(self):
        """Texto principal y secundario sobre todos los fondos de panel."""
        self.check([("TEXT", "BG"), ("TEXT", "BG_ALT"), ("TEXT", "BG_HOVER"),
                    ("TEXT_DIM", "BG"), ("TEXT_DIM", "BG_ALT"),
                    ("PLOT_TEXT", "PLOT_BG"),
                    ("PLOT_TEXT", "PLOT_TIME_LBL_BG")], MIN_TEXT)

    def test_accent_button(self):
        """Botón Accent en reposo: texto negro sobre ACCENT."""
        for name, t in theme.THEMES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(contrast("#000000", t["ACCENT"]),
                                        MIN_TEXT)

    # Pendiente decisión de diseño (bitácora): Light ACCENT/BG = 4.15
    @unittest.expectedFailure
    def test_accent_headers(self):
        """Cabeceras en color ACCENT sobre BG."""
        self.check([("ACCENT", "BG")], MIN_TEXT)

    # Pendiente decisión de diseño: High Contrast 1.72, Blueprint 2.90
    @unittest.expectedFailure
    def test_accent_button_pressed(self):
        """Botón Accent pulsado: texto blanco sobre ACCENT_2."""
        for name, t in theme.THEMES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(contrast("#ffffff", t["ACCENT_2"]),
                                        MIN_TEXT)


class LiveRetheme(unittest.TestCase):
    """apply_theme + _retheme_tk_widgets sobre widgets Tk reales."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk no disponible: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        theme.apply_theme(cls.root, "Dark")
        cls.root.destroy()

    def test_globals_follow_theme(self):
        """theme.COLOR_* cambia con apply_theme; nombre desconocido → Dark."""
        for name, t in theme.THEMES.items():
            theme.apply_theme(self.root, name)
            self.assertEqual(theme.COLOR_BG, t["BG"])
            self.assertEqual(theme.CURRENT_THEME_NAME, name)
        theme.apply_theme(self.root, "Nope")
        self.assertEqual(theme.CURRENT_THEME_NAME, "Dark")

    def test_ttk_style(self):
        """ttk.Style recibe los colores del tema."""
        theme.apply_theme(self.root, "Light")
        style = ttk.Style(self.root)
        self.assertEqual(str(style.lookup("TLabel", "background")),
                         theme.THEMES["Light"]["BG"])

    def test_tk_widgets_remapped(self):
        """Widgets tk clásicos pasan al rol equivalente; protegidos no."""
        dark, blue = theme.THEMES["Dark"], theme.THEMES["Blueprint"]
        theme.apply_theme(self.root, "Dark")
        frame = tk.Frame(self.root, bg=dark["BG_ALT"])
        label = tk.Label(frame, bg=dark["BG"], fg=dark["TEXT_DIM"])
        canvas = tk.Canvas(frame, bg=dark["BG"],
                           highlightbackground=dark["BORDER"])
        swatch = tk.Label(frame, bg=dark["BG"])
        swatch._theme_protected = True
        theme.apply_theme(self.root, "Blueprint")
        theme._retheme_tk_widgets(self.root)
        self.assertEqual(frame.cget("bg"), blue["BG_ALT"])
        self.assertEqual((label.cget("bg"), label.cget("fg")),
                         (blue["BG"], blue["TEXT_DIM"]))
        self.assertEqual(canvas.cget("highlightbackground"), blue["BORDER"])
        self.assertEqual(swatch.cget("bg"), dark["BG"])
        frame.destroy()


if __name__ == "__main__":
    unittest.main()
