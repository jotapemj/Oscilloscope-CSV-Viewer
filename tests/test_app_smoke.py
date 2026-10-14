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


MESSAGEBOX_FNS = ("showinfo", "showwarning", "showerror", "askyesno",
                  "askyesnocancel", "askokcancel", "askquestion",
                  "askretrycancel")


def install_modal_guard(record):
    """wait_window y messagebox no bloquean: registran y vuelven (el
    diálogo queda como "cancelado"). Lanzar o destruir dentro de un
    callback de Tk acaba en segfault. tearDown convierte el registro en
    fallo. Devuelve los patchers para pararlos en tearDownClass."""
    def no_wait(widget, window=None):
        w = window or widget
        try:
            title = w.title()
        except Exception:
            title = str(w)
        record.append(f"wait_window: {title}")

    def no_box(name):
        def f(title=None, message=None, **kw):
            record.append(f"messagebox.{name}: {title}")
            return None
        return f

    patchers = [mock.patch("tkinter.Misc.wait_window", no_wait)]
    patchers += [mock.patch(f"tkinter.messagebox.{n}", no_box(n))
                 for n in MESSAGEBOX_FNS]
    for p in patchers:
        p.start()
    return patchers


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
        cls.modals = []
        cls.guards = install_modal_guard(cls.modals)
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
            for p in cls.guards:
                p.stop()
            cls.env.stop()
            cls.tmp.cleanup()

    def tearDown(self):
        # Cualquier modal no simulado por el test es un fallo
        opened, self.modals[:] = list(self.modals), []
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel) and w.grab_current() is not None:
                w.grab_release()
        self.assertEqual(opened, [], "diálogos modales inesperados")

    def pump(self):
        self.root.update_idletasks()
        self.root.update()

    def test_csv_loaded(self):
        names = [c["name"] for c in self.app.channels]
        self.assertEqual(len(self.app.channels), 2, names)
        self.assertEqual(len(self.app.time), 4000)

    def open_via_menu(self, path, mode="replace"):
        """load_csv como desde el botón: modo y fichero simulados, mocks
        activos durante toda la carga asíncrona."""
        with mock.patch.object(self.app, "_ask_load_mode",
                               return_value=mode), \
                mock.patch.object(self.app, "_prompt_save_if_dirty",
                                  return_value=True), \
                mock.patch("tkinter.filedialog.askopenfilename",
                           return_value=path):
            self.app.load_csv()
            wait_until(self.root, lambda: False, timeout=1.5)

    def new_toplevels(self, before):
        return [w for w in self.root.winfo_children()
                if w not in before and isinstance(w, tk.Toplevel)]

    def test_csv_paths(self):
        """Reemplazar, añadir, columna de tiempo dudosa y fichero basura."""
        app = self.app
        d = self.tmp.name
        # 1) Reemplazo por un CSV de 3 canales
        t = np.arange(2000) / 2e6
        p3 = os.path.join(d, "three.csv")
        with open(p3, "w", encoding="utf-8") as fh:
            fh.write("Time,A,B,C\n")
            for x in t:
                fh.write(f"{x:.9e},{np.sin(x * 1e4):.6f},1.0,{x * 100:.6f}\n")
        self.open_via_menu(p3)
        self.assertEqual([c["name"] for c in app.channels], ["A", "B", "C"])
        self.assertEqual(len(app.time), 2000)

        # 2) Añadir: otro muestreo, se interpola a la base existente
        n_before = len(app.channels)
        nt = np.arange(500) / 0.5e6
        app._append_channels(nt, [nt * 1e3], ["RAMP"], None)
        self.pump()
        self.assertEqual(len(app.channels), n_before + 1)
        ramp = app.channels[-1]["voltage"]
        self.assertEqual(len(ramp), len(app.time))
        np.testing.assert_allclose(ramp[100], app.time[100] * 1e3, rtol=1e-6)

        # 3) Tiempo en ns sin unidad (dt = 10 > 1 s): diálogo de tiempo
        pus = os.path.join(d, "nano.csv")
        with open(pus, "w", encoding="utf-8") as fh:
            fh.write("t_ns,V\n")
            for k in range(300):
                fh.write(f"{k * 10},{np.sin(k / 9):.5f}\n")
        before = set(self.root.winfo_children())
        self.open_via_menu(pus)
        new = self.new_toplevels(before)
        self.assertTrue(new, "no se abrió el diálogo de columna de tiempo")
        for w in new:
            w.destroy()

        # 4) Basura: diálogo de importación con el error
        pbad = os.path.join(d, "bad.csv")
        with open(pbad, "w", encoding="utf-8") as fh:
            fh.write("hello\nworld\n")
        before = set(self.root.winfo_children())
        self.open_via_menu(pbad)
        new = self.new_toplevels(before)
        self.assertTrue(new, "no se abrió el diálogo de importación")
        for w in new:
            w.destroy()

        # Restaurar el CSV de partida para el resto de tests
        self.open_via_menu(self.csv)
        self.assertTrue(wait_until(self.root,
                                   lambda: len(app.channels) == 2))
        app.auto_fit()
        self.pump()

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

    def test_math_channels(self):
        """M1 = CH1*2, M2 = M1+CH2; ciclo rechazado; borrado en cascada."""
        app = self.app
        app.open_math_window()
        self.pump()
        ch1 = app.channels[0]["voltage"].copy()
        ch2 = app.channels[1]["voltage"].copy()

        def create(expr, name):
            res = app._resolve_refs(main.math_parse(expr))
            return app._create_math_channel(expr, res, name)

        self.assertTrue(create("CH1*2", "M1"))
        m1 = len(app.channels) - 1
        self.assertTrue(create("M1+CH2", "M2"))
        m2 = len(app.channels) - 1
        np.testing.assert_allclose(app.channels[m1]["voltage"], 2 * ch1)
        np.testing.assert_allclose(app.channels[m2]["voltage"], 2 * ch1 + ch2)
        self.assertTrue(app._math_would_create_cycle(m1, {m2}))
        self.assertFalse(app._math_would_create_cycle(m2, {0, 1}))
        app.update_plot()
        self.pump()
        with mock.patch.object(app, "_ask_delete_cascade",
                               return_value=True) as ask:
            app._delete_channel(m1)
        ask.assert_called_once()
        self.assertEqual(len(app.channels), 2)
        np.testing.assert_allclose(app.channels[0]["voltage"], ch1)
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()
        self.pump()

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

    def load(self, path, keep_math=False):
        """load_session con diálogos simulados. La carga es asíncrona: los
        mocks siguen activos hasta que termina."""
        self.app.session_path = None        # para detectar fin de carga
        with mock.patch("tkinter.filedialog.askopenfilename",
                        return_value=path),                 mock.patch.object(self.app, "_prompt_save_if_dirty",
                                  return_value=True),                 mock.patch.object(self.app, "_ask_keep_math_on_new_csv",
                                  return_value=keep_math):
            self.app.load_session()
            self.assertTrue(wait_until(
                self.root, lambda: self.app.session_path == path
                and not self.app.session_dirty))
            wait_until(self.root, lambda: False, timeout=0.3)

    def test_session_roundtrip(self):
        """Guardar .ocv y recargar restaura canales, MATH, trigger,
        cursores, anotaciones y medidas; título sin asterisco."""
        app = self.app
        path = os.path.join(self.tmp.name, "s.ocv")
        app.channels[0]["v_div"] = 0.5
        app.channels[1]["unit"] = "A"
        app.channels[1]["name"] = "IOUT"
        res = app._resolve_refs(main.math_parse("CH1*2"))
        self.assertTrue(app._create_math_channel("CH1*2", res, "M1"))
        app.trigger_config.update(enabled=True, source=1, level=1.65)
        app.cursors_enabled.set(True)
        app.cursor_x = [1e-3, 2e-3]
        app.annotations.append({"type": "stroke", "color": "#ff0000",
                                "width": 2, "ch_idx": 0,
                                "points": [(1e-3, 0.2), (2e-3, 0.4)]})
        app.active_measurements.append(
            {"channel_idx": 1, "name": "Frequency", "channel_idx2": None})
        self.assertTrue(app._write_session_to(path))

        # Ensuciar todo y recargar
        app.channels[0]["v_div"] = 2.0
        app.channels[1]["unit"] = "V"
        app.trigger_config["enabled"] = False
        app.cursor_x = [0.0, 0.0]
        app.annotations.clear()
        app.active_measurements.clear()
        self.load(path)

        self.assertEqual(app.channels[0]["v_div"], 0.5)
        self.assertEqual(app.channels[1]["unit"], "A")
        self.assertEqual(app.channels[1]["name"], "IOUT")
        self.assertEqual([c["name"] for c in app.channels if "math" in c],
                         ["M1"])
        m1 = next(c for c in app.channels if "math" in c)
        np.testing.assert_allclose(m1["voltage"],
                                   2 * app.channels[0]["voltage"])
        self.assertTrue(app.trigger_config["enabled"])
        self.assertEqual(app.trigger_config["level"], 1.65)
        self.assertEqual(list(app.cursor_x), [1e-3, 2e-3])
        self.assertEqual([a["points"][0] for a in app.annotations],
                         [(1e-3, 0.2)] if isinstance(
                             app.annotations[0]["points"][0], tuple)
                         else [[1e-3, 0.2]])
        self.assertEqual([m["name"] for m in app.active_measurements],
                         ["Frequency"])
        self.assertEqual(self.root.title(), "s — Oscilloscope CSV Viewer")

        # Dejar el estado como lo esperan los demás tests
        m1_idx = app.channels.index(m1)
        app._delete_channel(m1_idx)
        app.trigger_config["enabled"] = False
        app.annotations.clear()
        app.active_measurements.clear()
        app._refresh_trigger()

    def test_dirty_title_and_prompt(self):
        """Asterisco en el título y las tres salidas del prompt."""
        app = self.app
        path = os.path.join(self.tmp.name, "d.ocv")
        self.assertTrue(app._write_session_to(path))
        self.assertFalse(self.root.title().startswith("*"))
        app._mark_dirty()
        self.assertTrue(self.root.title().startswith("*d"))
        for choice, expected in (("cancel", False), ("discard", True)):
            with mock.patch.object(app, "_ask_save_changes_dialog",
                                   return_value=choice):
                self.assertIs(app._prompt_save_if_dirty(), expected)
            self.assertTrue(app.session_dirty)
        os.remove(path)
        with mock.patch.object(app, "_ask_save_changes_dialog",
                               return_value="save"):
            self.assertTrue(app._prompt_save_if_dirty())
        self.assertTrue(os.path.exists(path))
        self.assertFalse(app.session_dirty)


if __name__ == "__main__":
    unittest.main()
