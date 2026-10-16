"""Temas: paleta completa, contraste medido y re-tematizado en vivo."""
import tkinter as tk
import unittest
from tkinter import ttk

from ocv.ui import theme

MIN_TEXT = 4.5       # WCAG AA texto normal
MIN_UI = 3.0         # WCAG 1.4.11 componentes no textuales (flechas, ticks)
contrast = theme.contrast_ratio


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

    def test_accent_headers(self):
        """Cabeceras en color ACCENT sobre BG."""
        self.check([("ACCENT", "BG")], MIN_TEXT)

    def test_text_on_accent_states(self):
        """Texto calculado sobre acento en reposo, hover y pulsado."""
        self.check([("TEXT_ON_ACCENT", "ACCENT"),
                    ("TEXT_ON_ACCENT_HOVER", "ACCENT_HOVER"),
                    ("TEXT_ON_ACCENT_2", "ACCENT_2")], MIN_TEXT)


class StyleStates(unittest.TestCase):
    """Contraste medido sobre ttk.Style real, estado a estado."""

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

    def each_theme(self):
        for name in theme.THEMES:
            theme.apply_theme(self.root, name)
            yield name, ttk.Style(self.root)

    def test_accent_button(self):
        """Botón Accent: reposo, hover y pulsado."""
        for name, st in self.each_theme():
            for state in ([], ["active"], ["pressed", "active"]):
                with self.subTest(theme=name, state=state):
                    bg = st.lookup("Accent.TButton", "background", state)
                    fg = st.lookup("Accent.TButton", "foreground", state)
                    self.assertGreaterEqual(contrast(fg, bg), MIN_TEXT,
                                            f"{fg} on {bg}")

    def test_pressed_differs_from_hover(self):
        """El estado pulsado se ve (antes 'active' lo tapaba)."""
        for name, st in self.each_theme():
            with self.subTest(theme=name):
                self.assertEqual(
                    st.lookup("Accent.TButton", "background",
                              ["pressed", "active"]),
                    theme.THEMES[name]["ACCENT_2"])

    def test_spin_combo_arrow(self):
        """Flecha de Combobox/Spinbox visible en hover, active y pressed."""
        for name, st in self.each_theme():
            for w in ("TCombobox", "TSpinbox"):
                for state in (["hover"], ["active"], ["pressed"], []):
                    with self.subTest(theme=name, widget=w, state=state):
                        bg = st.lookup(w, "background", state)
                        arrow = st.lookup(w, "arrowcolor", state)
                        self.assertGreaterEqual(contrast(arrow, bg), MIN_UI,
                                                f"{arrow} on {bg}")

    def test_checkbox_tick(self):
        """Bug #21: tick legible sobre la casilla. 'clam' pinta la casilla
        con indicatorbackground (indicatorcolor lo ignora)."""
        for name, st in self.each_theme():
            for w in ("TCheckbutton", "TRadiobutton"):
                for state in (["selected"], ["selected", "active"]):
                    with self.subTest(theme=name, widget=w, state=state):
                        box = st.lookup(w, "indicatorbackground", state)
                        tick = st.lookup(w, "indicatorforeground", state)
                        self.assertGreaterEqual(contrast(tick, box), MIN_UI,
                                                f"{tick} on {box}")

    def test_treeview_selection(self):
        for name, st in self.each_theme():
            with self.subTest(theme=name):
                bg = st.lookup("Treeview", "background", ["selected"])
                fg = st.lookup("Treeview", "foreground", ["selected"])
                self.assertGreaterEqual(contrast(fg, bg), MIN_TEXT)


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

    def test_accent_text_follows_theme(self):
        """Texto sobre acento (blanco en Light) → color correcto en Dark.
        Sin tema de origen, #ffffff se confundía con TEXT de High Contrast."""
        light, dark = theme.THEMES["Light"], theme.THEMES["Dark"]
        theme.apply_theme(self.root, "Light")
        lbl = tk.Label(self.root, bg=light["ACCENT"],
                       fg=light["TEXT_ON_ACCENT"])
        theme.apply_theme(self.root, "Dark")
        theme._retheme_tk_widgets(self.root, prev_theme="Light")
        self.assertEqual(lbl.cget("bg"), dark["ACCENT"])
        self.assertEqual(lbl.cget("fg"), dark["TEXT_ON_ACCENT"])
        self.assertGreaterEqual(contrast(lbl.cget("fg"), lbl.cget("bg")),
                                MIN_TEXT)
        lbl.destroy()

    def test_retheme_twice_is_stable(self):
        """Bug #21: una pasada extra sin tema de origen (la de la ventana
        Settings) no debe re-mapear colores ya convertidos. Antes, #ffffff
        (TEXT de High Contrast) se leía como TEXT_ON_ACCENT de Light →
        negro. Dos pasadas CON origen son ambiguas por diseño: la app hace
        una sola (ver test de contraste en el smoke)."""
        for a in theme.THEMES:
            for b in theme.THEMES:
                if a == b:
                    continue
                with self.subTest(frm=a, to=b):
                    ta, tb = theme.THEMES[a], theme.THEMES[b]
                    theme.apply_theme(self.root, a)
                    lbl = tk.Label(self.root, bg=ta["BG"], fg=ta["TEXT"])
                    dim = tk.Label(self.root, bg=ta["BG"], fg=ta["TEXT_DIM"])
                    theme.apply_theme(self.root, b)
                    theme._retheme_tk_widgets(self.root, prev_theme=a)
                    theme._retheme_tk_widgets(self.root)
                    self.assertEqual((lbl.cget("bg"), lbl.cget("fg")),
                                     (tb["BG"], tb["TEXT"]))
                    self.assertEqual(dim.cget("fg"), tb["TEXT_DIM"])
                    lbl.destroy()
                    dim.destroy()


if __name__ == "__main__":
    unittest.main()
