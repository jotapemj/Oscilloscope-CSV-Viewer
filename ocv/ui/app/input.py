"""Keyboard shortcuts and mouse interaction on the main plot."""
import numpy as np
import tkinter as tk

from tkinter import ttk

from ocv.core.scope import H_DIVS, TIME_DIV_VALUES, V_DIV_VALUES, V_DIVS
from ocv.core.si_format import format_si


class InputMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # -----------------------------
    def is_click_on_trace(self, event):
        """True si el click cae cerca de ALGÚN canal visible."""
        return self.find_channel_under_click(event) is not None

    def find_channel_under_click(self, event):
        """Devuelve el índice del canal visible cuyo trazo pasa más cerca
        del punto clicado, o None si ninguno está dentro de tolerancia.
        Usa las coordenadas de pantalla ya reescaladas (event.ydata está
        en 'V equivalentes del canal activo'). """
        if self.time is None or not self.channels:
            return None
        if event.xdata is None or event.ydata is None:
            return None

        # Tiempo real del CSV
        click_time = event.xdata + self.t_offset.get()
        idx = int(np.searchsorted(self.time, click_time))
        idx = max(0, min(idx, len(self.time) - 1))

        v_div_active = self.v_div.get()
        # Tolerancia: 0.5 divisiones (en coordenadas de pantalla)
        tol_screen = v_div_active * 0.5

        best_i = None
        best_dist = float("inf")
        for i, ch in enumerate(self.channels):
            if not ch["visible"].get():
                continue
            # Reescalar el valor del canal a coordenadas de pantalla
            v_raw = ch["voltage"][idx]
            y_screen = (v_raw - ch["v_offset"]) / ch["v_div"] * v_div_active
            dist = abs(event.ydata - y_screen)
            if dist < tol_screen and dist < best_dist:
                best_dist = dist
                best_i = i
        return best_i

        # No marcamos dirty aquí explícitamente: las tk.Vars v_div/v_offset
        # tienen trace que ya lo hace. Esta función solo vuelca a dict.


    def _hotkey_guard(self):
        """Devuelve True si el foco está en un widget que captura texto
        (Entry/Spinbox/Text). Los handlers de atajos globales llaman a esto
        al principio Y si devuelve True, abortan — así escribir en V/div,
        nombres de canal, expresiones math, etc. no dispara atajos."""
        try:
            w = self.root.focus_get()
        except Exception:
            return False
        return isinstance(
            w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, tk.Text)
        )

    def _on_active_channel_hotkey(self, idx):
        """Atajo numérico (1-9): cambia el canal activo si hay canal en ese
        índice. Ignora la pulsación si el foco está en un widget de texto
        para no robar dígitos mientras se escribe."""
        if self._hotkey_guard():
            return
        if not (0 <= idx < len(self.channels)):
            return
        # Mismo canal: no-op silencioso (evita redraw + dirty espurio).
        if idx == self.active_channel_idx:
            return
        self.set_active_channel(idx)
        try:
            ch_name = self.channels[idx].get("name", f"CH{idx + 1}")
            self._show_toast(f"Active: {ch_name}", duration_ms=900)
        except Exception:
            pass

    # =====================
    # Atajos globales v1.30 — handlers
    # =====================
    def _hotkey_toggle_cursors(self):
        """C: toggle cursores horizontales+verticales del canal activo."""
        if self._hotkey_guard():
            return
        try:
            self.cursors_enabled.set(not bool(self.cursors_enabled.get()))
            self.on_cursors_toggle()
            self._show_toast(
                f"Cursors: {'on' if self.cursors_enabled.get() else 'off'}",
                duration_ms=800,
            )
        except Exception:
            pass

    def _hotkey_open_unit_picker(self):
        """U: abrir el picker de unidad del canal activo."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            self._open_unit_picker_dialog(self.active_channel_idx)
        except Exception:
            pass

    def _hotkey_snap_gnd(self):
        """G: snap del 0V del canal activo a la división vertical más cercana."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            self._snap_channel_gnd_to_grid(self.active_channel_idx)
        except Exception:
            pass

    def _hotkey_center_channel(self):
        """0: centrar el canal activo en el origen (v_offset = 0)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            ch = self.channels[self.active_channel_idx]
            ch["v_offset"] = 0.0
            self.v_offset.set(0.0)
            try:
                self.v_slider.set(0.0)
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_toggle_channel_visibility(self, idx):
        """Ctrl+1..9: toggle visibilidad del canal en el índice dado."""
        if self._hotkey_guard():
            return
        if not (0 <= idx < len(self.channels)):
            return
        try:
            var = self.channels[idx].get("visible")
            if var is not None:
                var.set(not bool(var.get()))
                # var.trace dispara update_plot + dirty.
                self._show_toast(
                    f"{self.channels[idx].get('name', f'CH{idx+1}')}: "
                    f"{'on' if var.get() else 'off'}",
                    duration_ms=700,
                )
        except Exception:
            pass

    def _hotkey_pan_v(self, direction):
        """Flecha arr/abj: mueve el canal activo verticalmente 0.1 div.
        direction = +1 (arriba) o -1 (abajo).
        Convención: flecha arriba → traza sube → v_offset disminuye
        (porque y_screen = (v_real - v_offset)/v_div, subir v_offset
        baja la traza)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            ch = self.channels[self.active_channel_idx]
            step = ch["v_div"] * 0.1 * direction
            ch["v_offset"] -= step
            self.v_offset.set(ch["v_offset"])
            try:
                self.v_slider.set(-ch["v_offset"])
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_pan_h(self, direction):
        """Flecha izq/der: mueve t_offset global 0.1 div de tiempo.
        direction = -1 (izq) o +1 (der)."""
        if self._hotkey_guard():
            return
        try:
            step = self.t_div.get() * 0.1 * direction
            self.t_offset.set(self.t_offset.get() + step)
            try:
                self.h_slider.set(self.t_offset.get())
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_step_v_div(self, direction):
        """Shift+arr/abj: paso discreto de V/div del canal activo.
        direction = -1 (más zoom, V/div más pequeño) o +1 (menos zoom)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            current = self.v_div.get()
            if self.fine_mode.get():
                # En fine mode: paso de 0.1
                step = 0.1 * (-direction)
                new_val = max(0.1, round(current + step, 2))
            else:
                # Buscar el siguiente valor discreto en V_DIV_VALUES
                idx = min(range(len(V_DIV_VALUES)),
                          key=lambda i: abs(V_DIV_VALUES[i] - current))
                new_idx = max(0, min(len(V_DIV_VALUES) - 1, idx + direction))
                new_val = V_DIV_VALUES[new_idx]
            if abs(new_val - current) < 1e-15:
                return
            self.v_div.set(new_val)
            self._updating_display = True
            self.v_div_display.set(format_si(new_val, self._active_unit()))
            self._updating_display = False
            self.on_v_div_change()
        except Exception:
            pass

    def _hotkey_move_cursor(self, axis, idx, direction):
        """Mueve un cursor por teclado.
        axis = 'x' (tiempo) o 'y' (voltaje del canal activo).
        idx = 0 (cursor 1) o 1 (cursor 2).
        direction = -1 / +1.
        Step = 0.05 div (más fino que el pan general de 0.1).
        Si los cursores están deshabilitados, los activa antes de mover."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            # Auto-activar cursores si están off (mejor UX: el usuario que
            # pulsa el atajo claramente quiere usarlos).
            if not bool(self.cursors_enabled.get()):
                self.cursors_enabled.set(True)
                self.on_cursors_toggle()
            if axis == "x":
                if idx >= len(self.cursor_x):
                    return
                step = self.t_div.get() * 0.05 * direction
                self.cursor_x[idx] = float(self.cursor_x[idx]) + step
            else:  # axis == "y"
                if idx >= len(self.cursor_y):
                    return
                # Step en voltaje del canal activo (el cursor Y es relativo
                # a la escala del activo, igual que el drag con el ratón).
                ch = self.channels[self.active_channel_idx]
                step = ch["v_div"] * 0.05 * direction
                self.cursor_y[idx] = float(self.cursor_y[idx]) + step
            self.update_plot()
            self._mark_dirty()
        except Exception:
            pass

    def _hotkey_step_t_div(self, direction):
        """Ctrl+arr/abj: paso discreto de T/div global.
        direction = -1 (más zoom, ms→µs) o +1 (menos zoom)."""
        if self._hotkey_guard():
            return
        try:
            current = self.t_div.get()
            # Buscar el vecino canónico en TIME_DIV_VALUES.
            if direction > 0:
                target = next((v for v in TIME_DIV_VALUES if v > current * 1.0001),
                              TIME_DIV_VALUES[-1])
            else:
                target = next((v for v in reversed(TIME_DIV_VALUES)
                               if v < current * 0.9999), TIME_DIV_VALUES[0])
            if abs(target - current) < 1e-15:
                return
            self._updating_display = True
            self.t_div_display.set(format_si(target, "s"))
            self._updating_display = False
            self.t_div.set(target)
            self.on_time_div_change()
        except Exception:
            pass

        

    def _on_plot_ctrl_wheel(self, event):
        """Ctrl + rueda sobre el plot: zoom horizontal (T/div).

        Mismo comportamiento que la rueda sobre el minimap pero accesible
        sin tener que mover el ratón fuera del grid.

        Convención: rueda ARRIBA → zoom in (T/div ↓, mostrar menos tiempo),
        rueda ABAJO → zoom out. Igual que _on_minimap_wheel para que el
        usuario no tenga que reaprender gestos.
        """
        if self.time is None:
            return
        if hasattr(event, "delta") and event.delta:
            direction = "down" if event.delta > 0 else "up"
        elif getattr(event, "num", None) == 4:
            direction = "down"     # Linux scroll-up → zoom in
        elif getattr(event, "num", None) == 5:
            direction = "up"       # Linux scroll-down → zoom out
        else:
            return
        self._step_t_div(direction)
        return "break"

    def _on_plot_shift_wheel(self, event):
        """Shift + rueda sobre el plot: pan horizontal.

        Detecta el sentido de la rueda según la plataforma:
          - Windows/Mac: event.delta (±120 múltiplos; signo = dirección).
          - Linux: usa Button-4 (up) y Button-5 (down), event.num.

        Mueve t_offset en pasos de ~1 división (=t_div). Es lo bastante
        rápido para navegar pero sin saltos enormes que despisten.
        """
        if self.time is None:
            return
        # Determinar dirección
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta < 0 else -1     # rueda abajo = pan→
        elif getattr(event, "num", None) == 5:
            direction = 1                                 # Linux scroll-down
        elif getattr(event, "num", None) == 4:
            direction = -1                                # Linux scroll-up
        else:
            return

        step = self.t_div.get() * direction              # 1 div por tick
        new_offset = self.t_offset.get() + step

        # Clamp al rango válido del CSV: 0 .. (t_total - t_window)
        t_window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - t_window)
        new_offset = max(0.0, min(new_offset, max_offset))

        self.t_offset.set(new_offset)
        # Sincronizar el slider horizontal si existe
        try:
            self.h_slider.set(new_offset)
        except Exception:
            pass
        self.update_plot()
        # Devolver "break" para que el evento no se propague a contenedores
        return "break"

    def on_mouse_press(self, event):
        # Click derecho: menú contextual del plot
        if event.button == 3 and event.inaxes == self.ax:
            self._show_plot_context_menu(event)
            return

        # Annotation mode: intercepta y consume el evento
        if self._is_annotation_active():
            if self._on_annotation_press(event):
                return

        if event.button != 1 or event.inaxes != self.ax:
            return
        if self.time is None or event.xdata is None:
            return

        click_consumed = False


        # -----------------------------
        # CURSORES
        # -----------------------------
        # En modo combinado, probamos primero con el tipo cuyo click está
        # más cerca (minimal distance en coordenadas de su eje).

        v_on = self._cursors_vertical_active()
        h_on = self._cursors_horizontal_active()

        if v_on or h_on:
            # Probar vertical (tiempo)
            if v_on:
                click_time = event.xdata + self.t_offset.get()
                tol = self.t_div.get() * 0.2
                for i, x in enumerate(self.cursor_x):
                    if abs(click_time - x) < tol:
                        self.active_cursor = i
                        click_consumed = True
                        break

            # Si no se ha agarrado vertical, probar horizontal
            if not click_consumed and h_on:
                click_voltage = event.ydata
                tol = self.v_div.get() * 0.3
                for i, y in enumerate(self.cursor_y):
                    if abs(click_voltage - y) < tol:
                        self.active_cursor_y = i
                        click_consumed = True
                        break


        # -----------------------------
        # DRAG DE TRAZA (fallback) - siempre horizontal,
        # y también vertical SOLO si se pincha sobre la traza
        # -----------------------------
        if not click_consumed:
            self.dragging = True
            self.drag_start_x = event.xdata
            self.drag_start_y = event.ydata
            self.drag_start_offset = self.t_offset.get()

            # ¿El click ha caído sobre algún canal? Si sí, actívalo y
            # habilita drag vertical. Si no, solo drag horizontal.
            clicked_idx = self.find_channel_under_click(event)
            if clicked_idx is not None:
                if clicked_idx != self.active_channel_idx:
                    self.set_active_channel(clicked_idx)
                    self._rebuild_channels_panel()
                self.drag_vertical_enabled = True
                self.drag_start_v_offset = self.v_offset.get()
            else:
                self.drag_vertical_enabled = False
                self.drag_start_v_offset = self.v_offset.get()

    def on_mouse_release(self, event):
        # Annotation: termina stroke si estaba dibujando
        if self._current_stroke is not None:
            self._on_annotation_release(event)
            return
        self.dragging = False
        self.active_cursor = None
        self.active_cursor_y = None

    def on_mouse_move(self, event):
        if self.time is None:
            return

        # Annotation: prolonga stroke si se está dibujando
        if self._current_stroke is not None:
            self._on_annotation_move(event)
            return

        # -----------------------------
        # CURSORES ACTIVOS
        # -----------------------------
        # Cursor vertical (tiempo) - funciona en modos Vertical y Horizontal+Vertical
        if self._cursors_vertical_active() and self.active_cursor is not None:
            if event.xdata is None:
                return
            new_time = event.xdata + self.t_offset.get()
            new_time = max(0.0, min(new_time, self.t_total))
            self.cursor_x[self.active_cursor] = new_time
            self._schedule_update_plot()
            self._mark_dirty()
            return

        # Cursor horizontal (voltaje) - funciona en modos Horizontal y Horizontal+Vertical
        if self._cursors_horizontal_active() and self.active_cursor_y is not None:
            if event.ydata is None:
                return
            new_voltage = event.ydata
            v_half_range = (self.v_div.get() * V_DIVS) / 2
            new_voltage = max(-v_half_range, min(new_voltage, v_half_range))
            self.cursor_y[self.active_cursor_y] = new_voltage
            self._schedule_update_plot()
            self._mark_dirty()
            return

        # -----------------------------
        # MOVER TRAZA (fallback) - X e Y
        # -----------------------------
        if not self.dragging:
            return

        if event.xdata is None:
            return

        # --- Horizontal ---
        dx = event.xdata - self.drag_start_x
        window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        new_offset = self.drag_start_offset - dx
        new_offset = max(0.0, min(new_offset, max_offset))

        self.t_offset.set(new_offset)
        self.h_slider.set(new_offset)

        # --- Vertical (solo si el click inicial fue sobre la traza) ---
        if getattr(self, "drag_vertical_enabled", False) and event.ydata is not None:
            dy = event.ydata - self.drag_start_y
            # Modelo nuevo: las trazas se dibujan como (v - offset)/v_div.
            # Arrastrar hacia arriba → la traza sube → offset debe disminuir.
            new_v_offset = self.drag_start_v_offset - dy

            # Limitar a ±4 divisiones (en V del canal activo)
            v_limit = self.v_div.get() * 4
            new_v_offset = max(-v_limit, min(new_v_offset, v_limit))

            self.v_offset.set(new_v_offset)
            # Slider INVERTIDO: derecha=+=traza arriba=offset negativo.
            # Bloqueamos el callback del slider para que no re-escriba el offset.
            self._syncing_active = True
            try:
                self.v_slider.set(-new_v_offset)
            finally:
                self._syncing_active = False
            # Persistir al canal activo
            if self.channels:
                self.channels[self.active_channel_idx]["v_offset"] = new_v_offset

        self._schedule_update_plot()
