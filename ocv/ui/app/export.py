"""Image export (light report figure), clipboard copy and plot context menu."""
import matplotlib.pyplot as plt
import numpy as np
import sys
import tkinter as tk

from tkinter import filedialog, ttk

from ocv.core.scope import H_DIVS, V_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si
from ocv.ui import theme


class ExportMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    def _open_save_image_dialog(self):
        """Ventana fija con opciones (cursores / measurements / escala Y)
        y luego guarda."""
        if self.time is None or not self.channels:
            self._show_toast("Nothing to save (no data loaded)")
            return

        cursors_available = (
            self._cursors_vertical_active() or self._cursors_horizontal_active()
        )
        meas_available = bool(self.active_measurements)

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Save image")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 360, 260)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win, text="Image options",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=12, pady=(14, 8), anchor="w")

        var_cursors = tk.BooleanVar(value=cursors_available)
        var_meas    = tk.BooleanVar(value=meas_available)

        cb_cursors = ttk.Checkbutton(
            win, text="Include cursors" + ("" if cursors_available else "  (none active)"),
            variable=var_cursors
        )
        cb_cursors.pack(anchor="w", padx=18, pady=2)
        if not cursors_available:
            cb_cursors.state(["disabled"])

        cb_meas = ttk.Checkbutton(
            win, text="Include measurements" + ("" if meas_available else "  (none)"),
            variable=var_meas
        )
        cb_meas.pack(anchor="w", padx=18, pady=2)
        if not meas_available:
            cb_meas.state(["disabled"])

        # Annotations
        annot_available = bool(self.annotations)
        var_annot = tk.BooleanVar(value=annot_available)
        cb_annot = ttk.Checkbutton(
            win, text="Include annotations" + ("" if annot_available else "  (none)"),
            variable=var_annot
        )
        cb_annot.pack(anchor="w", padx=18, pady=2)
        if not annot_available:
            cb_annot.state(["disabled"])

        # Selector de canal para escala vertical
        tk.Label(
            win, text="Show Y-scale ticks for channel:",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=18, pady=(10, 2))

        NONE_LABEL = "(none — generic grid)"
        choices = [NONE_LABEL] + [ch["name"] for ch in self.channels]
        var_yscale = tk.StringVar(value=NONE_LABEL)
        combo = ttk.Combobox(
            win, textvariable=var_yscale,
            values=choices, state="readonly"
        )
        combo.pack(fill=tk.X, padx=18, pady=(0, 6))

        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        def on_cancel():
            win.destroy()

        def on_save():
            include_cursors = bool(var_cursors.get()) and cursors_available
            include_meas    = bool(var_meas.get()) and meas_available
            include_annot   = bool(var_annot.get()) and annot_available
            sel = var_yscale.get()
            if sel == NONE_LABEL:
                yscale_ch_idx = None
            else:
                # nombres pueden repetirse tras rename: buscamos el primero
                yscale_ch_idx = next(
                    (i for i, ch in enumerate(self.channels) if ch["name"] == sel),
                    None
                )
            win.destroy()
            self._save_image_as(include_cursors, include_meas, yscale_ch_idx, include_annot)

        ttk.Button(btns, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save...", style="Accent.TButton",
                   command=on_save).pack(side=tk.RIGHT, padx=(0, 6))

    def _save_image_as(self, include_cursors, include_meas, yscale_ch_idx=None,
                       include_annot=False):
        """Pide ruta y construye una figura clara (fondo blanco, grid negro)
        con los canales, leyenda de escalas y opcionalmente cursores/medidas."""
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save oscilloscope image",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"),
                       ("JPEG image", "*.jpg;*.jpeg"),
                       ("PDF", "*.pdf"),
                       ("SVG", "*.svg")],
        )
        if not path:
            return

        try:
            fig = self._build_export_figure(include_cursors, include_meas, yscale_ch_idx,
                                            include_annot)
            fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight")
            plt.close(fig)
            self._show_toast(f"Saved: {path.split('/')[-1].split(chr(92))[-1]}")
        except Exception as e:
            self._show_toast(f"Save failed: {e}", duration_ms=2500)

    def _build_export_figure(self, include_cursors, include_meas, yscale_ch_idx=None,
                             include_annot=False):
        """Construye una figura matplotlib con look 'paper': fondo blanco,
        grid negro, canales con sus colores, leyenda de escalas, flechitas
        de cada canal y opcionalmente ticks Y en unidades reales del canal
        seleccionado."""
        from matplotlib.figure import Figure

        fig = Figure(figsize=(12, 7), facecolor="white")
        ax = fig.add_subplot(111)
        ax.set_facecolor("white")

        t_div = self.t_div.get()
        v_div_active = self.v_div.get()
        t_start = self.t_offset.get()
        window = t_div * H_DIVS
        t_end = t_start + window
        mask = (self.time >= t_start) & (self.time <= t_end)

        # Ejes: centramos en 0, mismas divisiones que en pantalla
        v_range = V_DIVS * v_div_active
        ax.set_xlim(0, window)
        ax.set_ylim(-v_range / 2, v_range / 2)

        # Grid negro con líneas finas
        ax.grid(True, which="both", color="#000000", alpha=0.25,
                linewidth=0.6, linestyle="-")

        # Ticks en divisiones
        xticks = np.linspace(0, window, H_DIVS + 1)
        yticks = np.linspace(-v_range / 2, v_range / 2, V_DIVS + 1)
        ax.set_xticks(xticks)
        ax.set_yticks(yticks)
        # Etiquetas de tiempo REAL: posición del tick (x) + offset del
        # viewport (t_start) + origen del CSV (time_origin).
        t_o = self._t_display_origin()
        ax.set_xticklabels([format_si(t_start + x + t_o, "s", sig_figs=3)
                            for x in xticks],
                           fontsize=8, color="black", rotation=0)

        # Ticks Y: si hay canal elegido, valores reales en "V" de ese canal
        if yscale_ch_idx is not None and 0 <= yscale_ch_idx < len(self.channels):
            sel = self.channels[yscale_ch_idx]
            # y_screen = (v - offset_sel) / v_div_sel · v_div_active
            # → v_real = y_screen / v_div_active · v_div_sel + offset_sel
            yreal = yticks / v_div_active * sel["v_div"] + sel["v_offset"]
            y_unit = sel.get("unit", DEFAULT_UNIT)
            ax.set_yticklabels(
                [format_si(v, y_unit, sig_figs=3) for v in yreal],
                fontsize=8, color=sel["color"]
            )
        else:
            ax.set_yticklabels([""] * len(yticks))
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(1.0)

        # Dibujar canales + flechita de 0V con el nombre
        legend_lines = []
        order = [i for i in range(len(self.channels)) if i != self.active_channel_idx]
        order.append(self.active_channel_idx)
        t_range = window
        v_range_half = v_range / 2
        for i in order:
            ch = self.channels[i]
            if not ch["visible"].get():
                continue
            v = ch["voltage"][mask]
            divs = (v - ch["v_offset"]) / ch["v_div"]
            y_plot = divs * v_div_active
            is_active = (i == self.active_channel_idx)
            ax.plot(self.time[mask] - t_start, y_plot,
                    color=ch["color"],
                    linewidth=1.2 if is_active else 1.0)
            legend_lines.append(
                f"{ch['name']}: {format_si(ch['v_div'], ch.get('unit', DEFAULT_UNIT))}/div"
            )

            # Flechita indicando 0V del canal, a la izquierda del eje
            y_gnd = (-ch["v_offset"] / ch["v_div"]) * v_div_active
            if -v_range_half <= y_gnd <= v_range_half:
                ax.annotate(
                    f"▶ {ch['name']}",
                    xy=(0, y_gnd),
                    xytext=(-t_range * 0.015, y_gnd),
                    ha="right", va="center",
                    color=ch["color"],
                    fontsize=9,
                    fontweight="bold" if is_active else "normal",
                    annotation_clip=False,
                )

        # Título/escalas arriba
        title = f"T/div: {format_si(t_div, 's')}   |   "
        title += "   ".join(legend_lines) if legend_lines else "(no visible channels)"
        ax.set_title(title, fontsize=10, color="black", loc="left", pad=10)

        # Cursores
        if include_cursors:
            if self._cursors_vertical_active():
                for x in self.cursor_x:
                    if t_start <= x <= t_end:
                        ax.axvline(x - t_start, color="#444444",
                                   linestyle=(0, (5, 5)), linewidth=1.0)
                if len(self.cursor_x) == 2:
                    dt = abs(self.cursor_x[1] - self.cursor_x[0])
                    freq_txt = format_si(1.0 / dt, "Hz") if dt > 0 else "—"
                    ax.text(0.01, -0.12,
                            f"Δt = {format_si(dt, 's')}    1/Δt = {freq_txt}",
                            transform=ax.transAxes, fontsize=9, color="black")

            if self._cursors_horizontal_active():
                for y in self.cursor_y:
                    ax.axhline(y, color="#444444",
                               linestyle=(0, (5, 5)), linewidth=1.0)
                if len(self.cursor_y) == 2:
                    dv = abs(self.cursor_y[1] - self.cursor_y[0])
                    ax.text(0.35, -0.12,
                            f"ΔV = {format_si(dv, self._active_unit())}",
                            transform=ax.transAxes, fontsize=9, color="black")

        # Measurements: tabla compacta debajo
        if include_meas and self.active_measurements:
            results = self.compute_measurements()
            lines = []
            for m in results:
                name_ch = m.get("channel_name", "?")
                val = m.get("value")
                unit = m.get("unit", "")
                if val is None:
                    val_str = "—"
                elif unit:
                    val_str = format_si(val, unit)
                else:
                    val_str = f"{val:.4g}"
                lines.append(f"{name_ch} · {m.get('name', '?')}: {val_str}")
            if lines:
                txt = "   |   ".join(lines)
                fig.text(0.5, 0.01, txt, ha="center", fontsize=8, color="black")

        # Annotations (strokes + texto)
        if include_annot and self.annotations:
            for a in self.annotations:
                if a["type"] == "stroke":
                    pts = a["points"]
                    if len(pts) < 2:
                        continue
                    xs = [p[0] - t_start for p in pts]
                    ys = [p[1] for p in pts]
                    if max(xs) < 0 or min(xs) > window:
                        continue
                    ax.plot(xs, ys, color=a["color"],
                            linewidth=a.get("width", 2),
                            solid_capstyle="round",
                            solid_joinstyle="round",
                            zorder=5)
                else:  # text
                    t, v = a["pos"]
                    if not (t_start <= t <= t_end):
                        continue
                    ax.text(t - t_start, v, a["content"],
                            color=a["color"], fontsize=a.get("size", 10),
                            fontfamily="Consolas",
                            verticalalignment="center",
                            zorder=5)

        fig.tight_layout(rect=(0, 0.04 if include_meas else 0, 1, 1))
        return fig

    def _copy_plot_to_clipboard(self):
        """Copia la imagen actual del plot al portapapeles como DIB
        (CF_DIB). Solo Windows; en otras plataformas avisa con un toast.

        Sin Pillow: monta la cabecera BITMAPINFOHEADER a mano y mueve
        los pixels BGRA al clipboard via ctypes. Top-down (biHeight
        negativo) para no tener que voltear filas.

        Ojo: si SetClipboardData tiene éxito Windows toma posesión del
        handle de GlobalAlloc — NO se libera. Si falla cualquier paso
        previo, sí hay que GlobalFree para no leakear."""
        if not sys.platform.startswith("win"):
            self._show_toast("Clipboard image only on Windows")
            return
        if self.time is None or not self.channels:
            self._show_toast("Nothing to copy (no data loaded)")
            return

        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:
            self._show_toast(f"Clipboard unavailable: {e}", duration_ms=2200)
            return

        try:
            self.canvas.draw()
            w, h = self.canvas.get_width_height()
            rgba = np.asarray(self.canvas.buffer_rgba())
            # GDI espera BGRA en 32-bit BI_RGB.
            bgra = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]]).tobytes()
        except Exception as e:
            self._show_toast(f"Render failed: {e}", duration_ms=2200)
            return

        DIB_HEADER_SIZE = 40
        BI_RGB = 0
        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize",          ctypes.c_uint32),
                ("biWidth",         ctypes.c_int32),
                ("biHeight",        ctypes.c_int32),
                ("biPlanes",        ctypes.c_uint16),
                ("biBitCount",      ctypes.c_uint16),
                ("biCompression",   ctypes.c_uint32),
                ("biSizeImage",     ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed",       ctypes.c_uint32),
                ("biClrImportant",  ctypes.c_uint32),
            ]

        hdr = BITMAPINFOHEADER()
        hdr.biSize = DIB_HEADER_SIZE
        hdr.biWidth = w
        hdr.biHeight = -h            # top-down
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB
        hdr.biSizeImage = w * h * 4

        total_size = DIB_HEADER_SIZE + hdr.biSizeImage

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, total_size)
        if not h_mem:
            self._show_toast("Clipboard alloc failed", duration_ms=2200)
            return

        try:
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                self._show_toast("Clipboard lock failed", duration_ms=2200)
                return
            ctypes.memmove(ptr, ctypes.byref(hdr), DIB_HEADER_SIZE)
            ctypes.memmove(ptr + DIB_HEADER_SIZE, bgra, hdr.biSizeImage)
            kernel32.GlobalUnlock(h_mem)

            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(h_mem)
                self._show_toast("Clipboard busy", duration_ms=2200)
                return
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_DIB, h_mem):
                    kernel32.GlobalFree(h_mem)
                    user32.CloseClipboard()
                    self._show_toast("Clipboard write failed", duration_ms=2200)
                    return
                # Éxito: Windows posee el handle, NO liberar.
                h_mem = None
            finally:
                user32.CloseClipboard()
            self._show_toast("Copied to clipboard", duration_ms=1200)
        except Exception as e:
            if h_mem:
                try:
                    kernel32.GlobalFree(h_mem)
                except Exception:
                    pass
            self._show_toast(f"Clipboard error: {e}", duration_ms=2200)

    def _show_plot_context_menu(self, event):
        """Menú contextual al hacer click derecho sobre el plot."""
        # Coordenadas en pantalla (absolutas)
        try:
            x_root = self.root.winfo_pointerx()
            y_root = self.root.winfo_pointery()
        except Exception:
            return

        menu = tk.Menu(
            self.root, tearoff=0,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_ACCENT, activeforeground=theme.COLOR_TEXT_ON_ACCENT,
            bd=0,
        )
        menu.add_command(
            label="Save image as...",
            command=self._open_save_image_dialog,
        )
        if sys.platform.startswith("win"):
            menu.add_command(
                label="Copy image to clipboard",
                command=self._copy_plot_to_clipboard,
            )
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()
