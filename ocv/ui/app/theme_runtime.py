"""Live theme switching for the main window (instant or with fade)."""
import tkinter as tk

from ocv.core.config import load_user_config, save_user_config
from ocv.ui import theme
from ocv.ui.theme import _retheme_tk_widgets, apply_theme, THEMES


class ThemeRuntimeMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    def _force_refresh_ttk_in(self, parent):
        """Recorre el árbol y, para los ttk.Treeview encontrados, fuerza un
        recálculo de estilo. ttk almacena en caché los colores y no los
        actualiza solo cuando se reconfigura ttk.Style globalmente.

        Truco: cambiar el estilo a uno custom temporal y volver al default.
        Esto invalida el cache interno.
        """
        try:
            cls = parent.winfo_class()
        except Exception:
            return
        if cls == "Treeview":
            try:
                # Forzar re-cálculo: re-aplicar el style por nombre
                cur_style = parent.cget("style") or "Treeview"
                parent.configure(style="")
                parent.update_idletasks()
                parent.configure(style=cur_style)
            except Exception:
                pass
        # Recursión
        try:
            for c in parent.winfo_children():
                self._force_refresh_ttk_in(c)
        except Exception:
            pass

    def apply_theme_runtime(self, name, sync_plot=True, fade=None):
        """Cambia el tema de la chrome en runtime, opcionalmente con fade.

        Parámetros:
            name      : "Dark" | "Light" | "Blueprint" | "High Contrast"
            sync_plot : si True, también actualiza self.grid_theme con los
                        colores PLOT_* del tema. Si False, el plot mantiene
                        su tema independiente (útil cuando el usuario ha
                        personalizado el plot a mano).
            fade      : True/False/None. Si None, se lee de user_config
                        (clave 'theme_fade', default True).

        Comportamiento:
            - fade=False → cambio instantáneo (comportamiento legacy).
            - fade=True  → animación corta (~180-200 ms) interpolando los
                           colores entre el tema actual y el destino, y
                           barrido completo al final.
        """
        if name not in THEMES:
            return
        if name == theme.CURRENT_THEME_NAME and not sync_plot:
            return

        # Resolver fade desde config si no lo fuerza el caller
        if fade is None:
            fade = bool(load_user_config().get("theme_fade", True))

        if fade and name != theme.CURRENT_THEME_NAME:
            # Solo tiene sentido animar si CAMBIA el tema; si solo cambia
            # sync_plot pero el nombre es el mismo, ir directo.
            self._apply_theme_fade(name, sync_plot)
        else:
            self._apply_theme_immediate(name, sync_plot)

    def _apply_theme_fade(self, name, sync_plot):
        """Transición de tema con overlay alpha — coste constante.

        En lugar de interpolar los colores de cientos de widgets frame a
        frame (caro en PCs modestos), tapamos la ventana con un Toplevel
        sin decoración del color del tema nuevo, animamos solo su alpha,
        y mientras el overlay está opaco aplicamos el tema en seco por
        detrás. El usuario ve:

          1. Pantalla actual.
          2. "Cortina" del color del tema nuevo apareciendo (fade-in).
          3. Cortina opaca → cambio brusco invisible.
          4. Cortina desapareciendo (fade-out) revela la UI ya con tema nuevo.

        El coste de la animación es independiente del nº de widgets: solo
        animamos un atributo (-alpha) de un único Toplevel. Si el WM no
        soporta -alpha, caemos al cambio instantáneo (degradación elegante).

        Si el usuario re-dispara un cambio de tema mientras hay una
        animación en curso, cancelamos la anterior y destruimos el overlay
        viejo para no apilar capas.
        """
        # v1.29: el tema es preferencia personal (persiste en user_config),
        # NO estado de sesión. Cambiar tema YA NO marca dirty ni se serializa
        # en el .ocv (ver _build_session_dict y _apply_session).

        # Cancelar animación previa
        prev_id = getattr(self, "_theme_fade_after_id", None)
        if prev_id is not None:
            try:
                self.root.after_cancel(prev_id)
            except Exception:
                pass
            self._theme_fade_after_id = None
        prev_ov = getattr(self, "_theme_fade_overlay", None)
        if prev_ov is not None:
            try:
                prev_ov.destroy()
            except Exception:
                pass
            self._theme_fade_overlay = None

        new_bg = THEMES[name]["BG"]

        # Crear overlay sobre la ventana principal. overrideredirect quita
        # marco, attributes("-topmost", True) garantiza que cubre todo.
        try:
            ov = tk.Toplevel(self.root)
            ov.overrideredirect(True)
            ov.configure(bg=new_bg)
            ov.attributes("-topmost", True)
            ov.attributes("-alpha", 0.0)
        except Exception:
            # WM sin -alpha o sin overrideredirect → fallback a cambio brusco
            self._apply_theme_immediate(name, sync_plot)
            return

        # Dimensionar y posicionar para cubrir EXACTAMENTE la root.
        # No usamos winfo_screen* (sería la pantalla primaria en multi-monitor):
        # winfo_rootx/y/width/height son coords absolutas del WM y funcionan
        # con la app en cualquier monitor.
        try:
            self.root.update_idletasks()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            ov.geometry(f"{rw}x{rh}+{rx}+{ry}")
        except Exception:
            pass

        self._theme_fade_overlay = ov

        # Parámetros de la animación. Total ~160 ms y solo 8 ticks de Tk:
        # mucho más barato que las 6× iteraciones por widget anteriores.
        fade_in_steps  = 4
        fade_out_steps = 4
        step_delay_ms  = 20
        peak_alpha     = 0.97   # pelín por debajo de 1 → no parece freeze

        def fade_in(i=0):
            self._theme_fade_after_id = None
            if i >= fade_in_steps:
                # Overlay opaco: cambio brusco invisible al usuario.
                try:
                    self._apply_theme_immediate(name, sync_plot)
                except Exception:
                    pass
                # Encadenar fade-out
                self._theme_fade_after_id = self.root.after(
                    step_delay_ms, lambda: fade_out(0))
                return
            try:
                ov.attributes("-alpha",
                              peak_alpha * ((i + 1) / fade_in_steps))
            except Exception:
                pass
            self._theme_fade_after_id = self.root.after(
                step_delay_ms, lambda: fade_in(i + 1))

        def fade_out(i=0):
            self._theme_fade_after_id = None
            if i >= fade_out_steps:
                try:
                    ov.destroy()
                except Exception:
                    pass
                self._theme_fade_overlay = None
                return
            try:
                ov.attributes("-alpha",
                              peak_alpha * (1 - (i + 1) / fade_out_steps))
            except Exception:
                pass
            self._theme_fade_after_id = self.root.after(
                step_delay_ms, lambda: fade_out(i + 1))

        fade_in(0)

    def _apply_theme_immediate(self, name, sync_plot=True):
        """Aplica el tema indicado de forma INSTANTÁNEA (sin fade).

        Procedimiento:
            1) apply_theme() reescribe COLOR_* y ttk.Style.
            2) _retheme_tk_widgets() recorre el árbol y actualiza tk.* clásicos.
            3) Si sync_plot: actualiza grid_theme y fuerza update_plot.
            4) Actualiza widgets puntuales que tienen colores en propiedades
               específicas no cubiertas por el barrido (highlight*, etc.).
        """
        if name not in THEMES:
            return

        prev = theme.CURRENT_THEME_NAME
        apply_theme(self.root, name)
        _retheme_tk_widgets(self.root, prev_theme=prev)

        # Toplevels abiertos (FFT, trigger, XY...): el barrido de root ya
        # los recorre (son hijos de root); no repetirlo, una segunda pasada
        # re-mapeaba colores ya convertidos. Aquí solo el refresco ttk.
        try:
            for tl in self.root.winfo_children():
                if isinstance(tl, tk.Toplevel):
                    # ttk.Treeview no se refresca solo aunque el Style cambie.
                    # Truco: cambiar la propiedad `style` a algo trivialmente
                    # distinto y volver fuerza un re-cálculo.
                    self._force_refresh_ttk_in(tl)
        except Exception:
            pass

        if sync_plot:
            t = THEMES[name]
            self.grid_theme["bg"]            = t["PLOT_BG"]
            self.grid_theme["grid_color"]    = t["PLOT_GRID"]
            self.grid_theme["text_color"]    = t["PLOT_TEXT"]
            self.grid_theme["time_label_bg"] = t["PLOT_TIME_LBL_BG"]
            # v1.29: si Appearance está abierta, refrescar swatches del
            # plot grid AHORA. El fade es asíncrono, así que el
            # refresh_swatches inline de choose_theme corría antes de
            # tiempo (con colores residuales del tema anterior).
            try:
                cb = getattr(self, "_appearance_swatch_refresh", None)
                if cb is not None:
                    cb()
            except Exception:
                pass
            # Cambiar el tema GLOBAL invalida las personalizaciones de
            # Advanced (que aplicaban sobre el tema anterior). Eliminamos
            # esa clave del config persistido para no arrastrarlas al
            # próximo arranque — el nuevo tema manda hasta que el usuario
            # personalice de nuevo en Advanced.
            try:
                save_user_config({"grid_theme": None})
            except Exception:
                pass

        # v1.30: repintar el icono tuerca de la top bar. tk.Canvas guarda
        # los items con su color absoluto, así que un cambio de tema sin
        # repintar deja la tuerca con el accent del tema viejo.
        try:
            cb = getattr(self, "_gear_redraw", None)
            if cb is not None:
                cb()
        except Exception:
            pass

        # Panel de canales: los colores de canal se ajustan al fondo del
        # tema (readable_on) y el remapeo genérico no los conoce
        try:
            self._rebuild_channels_panel()
            self._rebuild_measurements_panel()
        except Exception:
            pass

        # Toggle Detail/Fast: sus colores dependen del modo, el remapeo
        # genérico no los conoce (activebackground quedaba del tema viejo)
        try:
            self._update_render_mode_btn_visual()
        except Exception:
            pass

        # Re-render del plot principal con los nuevos colores
        try:
            self.update_plot()
        except Exception:
            pass

        # Notificar a las ventanas secundarias con plot propio (FFT, Eye)
        # para que repinten con el nuevo tema. Son singletons gestionados
        # por open_*_window, así que cualquier instancia abierta está en
        # self._fft_window / self._eye_window. La asociación entre window
        # (Toplevel) y su instancia (FFTWindow/EyeDiagramWindow) se hace
        # vía un dict que mantenemos paralelo a las refs de Toplevel.
        for ref_attr in ("_fft_instance", "_eye_instance"):
            inst = getattr(self, ref_attr, None)
            if inst is None:
                continue
            try:
                if hasattr(inst, "win") and inst.win.winfo_exists():
                    if hasattr(inst, "_redraw"):
                        inst._redraw()
            except Exception:
                pass

        # Persistir entre arranques (silenciosamente — no romper si falla)
        try:
            save_user_config({"app_theme": name})
        except Exception:
            pass

        self._show_toast(f"Theme: {name}")
