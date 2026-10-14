"""Smoke test de la app completa: Tk real sin mostrar ventana.

No mide píxeles; comprueba que los caminos principales se ejecutan sin
excepción tras mover código entre módulos (imports, nombres, wiring).
"""
import ctypes
import os
import struct
import sys
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


class FakeClipboardAPI:
    """user32/kernel32 falsos: memoria real, portapapeles del usuario intacto."""

    def __init__(self):
        self.buffers, self.calls, self.clip = {}, [], None
        api = self

        def fn(name, impl):
            def f(*a):
                api.calls.append(name)
                return impl(*a)
            return f

        def alloc(flags, size):
            buf = ctypes.create_string_buffer(size)
            h = 1000 + len(self.buffers)
            self.buffers[h] = buf
            return h

        def set_data(fmt, h):
            self.clip = (fmt, h)
            return h

        self.kernel32 = Ev(
            GlobalAlloc=fn("GlobalAlloc", alloc),
            GlobalLock=fn("GlobalLock",
                          lambda h: ctypes.addressof(self.buffers[h])),
            GlobalUnlock=fn("GlobalUnlock", lambda h: 1),
            GlobalFree=fn("GlobalFree", lambda h: 0))
        self.user32 = Ev(
            OpenClipboard=fn("OpenClipboard", lambda hwnd: 1),
            EmptyClipboard=fn("EmptyClipboard", lambda: 1),
            SetClipboardData=fn("SetClipboardData", set_data),
            CloseClipboard=fn("CloseClipboard", lambda: 1))


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

    def test_export_image(self):
        """Diálogo de exportación y PNG en carpeta temporal."""
        app = self.app
        before = set(self.root.winfo_children())
        app._open_save_image_dialog()
        self.pump()
        for w in set(self.root.winfo_children()) - before:
            w.destroy()
        out = os.path.join(self.tmp.name, "export.png")
        with mock.patch("tkinter.filedialog.asksaveasfilename",
                        return_value=out):
            app._save_image_as(include_cursors=True, include_meas=True,
                               include_annot=True)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    @unittest.skipUnless(sys.platform == "win32", "portapapeles solo Windows")
    def test_clipboard_dib(self):
        """DIB 32 bpp top-down, BGRA, y Windows se queda el handle."""
        app = self.app
        api = FakeClipboardAPI()
        with mock.patch("ctypes.windll", Ev(user32=api.user32,
                                            kernel32=api.kernel32)):
            app._copy_plot_to_clipboard()
        self.assertIsNotNone(api.clip, api.calls)
        fmt, h = api.clip
        self.assertEqual(fmt, 8)                       # CF_DIB
        self.assertNotIn("GlobalFree", api.calls)      # ownership
        raw = api.buffers[h].raw
        size, width, height, planes, bpp = struct.unpack_from("<IiiHH", raw)
        w, hgt = app.canvas.get_width_height()
        self.assertEqual((size, width, height, planes, bpp),
                         (40, w, -hgt, 1, 32))
        rgba = np.asarray(app.canvas.buffer_rgba())
        self.assertEqual(raw[40:44], bytes(rgba[0, 0, [2, 1, 0, 3]]))

    def mpl_event(self, xdata, ydata, button=1):
        """Evento matplotlib mínimo con coordenadas de datos y de píxel."""
        x, y = self.app.ax.transData.transform((xdata, ydata))
        return Ev(inaxes=self.app.ax, button=button, xdata=xdata,
                  ydata=ydata, x=x, y=y)

    def test_annotations(self):
        """Trazo, fijado a la escala de su canal, deshacer/rehacer, borrar."""
        app = self.app
        app.open_annotation_toolbar()
        self.pump()
        app.set_active_channel(0)
        app.channels[1]["v_div"] = app.channels[0]["v_div"] * 4
        n0 = len(app.annotations)
        app.annotation_mode = "draw"
        x0 = app.t_div.get()                       # 1 div desde el borde
        y0 = app.v_div.get() * 1.5                 # 1.5 div sobre GND
        app._on_annotation_press(self.mpl_event(x0, y0))
        app._on_annotation_move(self.mpl_event(x0 * 2, y0))
        app._on_annotation_release(self.mpl_event(x0 * 2, y0))
        self.assertEqual(len(app.annotations), n0 + 1)
        stroke = app.annotations[-1]
        self.assertEqual(stroke["ch_idx"], 0)

        def divs_on_screen():
            # Altura en divisiones con la escala del canal activo
            v_real = stroke["points"][0][1]
            return app._annot_screen_from_v_real(v_real, 0) / app.v_div.get()

        before = divs_on_screen()
        self.assertAlmostEqual(before, 1.5, places=9)
        app.set_active_channel(1)                  # otro V/div
        self.pump()
        self.assertAlmostEqual(divs_on_screen(), before, places=9)
        app.set_active_channel(0)

        app._undo_annotation_action()
        self.assertEqual(len(app.annotations), n0)
        app._redo_annotation_action()
        self.assertEqual(len(app.annotations), n0 + 1)
        app.annotation_mode = "erase"
        app._on_annotation_press(self.mpl_event(x0 * 2, y0))   # vértice
        self.assertEqual(len(app.annotations), n0)
        app.annotation_mode = None
        app.update_plot()
        self.pump()

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

    def test_trigger(self):
        """Edge y Width sobre el cuadrado de 1 kHz de CH2 (4 periodos)."""
        app = self.app
        app.open_trigger_window()
        self.pump()
        self.assertIsNotNone(app._trigger_window)
        cfg = app.trigger_config
        cfg.update(enabled=True, source=1, type="Edge", slope="Rising",
                   level=1.65)
        app._refresh_trigger()
        self.pump()
        rising = [e["t_trigger"] for e in app.trigger_events]
        self.assertEqual(len(rising), 4, rising)
        self.assertAlmostEqual(rising[1] - rising[0], 1e-3, delta=2e-6)
        app.t_div.set(50e-6)
        app.on_time_div_change()
        app._trigger_goto(+2)
        self.pump()
        t_center = app.t_offset.get() + app.t_div.get() * main.H_DIVS / 2
        self.assertAlmostEqual(t_center, rising[2], delta=2e-6)
        cfg.update(type="Width", polarity="Positive", condition="Width >",
                   width1=0.4e-3)
        app._refresh_trigger()
        self.assertEqual(len(app.trigger_events), 4)
        cfg.update(width1=0.6e-3)
        app._refresh_trigger()
        self.assertEqual(app.trigger_events, [])
        cfg["enabled"] = False
        app._refresh_trigger()
        app._trigger_window.destroy()
        app._trigger_window = None
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
