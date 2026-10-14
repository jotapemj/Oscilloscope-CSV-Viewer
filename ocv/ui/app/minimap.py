"""Overview strip above the plot: drawing, click-to-jump, drag and wheel zoom."""
import numpy as np
import tkinter as tk

from ocv.core.scope import H_DIVS
from ocv.ui import theme


class MinimapMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================
    # Minimap
    # =============================
    def _redraw_minimap(self):
        """Redibuja el minimap: traza(s) decimadas + rectángulo de ventana visible."""
        c = self.minimap_canvas
        try:
            w = c.winfo_width()
            h = c.winfo_height()
        except tk.TclError:
            return
        if w <= 1 or h <= 1:
            return
        c.delete("all")

        # En modo XY el minimap no tiene sentido: ocultar contenido
        if self.xy_config.get("enabled"):
            c.create_text(w / 2, h / 2, text="XY mode — minimap disabled",
                          fill=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9, "italic"))
            return

        if self.time is None or not self.channels or self.t_total <= 0:
            c.create_text(w / 2, h / 2, text="No data",
                          fill=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9, "italic"))
            return

        # Decimación a ancho del canvas
        N = max(1, int(w))
        n_samples = len(self.time)
        if n_samples > N:
            step = n_samples // N
            idxs = np.arange(0, n_samples, step)
        else:
            idxs = np.arange(n_samples)
        t_vis = self.time[idxs]

        # Encuadre vertical: NORMALIZAR cada canal a su propio rango para que
        # señales de amplitudes muy distintas (5V vs 800V) se vean igual de
        # bien. Cada canal ocupa toda la altura del minimap.
        margin_y = 4
        def map_x(t):
            return (t / self.t_total) * (w - 2) + 1

        # Dibujar canales: primero los no activos (gris), luego el activo en su color
        order = [i for i in range(len(self.channels)) if i != self.active_channel_idx]
        order.append(self.active_channel_idx)
        for i in order:
            ch = self.channels[i]
            if not ch["visible"].get():
                continue
            v = ch["voltage"][idxs]
            v_lo = float(np.min(v))
            v_hi = float(np.max(v))
            if v_hi == v_lo:
                v_hi = v_lo + 1.0
            is_active = (i == self.active_channel_idx)
            color = ch["color"] if is_active else "#666666"
            width = 1.6 if is_active else 1.0
            # Cada canal se normaliza a [0..1] y ocupa toda la altura
            pts = []
            for k in range(len(v)):
                norm = (v[k] - v_lo) / (v_hi - v_lo)
                y = h - margin_y - norm * (h - 2 * margin_y)
                pts.append(map_x(t_vis[k]))
                pts.append(y)
            if len(pts) >= 4:
                c.create_line(*pts, fill=color, width=width, smooth=False)

        # Rectángulo de ventana visible: solo cuando hay zoom (ventana
        # visible es menor que la traza total).
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        if window < self.t_total * 0.999:
            x1 = map_x(t_start)
            x2 = map_x(min(t_start + window, self.t_total))
            c.create_rectangle(x1, 1, x2, h - 1,
                               outline=theme.COLOR_ACCENT, width=1.5,
                               fill=theme.COLOR_ACCENT, stipple="gray25")

    def _minimap_click_to_time(self, x_pixel):
        """Convierte coordenada X del canvas a tiempo absoluto."""
        try:
            w = self.minimap_canvas.winfo_width()
        except tk.TclError:
            return 0.0
        if w <= 2 or self.t_total <= 0:
            return 0.0
        frac = (x_pixel - 1) / (w - 2)
        frac = max(0.0, min(1.0, frac))
        return frac * self.t_total

    def _minimap_rect_bounds_px(self):
        """Devuelve (x1, x2) en pixels del rectángulo visible actual."""
        try:
            w = self.minimap_canvas.winfo_width()
        except tk.TclError:
            return (0, 0)
        if self.t_total <= 0:
            return (0, 0)
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        x1 = t_start / self.t_total * (w - 2) + 1
        x2 = min(t_start + window, self.t_total) / self.t_total * (w - 2) + 1
        return (x1, x2)

    def _on_minimap_press(self, event):
        if self.time is None or self.xy_config.get("enabled"):
            return
        x1, x2 = self._minimap_rect_bounds_px()
        # Si clicaste DENTRO del rectángulo: preparar drag (pan)
        if x1 <= event.x <= x2:
            self._minimap_dragging = True
            t_click = self._minimap_click_to_time(event.x)
            self._minimap_drag_offset = t_click - self.t_offset.get()
        else:
            # Clic fuera: centrar ventana en ese punto
            self._minimap_dragging = False
            window = self.t_div.get() * H_DIVS
            t_click = self._minimap_click_to_time(event.x)
            new_offset = max(0.0, min(t_click - window / 2.0,
                                      self.t_total - window))
            self.t_offset.set(new_offset)
            try:
                self.h_slider.set(new_offset)
            except Exception:
                pass
            self.update_plot()

    def _on_minimap_drag(self, event):
        if not self._minimap_dragging or self.time is None:
            return
        t_at_cursor = self._minimap_click_to_time(event.x)
        new_offset = t_at_cursor - self._minimap_drag_offset
        window = self.t_div.get() * H_DIVS
        new_offset = max(0.0, min(new_offset, self.t_total - window))
        self.t_offset.set(new_offset)
        try:
            self.h_slider.set(new_offset)
        except Exception:
            pass
        self.update_plot()

    def _on_minimap_release(self, event):
        self._minimap_dragging = False

    def _on_minimap_wheel(self, event):
        """Windows/Mac: event.delta ±120 por tick."""
        direction = "down" if event.delta > 0 else "up"  # rueda arriba = zoom in (t_div ↓)
        self._step_t_div(direction)

    def _on_minimap_wheel_linux(self, event, sign):
        """Linux: Button-4 = scroll up, Button-5 = scroll down."""
        direction = "down" if sign > 0 else "up"
        self._step_t_div(direction)
