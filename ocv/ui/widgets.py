"""Small reusable Tk widgets."""
import tkinter as tk

from ocv.ui import theme


# =====================================================
# Tooltip widget reutilizable
# =====================================================
class Tooltip:
    """Tooltip ligero para widgets Tk.

    Uso:
        Tooltip(btn, "Texto del tooltip")
        Tooltip(spinbox, "Línea 1\\nLínea 2", delay_ms=400)

    Comportamiento:
    - Aparece tras `delay_ms` desde el <Enter>.
    - Desaparece al <Leave>, <ButtonPress>, o destruir el widget.
    - Se posiciona cerca del cursor; si se saldría de la pantalla por la
      derecha o por abajo, se reajusta.
    - Lee COLOR_BG_ALT/COLOR_TEXT/COLOR_BORDER del módulo en el momento de
      mostrarse, no del constructor: así respeta el tema activo aunque se
      cambie entre uno y otro mientras la app está corriendo.
    - Soporta texto dinámico mediante `set_text(...)` o pasando un callable
      al constructor (se llama justo antes de mostrar).

    Decisión: usar Toplevel sin decoración en vez de Canvas/Label dentro del
    widget. Es más limpio, soporta wrap automático con `wraplength`, y se
    posiciona libremente sobre la ventana.
    """

    # Mantenemos un set global de tooltips activos para poder ocultarlos
    # todos a la vez si fuera necesario (debug, abrir modal, etc.).
    _ALL = set()

    def __init__(self, widget, text, delay_ms=500, wraplength=320):
        self.widget = widget
        self._text = text                  # str o callable que devuelve str
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tip = None                   # Toplevel cuando está visible
        self._after_id = None              # id del after pendiente
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")
        Tooltip._ALL.add(self)

    # Permite cambiar el texto en runtime (p.ej. tooltip dinámico de "Cursor: ON/OFF")
    def set_text(self, text):
        self._text = text
        if self._tip is not None:
            # Actualizar in-place si está visible
            try:
                self._label.config(text=self._resolve_text())
            except Exception:
                pass

    def _resolve_text(self):
        if callable(self._text):
            try:
                return str(self._text())
            except Exception:
                return ""
        return str(self._text)

    def _on_enter(self, _e):
        self._cancel_pending()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _e):
        self._cancel_pending()
        self._hide()

    def _on_destroy(self, _e):
        self._cancel_pending()
        self._hide()
        Tooltip._ALL.discard(self)

    def _cancel_pending(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        text = self._resolve_text()
        if not text:
            return
        if self._tip is not None:
            return

        # Crear toplevel
        try:
            tip = tk.Toplevel(self.widget)
        except Exception:
            return
        tip.wm_overrideredirect(True)
        # En algunos WMs hace falta forzar topmost para que aparezca encima
        # de los Toplevels modales (grab_set).
        try:
            tip.wm_attributes("-topmost", True)
        except Exception:
            pass

        # Colocar fuera de pantalla mientras se mide
        tip.wm_geometry("+0+0")
        tip.configure(bg=theme.COLOR_BORDER)  # actúa como borde

        lbl = tk.Label(
            tip, text=text,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9),
            justify="left",
            padx=8, pady=4,
            wraplength=self.wraplength,
        )
        lbl.pack(padx=1, pady=1)   # 1 px gap → borde fino

        # Posicionar: cerca del cursor, ajustando si se sale del escritorio
        # virtual (la unión de todos los monitores). winfo_screenwidth/height
        # devuelve SIEMPRE la pantalla primaria, así que clampar contra eso
        # empuja el tooltip al monitor primario cuando el cursor está en uno
        # secundario. Usamos winfo_vrootx/y/width/height (X virtual root) que
        # cubre el escritorio completo en setups multimonitor.
        try:
            px = self.widget.winfo_pointerx()
            py = self.widget.winfo_pointery()
            x = px + 14
            y = py + 18
            tip.update_idletasks()
            w = tip.winfo_reqwidth()
            h = tip.winfo_reqheight()
            # Escritorio virtual (todos los monitores)
            vrx = self.widget.winfo_vrootx()
            vry = self.widget.winfo_vrooty()
            vrw = self.widget.winfo_vrootwidth()
            vrh = self.widget.winfo_vrootheight()
            right_edge  = vrx + vrw
            bottom_edge = vry + vrh
            if x + w > right_edge - 4:
                x = right_edge - w - 4
            if y + h > bottom_edge - 4:
                # Subir el tooltip por encima del cursor
                y = py - h - 6
            # No dejar el tooltip fuera del escritorio virtual por la izquierda
            # o por arriba (puede pasar con monitores con offsets negativos).
            if x < vrx + 4:
                x = vrx + 4
            if y < vry + 4:
                y = vry + 4
            tip.wm_geometry(f"+{x}+{y}")
        except Exception:
            pass

        self._tip = tip
        self._label = lbl

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
            self._label = None
