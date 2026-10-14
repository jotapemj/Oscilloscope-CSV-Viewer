"""Smoke test de la app completa: Tk real sin mostrar ventana.

No mide píxeles; comprueba que los caminos principales se ejecutan sin
excepción tras mover código entre módulos (imports, nombres, wiring).
"""
import os
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace as Ev
import unittest
from unittest import mock

import numpy as np

import main
from ocv.ui import theme


def write_csv(path, n=4000, fs=1e6):
    t = np.arange(n) / fs
    ch1 = np.sin(2 * np.pi * 5e3 * t)
    ch2 = (np.sin(2 * np.pi * 1e3 * t) > 0) * 3.3
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Time,CH1,CH2\n")
        for row in zip(t, ch1, ch2):
            fh.write(",".join(f"{x:.9e}" for x in row) + "\n")


def wait_until(root, cond, timeout=15.0):
    """Bombea el bucle Tk hasta que cond() o timeout. La carga de CSV es
    asíncrona (hilo + after)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


class AppSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk no disponible: {e}")
        # Mapeada pero invisible: geometrías reales sin ventana en pantalla
        cls.root.attributes("-alpha", 0.0)
        cls.root.geometry("1400x900+40+40")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"OCV_CONFIG_DIR": cls.tmp.name})
        cls.env.start()
        theme.apply_theme(cls.root, "Dark")
        cls.app = main.OscilloscopeApp(cls.root)
        cls.csv = os.path.join(cls.tmp.name, "capture.csv")
        write_csv(cls.csv)
        cls.app._load_csv_from_path(cls.csv)
        if not wait_until(cls.root, lambda: len(cls.app.channels) == 2):
            raise AssertionError("CSV no cargó en 15 s")
        wait_until(cls.root, lambda: False, timeout=0.3)   # overlay fuera

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        finally:
            cls.env.stop()
            cls.tmp.cleanup()

    def pump(self):
        self.root.update_idletasks()
        self.root.update()

    def test_csv_loaded(self):
        names = [c["name"] for c in self.app.channels]
        self.assertEqual(len(self.app.channels), 2, names)
        self.assertEqual(len(self.app.time), 4000)

    def test_plot_and_measurements(self):
        """Redibujo completo y medidas sin excepción."""
        self.app.update_plot()
        self.pump()

    def test_secondary_windows(self):
        """FFT, Eye y Decoder se abren y se cierran."""
        for opener, attr in (("open_fft_window", "_fft_instance"),
                             ("open_eye_window", "_eye_instance"),
                             ("open_decoder_window", None)):
            with self.subTest(window=opener):
                before = set(self.root.winfo_children())
                getattr(self.app, opener)()
                self.pump()
                new = [w for w in self.root.winfo_children()
                       if w not in before and isinstance(w, tk.Toplevel)]
                self.assertTrue(new, f"{opener} no abrió Toplevel")
                if attr:
                    self.assertIsNotNone(getattr(self.app, attr, None))
                for w in new:
                    w.destroy()
                self.pump()

    def test_theme_switch_full_ui(self):
        """Cambio de tema sobre la UI entera, con FFT abierta."""
        self.app.open_fft_window()
        self.pump()
        for name in theme.THEMES:
            with self.subTest(theme=name):
                self.app._apply_theme_immediate(name, sync_plot=True)
                self.pump()
                self.assertEqual(theme.CURRENT_THEME_NAME, name)
        self.app._apply_theme_immediate("Dark", sync_plot=True)

    def test_theme_fade(self):
        """Cambio de tema con fundido: overlay, animación y limpieza."""
        self.app.apply_theme_runtime("Blueprint", sync_plot=True, fade=True)
        self.assertTrue(wait_until(
            self.root, lambda: theme.CURRENT_THEME_NAME == "Blueprint"
            and getattr(self.app, "_theme_fade_overlay", None) is None,
            timeout=5))
        self.app.apply_theme_runtime("Dark", sync_plot=True, fade=False)
        self.assertEqual(theme.CURRENT_THEME_NAME, "Dark")

    def test_minimap(self):
        """Clic fuera del rectángulo salta; arrastre mueve; rueda hace zoom."""
        app = self.app
        app.t_div.set(app.t_total / 40)          # ventana estrecha
        app.on_time_div_change()                 # como la UI: slider incluido
        app._redraw_minimap()
        self.pump()
        w = app.minimap_canvas.winfo_width()
        self.assertGreater(w, 100)
        app._on_minimap_press(Ev(x=int(w * 0.8), y=5))
        app._on_minimap_release(Ev(x=int(w * 0.8), y=5))
        jumped = app.t_offset.get()
        self.assertGreater(jumped, app.t_total * 0.5)
        x1, x2 = app._minimap_rect_bounds_px()
        mid = (x1 + x2) // 2
        app._on_minimap_press(Ev(x=mid, y=5))
        app._on_minimap_drag(Ev(x=mid - w // 4, y=5))
        app._on_minimap_release(Ev(x=mid - w // 4, y=5))
        self.assertLess(app.t_offset.get(), jumped)
        t_div = app.t_div.get()
        app._on_minimap_wheel(Ev(x=mid, y=5, delta=120))
        self.pump()
        self.assertNotEqual(app.t_div.get(), t_div)
        app.auto_fit()
        self.pump()

    def test_xy_mode(self):
        """XY on/off; al salir el plot temporal vuelve a tener trazas."""
        app = self.app
        app.open_xy_window()
        self.pump()
        self.assertIsNotNone(app._xy_window)
        app.xy_config["enabled"] = True
        app.update_plot()
        self.pump()
        app.xy_config["enabled"] = False
        app.update_plot()
        self.pump()
        live = [a for a in app._channel_lines.values()
                if a in app.ax.lines or a in app.ax.collections]
        self.assertTrue(live, "plot en blanco tras salir de XY")
        app._xy_window.destroy()
        app._xy_window = None

    def test_session_roundtrip(self):
        """Guardar .ocv y volver a cargarlo restaura los canales."""
        path = os.path.join(self.tmp.name, "s.ocv")
        self.app.channels[0]["v_div"] = 0.5
        self.assertTrue(self.app._write_session_to(path))
        self.app.channels[0]["v_div"] = 2.0
        self.app.session_path = None        # para detectar fin de carga
        with mock.patch("tkinter.filedialog.askopenfilename",
                        return_value=path):
            self.app.load_session()
        self.assertTrue(wait_until(
            self.root, lambda: self.app.session_path == path
            and not self.app.session_dirty))
        self.assertEqual(self.app.channels[0]["v_div"], 0.5)
        self.assertEqual(self.app.session_path, path)
        self.assertFalse(self.app.session_dirty)


if __name__ == "__main__":
    unittest.main()
