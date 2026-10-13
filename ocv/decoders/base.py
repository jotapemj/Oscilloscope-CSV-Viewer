"""Protocol decoder base class and shared bit helpers."""
import numpy as np


# =====================================================
# Protocol Decoders
# =====================================================
# Arquitectura:
#   - ProtocolDecoder (clase base abstracta): declara interface y helpers.
#   - Subclases concretas: UartDecoder, I2CDecoder, SPIDecoder, ...
#   - DECODER_REGISTRY: lista de subclases disponibles. Añadir un decoder
#     nuevo es crear la subclase y añadirla aquí; no se toca la UI.
#
# Cada decode() devuelve una lista de Frame dicts:
#   {"t_start": float, "t_end": float, "type": str, "value": int|str,
#    "label": str, "color": str, "error": bool}
#
# La UI (DecoderWindow) los muestra como tabla y se los pasa a la app
# principal para que los pinte bajo la traza correspondiente.


def _binarize(time_arr, voltage_arr, threshold=None, hysteresis=0.0):
    """Convierte una señal analógica en estados binarios (0/1) usando un
    threshold con histéresis opcional. Devuelve un array uint8 de la misma
    longitud que voltage_arr.

    Threshold por defecto: punto medio entre los percentiles 10 y 90 de la
    señal — robusto frente a outliers y ruido transitorio.

    La histéresis reduce los falsos cambios por ruido cerca del threshold.
    Si hysteresis=0, equivale a un comparador simple.
    """
    v = voltage_arr
    if threshold is None:
        p_lo = float(np.quantile(v, 0.10))
        p_hi = float(np.quantile(v, 0.90))
        threshold = (p_lo + p_hi) / 2.0
    if hysteresis <= 0:
        return (v > threshold).astype(np.uint8)
    # Schmitt trigger software
    hi = threshold + hysteresis / 2.0
    lo = threshold - hysteresis / 2.0
    out = np.empty(len(v), dtype=np.uint8)
    state = 1 if v[0] > threshold else 0
    for i, x in enumerate(v):
        if state == 0 and x > hi:
            state = 1
        elif state == 1 and x < lo:
            state = 0
        out[i] = state
    return out


def _find_edges(binary_arr, time_arr, kind="any"):
    """Devuelve los índices donde el binario cambia.
    kind: 'rising' | 'falling' | 'any'. Los índices apuntan a la POSICIÓN
    POST-cambio (primer punto del nuevo estado).
    """
    diff = np.diff(binary_arr.astype(np.int8))
    if kind == "rising":
        idx = np.where(diff > 0)[0] + 1
    elif kind == "falling":
        idx = np.where(diff < 0)[0] + 1
    else:
        idx = np.where(diff != 0)[0] + 1
    return idx


def _sample_at(binary_arr, time_arr, t_target):
    """Devuelve el valor binario muestreado en t_target (búsqueda binaria).
    Si t_target está fuera de rango, devuelve el extremo más cercano."""
    if t_target <= time_arr[0]:
        return int(binary_arr[0])
    if t_target >= time_arr[-1]:
        return int(binary_arr[-1])
    i = int(np.searchsorted(time_arr, t_target))
    if i >= len(binary_arr):
        i = len(binary_arr) - 1
    return int(binary_arr[i])


class ProtocolDecoder:
    """Base abstracta para decoders de protocolo.

    Sub-clases definen:
      - name:              str visible en la UI.
      - description:       tooltip corto.
      - channels_needed:   list[str] de roles OBLIGATORIOS.
      - channels_optional: list[str] de roles OPCIONALES (default []).
                           Si el usuario no los mapea, el decoder debe
                           tolerarlo (None llegará en channel_voltages).
      - default_params():  dict de parámetros configurables.
      - decode():          lógica de decodificación.
      - draw_diagram():    (opcional) dibuja un diagrama explicativo del
                           frame en un tk.Canvas. Devuelve True si dibujó
                           algo, False si no implementado.

    channels_optional permite, por ejemplo, que SPI funcione con solo MOSI
    (sniff half-duplex), o que SDIO acepte DAT1-3 sólo si vas a 4-bit.
    """

    name = "Base"
    description = ""
    channels_needed = []          # ["TX"] / ["SDA", "SCL"] / ...
    channels_optional = []        # roles opcionales — pueden venir como None

    @classmethod
    def default_params(cls):
        """Devuelve dict de parámetros por defecto. Cada uno con
        (label, default, kind, options). kind: 'int', 'float', 'choice', 'bool'."""
        return {}

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        """Implementación específica. channel_voltages es un dict
        {role: np.array | None}. Los roles en channels_needed están
        garantizados como np.array; los de channels_optional pueden ser
        None si el usuario no los mapeó. Devuelve lista de Frame dicts."""
        raise NotImplementedError

    # =====================================================
    # Diagrama explicativo del frame
    # =====================================================
    # Cada decoder puede sobreescribir draw_diagram() para pintar una
    # explicación visual del formato de su trama. Se muestra en la
    # DecoderWindow y ayuda al usuario a entender qué hace cada bit.
    #
    # Convención: las subclases usan los helpers _dgm_* (definidos abajo)
    # para mantener consistencia visual entre protocolos.
    #
    # `theme` es un dict con claves:
    #   "bg":     color de fondo del canvas
    #   "fg":     color de texto/líneas principal
    #   "dim":    color secundario (etiquetas, ejes)
    #   "accent": color de énfasis (cajas activas)
    #   "ok":     verde (campos válidos)
    #   "err":    rojo (campos de error)
    #   "data":   azul (campos de datos)
    #   "ctrl":   gris (campos de control)
    #   "border": color de bordes
    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Dibuja el diagrama del frame del protocolo dentro del rectángulo
        (x, y, x+w, y+h) del canvas. Devuelve True si dibujó algo.
        Por defecto: no hace nada (el decoder no expone diagrama)."""
        return False

    # -----------------------------
    # Helpers de dibujo reutilizables
    # -----------------------------
    # Diseñados para que cada decoder no reinvente la rueda y los
    # diagramas mantengan estilo consistente (estilo R&S: cajas con
    # etiqueta, marcas verticales discontinuas, flechas bidi).
    @staticmethod
    def _dgm_box(canvas, x0, y0, x1, y1, label="", fill="", outline=None,
                 text_color=None, font=("Segoe UI", 8, "bold")):
        """Caja rectangular con etiqueta centrada."""
        canvas.create_rectangle(x0, y0, x1, y1, fill=fill,
                                outline=outline, width=1)
        if label:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                               text=label, fill=text_color, font=font)

    @staticmethod
    def _dgm_signal_step(canvas, points, color, width=2):
        """Pinta una señal digital como línea quebrada. `points` es lista
        de (x, y). Ideal para dibujar trazas idle→start→bits→stop."""
        if len(points) < 2:
            return
        flat = []
        for px, py in points:
            flat.extend([px, py])
        canvas.create_line(*flat, fill=color, width=width,
                           capstyle="round", joinstyle="miter")

    @staticmethod
    def _dgm_label(canvas, x, y, text, color, font=("Segoe UI", 8),
                   anchor="center"):
        """Etiqueta de texto."""
        return canvas.create_text(x, y, text=text, fill=color,
                                  font=font, anchor=anchor)

    @staticmethod
    def _dgm_arrow_h(canvas, x0, x1, y, color, label="", text_color=None,
                     font=("Segoe UI", 7)):
        """Flecha horizontal bidireccional con etiqueta opcional encima."""
        canvas.create_line(x0, y, x1, y, fill=color, width=1,
                           arrow="both", arrowshape=(6, 7, 3))
        if label:
            canvas.create_text((x0 + x1) / 2, y - 8,
                               text=label, fill=text_color or color,
                               font=font)

    @staticmethod
    def _dgm_tick(canvas, x, y_top, y_bot, color, label="", text_color=None,
                  font=("Segoe UI", 7), label_above=True, dash=(2, 2)):
        """Línea vertical (opcionalmente discontinua) con etiqueta arriba/abajo."""
        canvas.create_line(x, y_top, x, y_bot, fill=color, width=1, dash=dash)
        if label:
            ty = y_top - 6 if label_above else y_bot + 8
            anchor = "s" if label_above else "n"
            canvas.create_text(x, ty, text=label, fill=text_color or color,
                               font=font, anchor=anchor)

    @staticmethod
    def _dgm_field_row(canvas, x0, y0, fields, h, theme,
                       font=("Segoe UI", 8, "bold")):
        """Fila de cajas con etiqueta (estilo R&S CAN/LIN/MIL-1553/ARINC).

        `fields` es una lista de tuplas (label, width_px, color_key) donde
        color_key es una clave del dict theme (p.ej. 'data', 'ctrl', 'ok',
        'err', 'accent'). Cada caja tiene contorno coloreado y texto del
        mismo color centrado dentro. Devuelve la x final (x0 + sum widths).
        """
        cursor = x0
        for label, w, ck in fields:
            col = theme.get(ck, theme.get("data", "#5fa8ff"))
            canvas.create_rectangle(cursor, y0, cursor + w, y0 + h,
                                    fill="", outline=col, width=1.5)
            canvas.create_text(cursor + w / 2, y0 + h / 2,
                               text=label, fill=col, font=font)
            cursor += w
        return cursor

    @staticmethod
    def _dgm_rail_label(canvas, x, y_center, label, theme,
                        font=("Segoe UI", 8, "bold")):
        """Etiqueta a la izquierda de un raíl (SCL, SDA, CLK, MOSI…).

        Encapsula el patrón típico: texto en color accent, centrado vertical
        respecto al raíl. Útil para diagramas multi-raíl como I2C/SPI/I2S."""
        canvas.create_text(x, y_center, text=label,
                           fill=theme.get("accent", "#4cc2ff"),
                           font=font, anchor="e")
