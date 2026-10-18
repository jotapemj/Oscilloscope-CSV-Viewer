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

from ocv.ui.app.app import OscilloscopeApp
from ocv.core.math_expr import math_parse
from ocv.core.scope import H_DIVS, TIME_DIV_VALUES
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
        cls.app = OscilloscopeApp(cls.root)
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
        app.annotations.append({"type": "text", "pos": (1e-4, 0.5),
                                "content": "OLD", "color": "#ffff00",
                                "ch_idx": 0})
        app._push_undo_annotation("add", [(0, app.annotations[0])])
        self.open_via_menu(p3)
        self.assertEqual([c["name"] for c in app.channels], ["A", "B", "C"])
        # Bug #16: las anotaciones del CSV anterior no se arrastran
        self.assertEqual(app.annotations, [])
        self.assertEqual(app._undo_stack, [])
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

    def test_fft_peak_detect(self):
        """Bug #22: CSV peak detect (cada instante dos veces) → la FFT usa
        el fs real, no el doble (antes dt = media de diffs con ceros)."""
        app = self.app
        fs = 1e6
        t = np.repeat(np.arange(4000) / fs, 2)
        v = np.sin(2 * np.pi * 10e3 * t) + np.tile([-0.05, 0.05], 4000)
        app._load_channels(t, [v], ["PK"])
        self.pump()
        before = set(self.root.winfo_children())
        try:
            app.open_fft_window()
            self.pump()
            self.assertAlmostEqual(app._fft_instance.fs / fs, 1.0, places=6)
        finally:
            for w in self.new_toplevels(before):
                w.destroy()
            self.open_via_menu(self.csv)
            self.assertTrue(wait_until(self.root,
                                       lambda: len(app.channels) == 2))
            app.auto_fit()
            self.pump()

    def test_phosphor_peak_detect(self):
        """Bug #22: en peak detect los segmentos con dt = 0 no se comen
        el percentil: el resto conserva la graduación de intensidad."""
        t = np.repeat(np.arange(2000) / 1e6, 2)
        y = np.sin(2 * np.pi * 2e3 * t) + np.tile([-0.02, 0.02], 2000)
        _, rgba = self.app._compute_phosphor_rgba(t, y, "#ffff00", 1.0, 1.0)
        alpha = rgba[:, 3][np.diff(t) > 0]
        self.assertTrue(np.all(np.isfinite(rgba[:, 3])))
        self.assertLess(alpha.min(), 0.6, "sin graduación")
        self.assertGreater(alpha.max(), 0.9)

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

    def find_label(self, text):
        stack = [self.root]
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Label) and w.cget("text") == text:
                return w
            stack.extend(w.winfo_children())
        return None

    def test_topbar_narrow_window(self):
        """Bug #20: con ventana estrecha About y Settings no se aplastan."""
        geom = self.root.geometry()
        try:
            self.root.geometry("900x700")
            wait_until(self.root, lambda: self.root.winfo_width() == 900,
                       timeout=2)
            self.pump()
            for text in ("ⓘ  About", "Settings"):
                with self.subTest(text=text):
                    lab = self.find_label(text)
                    self.assertIsNotNone(lab)
                    self.assertGreaterEqual(lab.winfo_width(),
                                            lab.winfo_reqwidth())
        finally:
            self.root.geometry(geom)
            self.pump()

    def test_topbar_min_width(self):
        """Bug #29: ventana estrecha recortaba la barra superior. El ancho
        mínimo de la ventana deja ver la barra entera."""
        bar = self.app.topbar
        geom = self.root.geometry()
        try:
            self.root.geometry("400x700")
            self.pump()
            self.root.update()
            for w in bar.winfo_children():
                with self.subTest(widget=str(w)):
                    self.assertGreaterEqual(w.winfo_width(),
                                            w.winfo_reqwidth())
        finally:
            self.root.geometry(geom)
            self.pump()

    def low_contrast_texts(self, min_ratio=4.5):
        """Textos tk (Label/Button) y ttk (casillas, radios, etiquetas)
        con fg/bg por debajo de min_ratio."""
        from tkinter import ttk
        st = ttk.Style(self.root)
        bad, stack = [], [self.root]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if getattr(w, "_theme_protected", False):
                continue
            cls = w.winfo_class()
            try:
                if cls in ("Label", "Button", "Checkbutton"):
                    text, fg, bg = w.cget("text"), w.cget("fg"), w.cget("bg")
                elif cls in ("TCheckbutton", "TRadiobutton", "TLabel"):
                    style = str(w.cget("style")) or cls
                    text = w.cget("text")
                    fg = str(st.lookup(style, "foreground"))
                    bg = str(st.lookup(style, "background"))
                else:
                    continue
            except tk.TclError:
                continue
            # "■" = muestra de color del canal, no texto
            if str(text).strip() in ("", "■") or not fg.startswith("#") \
                    or not bg.startswith("#"):
                continue
            if theme.contrast_ratio(fg, bg) < min_ratio:
                bad.append(f"{str(text)[:30]!r} {fg} on {bg}")
        return bad

    def test_theme_switch_contrast(self):
        """Bug #21: con Settings abierta, ningún cambio de tema deja texto
        ilegible (antes: negro sobre negro al pasar a High Contrast)."""
        app = self.app
        before = set(self.root.winfo_children())
        app._open_grid_view_window()
        app.open_eye_window()          # UI #3: métricas en amarillo fijo
        app.open_fft_window()
        self.pump()
        try:
            names = list(theme.THEMES)
            for a in names:
                for b in names:
                    if a == b:
                        continue
                    app._apply_theme_immediate(a, sync_plot=False)
                    app._apply_theme_immediate(b, sync_plot=False)
                    with self.subTest(frm=a, to=b):
                        self.assertEqual(self.low_contrast_texts(), [])
        finally:
            for w in self.new_toplevels(before):
                w.destroy()
            app._appearance_swatch_refresh = None
            app._apply_theme_immediate("Dark", sync_plot=True)
            self.pump()

    def test_channel_colors_readable(self):
        """UI #1: en Light los colores de canal se oscurecen al mostrarlos
        (ticks Y del canal activo, nombres del panel); el color guardado
        no cambia."""
        app = self.app
        stored = [c["color"] for c in app.channels]
        try:
            for name in theme.THEMES:
                app._apply_theme_immediate(name, sync_plot=True)
                app.update_plot()
                self.pump()
                bg = app.grid_theme["bg"]
                with self.subTest(theme=name):
                    for lbl in app.ax.get_yticklabels():
                        if lbl.get_text():
                            from matplotlib.colors import to_hex
                            self.assertGreaterEqual(theme.contrast_ratio(
                                to_hex(lbl.get_color()),
                                theme._NAMED.get(bg, bg)), 4.5)
                    self.assertEqual(self.low_contrast_texts(), [])
            self.assertEqual([c["color"] for c in app.channels], stored)
        finally:
            app._apply_theme_immediate("Dark", sync_plot=True)
            self.pump()

    def test_render_toggle_contrast(self):
        """Bug #21 / UI #4: toggle Detail/Fast legible en reposo, hover y
        pulsado en todos los temas; ahora es un ttk.Button con estilo."""
        from tkinter import ttk
        app = self.app
        b = app._render_mode_btn
        st = ttk.Style(self.root)
        mode = app.render_mode.get()
        try:
            for name in theme.THEMES:
                app._apply_theme_immediate(name, sync_plot=False)
                for m in ("Fast", "Detail"):
                    app.render_mode.set(m)
                    app._update_render_mode_btn_visual()
                    style = str(b.cget("style"))
                    for state in ([], ["active"], ["pressed", "active"]):
                        with self.subTest(theme=name, mode=m, state=state):
                            bg = st.lookup(style, "background", state)
                            fg = st.lookup(style, "foreground", state)
                            self.assertGreaterEqual(
                                theme.contrast_ratio(fg, bg), 4.5,
                                f"{style} {fg} on {bg}")
                    if m == "Fast":
                        self.assertEqual(st.lookup(style, "background"),
                                         theme.COLOR_ACCENT)
        finally:
            app._apply_theme_immediate("Dark", sync_plot=True)
            app.render_mode.set(mode)
            app._update_render_mode_btn_visual()

    def test_tool_buttons_look_like_buttons(self):
        """UI #4: botones TOOLS como ttk 'Tool.TButton' con borde visible
        (>= 3:1 contra la barra) en todos los temas."""
        from tkinter import ttk
        app = self.app
        fft = self.find_widget(ttk.Button, "FFT")
        self.assertIsNotNone(fft)
        self.assertEqual(str(fft.cget("style")), "Tool.TButton")
        st = ttk.Style(self.root)
        try:
            for name in theme.THEMES:
                app._apply_theme_immediate(name, sync_plot=False)
                with self.subTest(theme=name):
                    border = st.lookup("Tool.TButton", "bordercolor")
                    self.assertGreaterEqual(
                        theme.contrast_ratio(border, theme.COLOR_BG_ALT), 3.0)
        finally:
            app._apply_theme_immediate("Dark", sync_plot=True)

    def find_widget(self, cls, text):
        stack = [self.root]
        while stack:
            w = stack.pop()
            if isinstance(w, cls) and str(w.cget("text")) == text:
                return w
            stack.extend(w.winfo_children())
        return None

    def test_session_next_to_open_csv(self):
        """Mejora 3: Session en el panel de Open CSV; a 1500 px la barra
        superior cabe entera (antes se cortaba 'Show on all channels')."""
        from tkinter import ttk
        app = self.app
        open_btn = self.find_widget(ttk.Button, "📂  Open CSV")
        self.assertIs(app._session_btn.master, open_btn.master)
        geom = self.root.geometry()
        try:
            self.root.geometry("1500x900")
            wait_until(self.root, lambda: self.root.winfo_width() == 1500,
                       timeout=2)
            self.pump()
            chk = self.find_widget(ttk.Checkbutton, "Show on all channels")
            self.assertGreaterEqual(chk.winfo_width(), chk.winfo_reqwidth())
        finally:
            self.root.geometry(geom)
            self.pump()

    def test_channel_checkboxes_are_ttk(self):
        """UI #5: casillas de canal con el mismo dibujo que el resto (ttk)
        y su color de canal; clic simple sigue alternando visible."""
        from tkinter import ttk
        app = self.app
        app._rebuild_channels_panel()
        self.pump()
        name = app.channels[0]["name"]
        cb = self.find_widget(ttk.Checkbutton, name)
        self.assertIsNotNone(cb, "casilla de canal no es ttk")
        vis = app.channels[0]["visible"].get()
        cb.invoke()
        self.assertNotEqual(app.channels[0]["visible"].get(), vis)
        cb.invoke()
        self.assertEqual(app.channels[0]["visible"].get(), vis)

    def buttons_x(self, top, texts):
        """Posición x en pantalla de los ttk.Button de `top` por texto."""
        from tkinter import ttk
        out, stack = {}, [top]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, ttk.Button) and str(w.cget("text")) in texts:
                out[str(w.cget("text"))] = w.winfo_rootx()
        return [out.get(t) for t in texts]

    def test_primary_button_first(self):
        """UI #6: acción principal a la izquierda, como el resto de
        diálogos (Create | Cancel). Medidas y guardar-sesión iban al revés."""
        app = self.app
        before = set(self.root.winfo_children())
        app.open_meas_window()
        self.pump()
        top = self.new_toplevels(before)[0]
        try:
            add, close = self.buttons_x(top, ["Add", "Close"])
            self.assertLess(add, close)
        finally:
            top.destroy()
        before = set(self.root.winfo_children())
        app._ask_save_changes_dialog("closing")   # wait_window simulado
        self.modals.clear()
        top = self.new_toplevels(before)[0]
        try:
            self.pump()
            xs = self.buttons_x(top, ["Save", "Don't save", "Cancel"])
            self.assertEqual(xs, sorted(xs))
        finally:
            top.destroy()

    def test_settings_layout(self):
        """UI #7: en Settings ninguna opción se corta y el tema activo se
        marca con '✓' en su botón."""
        from tkinter import ttk
        app = self.app
        before = set(self.root.winfo_children())
        app._open_grid_view_window()
        self.pump()
        top = self.new_toplevels(before)[0]
        try:
            stack = [top]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if isinstance(w, ttk.Radiobutton):
                    with self.subTest(text=str(w.cget("text"))[:20]):
                        self.assertLessEqual(
                            w.winfo_rootx() + w.winfo_reqwidth(),
                            top.winfo_rootx() + top.winfo_width())
            tile = self.find_widget(tk.Button, "✓ Dark")
            self.assertIsNotNone(tile, "tema activo sin marcar")
            self.assertIsNone(self.find_widget(tk.Button, "✓ Light"))
            # Cambio con fundido (asíncrono): la marca sigue al tema
            app.apply_theme_runtime("Light", sync_plot=True, fade=True)
            self.assertTrue(wait_until(
                self.root, lambda: self.find_widget(tk.Button, "✓ Light")
                is not None, timeout=5))
            self.assertIsNone(self.find_widget(tk.Button, "✓ Dark"))
        finally:
            top.destroy()
            app._appearance_swatch_refresh = None
            app._apply_theme_immediate("Dark", sync_plot=True)
            self.pump()

    def test_plot_uses_available_space(self):
        """UI #9: el plot ocupa el alto/ancho disponibles (antes márgenes
        por defecto de matplotlib: ~12 % vacío arriba y 10 % a la
        derecha) y las etiquetas no se salen de la figura."""
        app = self.app
        app.update_plot()
        self.pump()
        app.canvas.draw()
        pos = app.ax.get_position()
        self.assertGreaterEqual(pos.y1, 0.95)
        self.assertGreaterEqual(pos.x1, 0.96)
        r = app.canvas.get_renderer()
        fig_box = app.fig.bbox
        for art in (app.ax.get_xticklabels() + app.ax.get_yticklabels()
                    + app._gnd_marker_artists):
            if art.get_visible() and art.get_text():
                bb = art.get_window_extent(r)
                with self.subTest(text=art.get_text()):
                    self.assertGreaterEqual(bb.x0, fig_box.x0 - 1)
                    self.assertGreaterEqual(bb.y0, fig_box.y0 - 1)

    def test_trigger_count_label_states(self):
        """UI #10: trigger desactivado = gris 'Trigger off', no rojo de
        error. Activado sin eventos sí va en rojo."""
        app = self.app
        cfg = app.trigger_config
        saved = dict(cfg)
        app.open_trigger_window()
        self.pump()
        try:
            lbl = app._trigger_window._count_label
            cfg["enabled"] = False
            app._refresh_trigger()
            self.assertEqual(lbl.cget("text"), "Trigger off")
            self.assertEqual(lbl.cget("fg"), theme.COLOR_TEXT_DIM)
            cfg.update(enabled=True, type="Edge", source=0, level=1e6)
            app._refresh_trigger()
            self.assertEqual(lbl.cget("fg"), theme.COLOR_DANGER)
        finally:
            cfg.clear()
            cfg.update(saved)
            app._refresh_trigger()
            app._trigger_window.destroy()
            app._trigger_window = None

    def test_cursor_lines_visible(self):
        """Bug #26: líneas de cursor en blanco fijo → invisibles en Light.
        Deben llegar a 3:1 contra el fondo del plot en todos los temas."""
        from matplotlib.colors import to_hex
        app = self.app
        mode, en = app.cursor_mode.get(), app.cursors_enabled.get()
        try:
            app.cursors_enabled.set(True)
            app.cursor_mode.set("Horizontal + Vertical")
            app.on_cursors_toggle()
            for name in theme.THEMES:
                app._apply_theme_immediate(name, sync_plot=True)
                app.update_plot()
                self.pump()
                bg = app.grid_theme["bg"]
                lines = [l for l in app._cursor_v_lines + app._cursor_h_lines
                         if l is not None]
                self.assertTrue(lines)
                for l in lines:
                    with self.subTest(theme=name):
                        self.assertGreaterEqual(theme.contrast_ratio(
                            to_hex(l.get_color()), theme._NAMED.get(bg, bg)),
                            3.0)
        finally:
            app._apply_theme_immediate("Dark", sync_plot=True)
            app.cursor_mode.set(mode)
            app.cursors_enabled.set(en)
            app.on_cursors_toggle()
            self.pump()

    def test_trigger_markers_visible(self):
        """Bug #27: marcadores de trigger #ffeb3b fijo → amarillo sobre
        blanco en Light. Deben llegar a 3:1 contra el fondo del plot."""
        from matplotlib.colors import to_hex
        app = self.app
        cfg = app.trigger_config
        saved = dict(cfg)
        try:
            cfg.update(enabled=True, source=1, type="Edge", slope="Rising",
                       level=1.65, show_markers=True)
            app.auto_fit()
            for name in theme.THEMES:
                app._apply_theme_immediate(name, sync_plot=True)
                app._refresh_trigger()
                app.update_plot()
                self.pump()
                bg = app.grid_theme["bg"]
                self.assertTrue(app._trigger_marker_artists)
                for a in app._trigger_marker_artists:
                    with self.subTest(theme=name, artist=type(a).__name__):
                        self.assertGreaterEqual(theme.contrast_ratio(
                            to_hex(a.get_color()), theme._NAMED.get(bg, bg)),
                            3.0)
        finally:
            app._apply_theme_immediate("Dark", sync_plot=True)
            cfg.clear()
            cfg.update(saved)
            app._refresh_trigger()
            self.pump()

    def test_export_gnd_labels_do_not_overlap(self):
        """Bug #28: en la imagen exportada, canales con el mismo 0 V
        tapaban sus etiquetas de GND (en pantalla ya se apilaban)."""
        app = self.app
        a, b = app.channels[0], app.channels[1]
        saved = (b["v_offset"], b["v_div"])
        try:
            b["v_offset"], b["v_div"] = a["v_offset"], a["v_div"]
            fig = app._build_export_figure(False, False, include_annot=False)
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            cv = FigureCanvasAgg(fig)
            cv.draw()
            r = cv.get_renderer()
            boxes = [t.get_window_extent(r) for t in fig.axes[0].texts
                     if t.get_text().startswith("▶")]
            self.assertEqual(len(boxes), 2)
            self.assertFalse(boxes[0].overlaps(boxes[1]),
                             f"{boxes[0]} / {boxes[1]}")
        finally:
            b["v_offset"], b["v_div"] = saved

    def test_gnd_labels_do_not_overlap(self):
        """UI #2: dos canales con el 0 V a la misma altura: sus etiquetas
        de GND se apilan en vez de taparse ('▶ \\▶ M1')."""
        app = self.app
        a, b = app.channels[0], app.channels[1]
        saved = (b["v_offset"], b["v_div"])
        try:
            b["v_offset"], b["v_div"] = a["v_offset"], a["v_div"]
            app.update_plot()
            self.pump()
            app.canvas.draw()
            r = app.canvas.get_renderer()
            boxes = [ann.get_window_extent(r) for ann in app._gnd_marker_artists]
            self.assertEqual(len(boxes), 2)
            self.assertFalse(boxes[0].overlaps(boxes[1]),
                             f"{boxes[0]} / {boxes[1]}")
        finally:
            b["v_offset"], b["v_div"] = saved
            app.update_plot()
            self.pump()

    def test_about_version(self):
        """Bug #24: About mostraba 'Version 1.24' fijo a mano."""
        import ocv
        before = set(self.root.winfo_children())
        self.app._open_about_window()
        self.pump()
        try:
            lab = self.find_label(f"Version {ocv.__version__}")
            self.assertIsNotNone(lab)
        finally:
            for w in self.new_toplevels(before):
                w.destroy()

    def test_export_annotations_on_screen_scale(self):
        """Bug #15: anotaciones exportadas en y_screen, no en voltios
        reales; una fuera de rango no estira la imagen."""
        import io
        app = self.app
        ch = app.channels[0]
        saved = (ch["v_offset"], app.active_channel_idx, list(app.annotations))
        try:
            app.set_active_channel(0)
            ch["v_offset"] = 10.0
            v_div = app.v_div.get()
            app.annotations[:] = [
                {"type": "text", "pos": (1e-4, 10.0 + 2 * ch["v_div"]),
                 "content": "IN", "color": "#ffff00", "ch_idx": 0},
                {"type": "text", "pos": (1e-4, 10.0 + 500 * ch["v_div"]),
                 "content": "FAR", "color": "#ffff00", "ch_idx": 0},
                {"type": "stroke", "color": "#ff0000", "width": 2,
                 "ch_idx": 0, "points": [(1e-4, 10.0), (2e-4, 10.0)]},
            ]
            fig = app._build_export_figure(False, False, include_annot=True)
            ax = fig.axes[0]
            txt = {t.get_text(): t for t in ax.texts}
            self.assertAlmostEqual(txt["IN"].get_position()[1], 2 * v_div)
            stroke = ax.lines[-1]
            np.testing.assert_allclose(stroke.get_ydata(), [0.0, 0.0])
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=50, bbox_inches="tight")
            w, h = struct.unpack(">II", buf.getvalue()[16:24])
            self.assertLess(h, w, f"imagen estirada: {w}x{h}")
        finally:
            ch["v_offset"], idx, app.annotations[:] = saved
            app.set_active_channel(idx)

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
            res = app._resolve_refs(math_parse(expr))
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
        t_center = app.t_offset.get() + app.t_div.get() * H_DIVS / 2
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

    def load(self, path):
        """load_session con diálogos simulados. La carga es asíncrona: los
        mocks siguen activos hasta que termina. Devuelve el mock de la
        pregunta "¿mantener MATH?" (responde Keep, el peor caso)."""
        self.app.session_path = None        # para detectar fin de carga
        with mock.patch("tkinter.filedialog.askopenfilename",
                        return_value=path), \
                mock.patch.object(self.app, "_prompt_save_if_dirty",
                                  return_value=True), \
                mock.patch.object(self.app, "_ask_keep_math_on_new_csv",
                                  return_value=True) as ask_keep:
            self.app.load_session()
            self.assertTrue(wait_until(
                self.root, lambda: self.app.session_path == path
                and not self.app.session_dirty))
            wait_until(self.root, lambda: False, timeout=0.3)
        return ask_keep

    def test_dirty_clears_when_reverted(self):
        """Mejora 2: volver al estado guardado quita el asterisco; un
        cambio real lo mantiene. Un CSV nuevo (Untitled) sigue dirty."""
        app = self.app
        path = os.path.join(self.tmp.name, "dirty.ocv")
        self.assertTrue(app._write_session_to(path))
        self.assertFalse(app.session_dirty)
        vd = app.v_div.get()
        values = [v for v in (0.1, 0.2, 0.5, 1.0, 2.0) if abs(v - vd) > 1e-12]
        app.v_div.set(values[0])
        self.assertTrue(app.session_dirty)
        app.v_div.set(vd)                      # vuelta al valor guardado
        self.assertTrue(wait_until(self.root, lambda: not app.session_dirty,
                                   timeout=2))
        self.assertFalse(self.root.title().startswith("*"))
        app.v_div.set(values[1])               # cambio real: sigue dirty
        wait_until(self.root, lambda: False, timeout=0.5)
        self.assertTrue(app.session_dirty)
        app.v_div.set(vd)
        wait_until(self.root, lambda: not app.session_dirty, timeout=2)
        # CSV nuevo: Untitled dirty, sin estado limpio al que volver
        self.open_via_menu(self.csv)
        self.assertTrue(wait_until(self.root,
                                   lambda: len(app.channels) == 2))
        wait_until(self.root, lambda: False, timeout=0.5)
        self.assertTrue(app.session_dirty)
        self.assertIsNone(app.session_path)
        app.auto_fit()
        self.pump()

    def test_session_roundtrip(self):
        """Guardar .ocv y recargar restaura canales, MATH, trigger,
        cursores, anotaciones y medidas; título sin asterisco."""
        app = self.app
        path = os.path.join(self.tmp.name, "s.ocv")
        app.channels[0]["v_div"] = 0.5
        app.channels[1]["unit"] = "A"
        app.channels[1]["name"] = "IOUT"
        res = app._resolve_refs(math_parse("CH1*2"))
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
        ask_keep = self.load(path)

        # Bug #12: la sesión manda; ni pregunta ni MATH duplicados
        ask_keep.assert_not_called()
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

    def test_dialogs(self):
        """About, Settings y leyenda de atajos se abren; toast no rompe."""
        app = self.app
        for opener in ("_open_about_window", "_open_grid_view_window",
                       "_open_commands_legend_dialog"):
            with self.subTest(dialog=opener):
                before = set(self.root.winfo_children())
                getattr(app, opener)()
                self.pump()
                new = self.new_toplevels(before)
                self.assertTrue(new, f"{opener} no abrió ventana")
                for w in new:
                    w.destroy()
        app._show_toast("hello", duration_ms=50)
        wait_until(self.root, lambda: False, timeout=0.5)

    def test_meas_panel_auto_hide(self):
        """Panel de medidas oculto sin medidas; aparece con la primera y
        se oculta al quitar la última."""
        app = self.app
        panel = app.meas_panel
        app.active_measurements.clear()
        app.update_plot()
        self.pump()
        self.assertEqual(panel.winfo_manager(), "")
        app.active_measurements.append(
            {"channel_idx": 0, "name": "Peak-Peak", "channel_idx2": None})
        app.update_plot()
        self.pump()
        self.assertEqual(panel.winfo_manager(), "pack")
        self.assertTrue(panel.winfo_ismapped())
        app._remove_measurement(0)
        self.pump()
        self.assertEqual(panel.winfo_manager(), "")

    def test_measurements_and_cursors(self):
        """Medidas reales vía la app y lectura de cursores."""
        app = self.app
        app.auto_fit()
        self.pump()
        app.active_measurements[:] = [
            {"channel_idx": 1, "name": "Frequency", "channel_idx2": None},
            {"channel_idx": 0, "name": "Peak-Peak", "channel_idx2": None},
        ]
        app.open_meas_window()
        app._rebuild_measurements_panel()
        app.update_plot()
        self.pump()
        res = {r["name"]: r for r in app.compute_measurements()}
        self.assertAlmostEqual(res["Frequency"]["value"], 1e3, delta=1.0)
        self.assertEqual(res["Frequency"]["unit"], "Hz")
        self.assertAlmostEqual(res["Peak-Peak"]["value"], 2.0, delta=0.01)
        app._remove_measurement(0)
        self.assertEqual([m["name"] for m in app.active_measurements],
                         ["Peak-Peak"])
        app.active_measurements.clear()
        app._rebuild_measurements_panel()

        app.cursors_enabled.set(True)
        app.on_cursors_toggle()
        app.center_cursors()
        self.pump()
        t1, t2 = app.cursor_x
        window = app.t_div.get() * H_DIVS
        self.assertAlmostEqual(t2 - t1, window / 2, delta=window * 1e-9)
        app.update_cursors_info_label()
        self.assertIn("Δt:", app.cursors_info_label.get_text())
        app.cursors_enabled.set(False)
        app.on_cursors_toggle()
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()
        self.pump()

    def test_scales(self):
        """Pivote V/div sobre GND, tick 0 V limpio, "1." tecleado, t/div
        al vecino, snap de GND a la rejilla."""
        app = self.app
        app.set_active_channel(0)
        app.v_div.set(0.5)
        app.on_v_div_change()
        app.v_offset.set(-0.5)                 # GND 1 div por encima
        app.on_v_div_change()
        gnd_divs = -app.v_offset.get() / app.v_div.get()
        app._step_v_div("up")                  # 0.5 → 1
        self.pump()
        self.assertAlmostEqual(app.v_div.get(), 1.0)
        self.assertAlmostEqual(-app.v_offset.get() / app.v_div.get(),
                               gnd_divs, places=9)
        app.update_plot()
        self.pump()
        labels = [t.get_text() for t in app.ax.get_yticklabels()]
        self.assertIn("0 V", labels, labels)
        self.assertFalse([x for x in labels if "e-" in x], labels)

        # Secuencia que deja residuo float (-1.8e-15 V) sin el snap a cero
        app.v_div.set(0.1)
        app.on_v_div_change()
        app.v_offset.set(-3 * 0.1)             # -0.30000000000000004, como
        app.on_v_div_change()                  # sale de sliders/snap reales
        while app.v_div.get() < 10:
            app._step_v_div("up")              # redibujo completo
            self.pump()
            for path in ("full", "fast"):
                labels = [t.get_text() for t in app.ax.get_yticklabels()]
                self.assertFalse([x for x in labels if "e-" in x],
                                 f"{path} V/div {app.v_div.get()}: {labels}")
                app.update_plot()              # siguiente: camino rápido
                self.pump()

        app.v_div_display.set("2.")            # fix #4 por la UI
        app._on_v_div_typed()
        self.assertAlmostEqual(app.v_div.get(), 2.0)

        before = app.t_div.get()
        app._step_t_div("down")
        self.assertLess(app.t_div.get(), before)
        self.assertIn(app.t_div.get(), TIME_DIV_VALUES)

        app.v_offset.set(-0.37 * app.v_div.get())
        app.on_v_div_change()
        app._snap_channel_gnd_to_grid(0)
        ch = app.channels[0]
        divs = -ch["v_offset"] / ch["v_div"]
        self.assertAlmostEqual(divs, round(divs), places=9)

        app.center_trace()
        v = ch["voltage"]
        self.assertAlmostEqual(ch["v_offset"], (v.max() + v.min()) / 2)
        app.auto_fit()
        self.pump()

    def key(self, seq, widget=None):
        w = widget or self.app.canvas.get_tk_widget()
        w.focus_force()
        self.pump()
        w.event_generate(seq, when="now")
        self.pump()

    def test_hotkeys(self):
        """Atajos globales por event_generate: cableado + guard de Entry."""
        app = self.app
        app.auto_fit()
        app.t_div.set(app.t_total / 40)
        app.on_time_div_change()
        self.key("<Key-2>")
        self.assertEqual(app.active_channel_idx, 1)
        entry = next(w for w in self.all_widgets()
                     if w.winfo_class() in ("TEntry", "TSpinbox", "Entry")
                     and w.winfo_ismapped())
        self.key("<Key-1>", widget=entry)      # escribiendo: ignorar
        self.assertEqual(app.active_channel_idx, 1)
        self.key("<Key-1>")
        self.assertEqual(app.active_channel_idx, 0)

        off, vd = app.v_offset.get(), app.v_div.get()
        self.key("<Up>")                       # traza sube 0.1 div
        self.assertAlmostEqual(app.v_offset.get(), off - 0.1 * vd)
        self.key("<Down>")
        self.assertAlmostEqual(app.v_offset.get(), off)
        self.key("<Shift-Up>")
        self.assertNotEqual(app.v_div.get(), vd)
        self.key("<Shift-Down>")
        self.assertAlmostEqual(app.v_div.get(), vd)
        td, to = app.t_div.get(), app.t_offset.get()
        self.key("<Right>")
        self.assertGreater(app.t_offset.get(), to)
        self.key("<Control-Shift-Up>")
        self.assertNotEqual(app.t_div.get(), td)
        # Bug #17: alejar con el atajo se para al ver toda la señal
        for _ in range(40):
            self.key("<Control-Shift-Down>")
        self.assertLessEqual(app.t_div.get() * H_DIVS,
                             app.t_total * (1 + 1e-9))
        app.auto_fit()
        self.pump()

        app.cursors_enabled.set(False)
        app.on_cursors_toggle()
        self.key("<Control-Left>")             # mueve X1 y activa cursores
        self.assertTrue(app.cursors_enabled.get())
        state = app.cursors_enabled.get()
        self.key("<Key-c>")
        self.assertNotEqual(app.cursors_enabled.get(), state)
        vis = app.channels[1]["visible"].get()
        self.key("<Control-Key-2>")
        self.assertNotEqual(app.channels[1]["visible"].get(), vis)
        self.key("<Control-Key-2>")
        self.key("<Key-g>")
        self.key("<Key-0>")
        app.auto_fit()
        self.pump()

    def all_widgets(self, w=None):
        w = w or self.root
        out = [w]
        for c in w.winfo_children():
            out += self.all_widgets(c)
        return out

    def test_mouse_select_and_drag(self):
        """Clic sobre la traza de CH2 la activa; arrastrar mueve la vista."""
        app = self.app
        app.auto_fit()
        app.set_active_channel(0)
        self.pump()
        ch = app.channels[1]
        k = int(np.argmax(ch["voltage"] > 3.0)) + 50    # en el nivel alto
        x = app.time[k] - app.t_offset.get()
        y = (ch["voltage"][k] - ch["v_offset"]) / ch["v_div"] * app.v_div.get()
        app.on_mouse_press(self.mpl_event(x, y))
        app.on_mouse_release(self.mpl_event(x, y))
        self.assertEqual(app.active_channel_idx, 1)
        app.t_div.set(app.t_total / 40)
        app.on_time_div_change()
        before = (app.t_offset.get(), app.v_offset.get())
        app.on_mouse_press(self.mpl_event(x, y + 3 * app.v_div.get()))
        app.on_mouse_move(self.mpl_event(x - 2 * app.t_div.get(), y))
        app.on_mouse_release(self.mpl_event(x - 2 * app.t_div.get(), y))
        self.assertNotEqual((app.t_offset.get(), app.v_offset.get()), before)
        app.set_active_channel(0)
        app.auto_fit()
        self.pump()

    def find(self, root, cls_names, text=None):
        for w in self.all_widgets(root):
            if w.winfo_class() not in cls_names:
                continue
            if text is None or str(w.cget("text")) == text:
                return w
        raise AssertionError(f"no {cls_names} {text!r} en {root}")

    def test_channel_panel_dialogs(self):
        """Unidad (A en el eje), renombrar, color, modo render, menú."""
        app = self.app
        app.set_active_channel(0)
        self.pump()
        rows_before = len(app.channels_frame.winfo_children())
        self.assertGreater(rows_before, 0)

        before = set(self.root.winfo_children())
        app._open_unit_picker_dialog(0)
        self.pump()
        (dlg,) = self.new_toplevels(before)
        self.find(dlg, ("Label",), "A").event_generate("<Button-1>")
        self.find(dlg, ("TButton",), "OK").invoke()
        self.pump()
        self.assertEqual(app.channels[0]["unit"], "A")
        app.update_plot()
        self.pump()
        labels = [t.get_text() for t in app.ax.get_yticklabels() if t.get_text()]
        self.assertTrue(all(x.endswith("A") for x in labels), labels)
        app.channels[0]["unit"] = "V"
        app._last_structure_fingerprint = None
        app._update_v_div_display()

        old = app.channels[0]["name"]
        before = set(self.root.winfo_children())
        app._open_rename_dialog(0)
        self.pump()
        (dlg,) = self.new_toplevels(before)
        entry = self.find(dlg, ("TEntry",))
        entry.delete(0, "end")
        entry.insert(0, "VIN")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(app.channels[0]["name"], "VIN")
        app.channels[0]["name"] = old
        app._rebuild_channels_panel()

        before = set(self.root.winfo_children())
        app._open_color_picker(0)
        self.pump()
        for w in self.new_toplevels(before):
            w.destroy()

        mode = app.render_mode.get()
        app._toggle_render_mode()
        self.assertNotEqual(app.render_mode.get(), mode)
        if app.render_mode.get() == "Fast":
            self.assertEqual(str(app._render_mode_btn.cget("style")),
                             "ToolOn.TButton")
        app._toggle_render_mode()
        self.assertEqual(app.render_mode.get(), mode)

        with mock.patch("tkinter.Menu.tk_popup") as popup:
            app._open_session_menu()
        popup.assert_called_once()
        self.pump()

    def test_phosphor_and_protocol_overlay(self):
        """Fósforo: LineCollection con tope de segmentos en ambos caminos;
        al apagar vuelve a Line2D. Overlay de tramas de protocolo."""
        app = self.app
        app.auto_fit()
        app.phosphor_enabled.set(True)         # su trace ya redibuja
        self.pump()
        app._last_structure_fingerprint = None  # forzar camino completo
        app.update_plot()
        self.pump()

        def collections():
            return [a for a in app._channel_lines.values()
                    if hasattr(a, "set_segments")]

        # minmax_decimate(cap // 2 buckets) da hasta 4 puntos por bucket:
        # el tope real es 2 * cap (típico ~cap). Sin tope: un segmento por
        # muestra visible.
        cap = 2 * app._PHOSPHOR_MAX_SEGMENTS
        lcs = collections()
        self.assertEqual(len(lcs), len(app.channels))
        self.assertTrue(all(len(lc.get_segments()) <= cap for lc in lcs),
                        [len(lc.get_segments()) for lc in lcs])
        app.t_offset.set(app.t_offset.get() + 1e-9)
        app.update_plot()                      # mismo fingerprint: rápido
        self.pump()
        lcs = collections()
        self.assertTrue(lcs)
        self.assertTrue(all(len(lc.get_segments()) <= cap for lc in lcs))
        app.phosphor_enabled.set(False)
        app.update_plot()
        self.pump()
        self.assertFalse(collections())
        self.assertTrue(all(hasattr(a, "set_data")
                            for a in app._channel_lines.values()))

        frames = [{"t_start": 1e-4, "t_end": 2e-4, "type": "data",
                   "value": 0x41, "label": "0x41 'A'", "color": "#5fa8ff",
                   "error": False}]
        app._set_protocol_frames(frames, {"primary": 1})
        self.pump()
        app._set_protocol_frames([], {})
        app._schedule_update_plot()
        wait_until(self.root, lambda: False, timeout=0.3)

    def test_center_dialog_on_main_window(self):
        """El diálogo se centra sobre la ventana principal (coords
        absolutas), no sobre la pantalla primaria (gotcha multimonitor)."""
        app = self.app
        win = tk.Toplevel(self.root)
        win.attributes("-alpha", 0.0)
        app._center_dialog(win, 300, 200)
        self.pump()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        cx = win.winfo_rootx() + win.winfo_width() / 2
        cy = win.winfo_rooty() + win.winfo_height() / 2
        self.assertAlmostEqual(cx, rx + rw / 2, delta=40)
        self.assertAlmostEqual(cy, ry + rh / 2, delta=60)
        win.destroy()

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
