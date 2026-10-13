"""UART / RS-232 / RS-422 / RS-485 decoder."""
import numpy as np

from ocv.decoders.base import (
    _binarize,
    _find_edges,
    _sample_at,
    ProtocolDecoder,
)


# -----------------------------
# UART
# -----------------------------
class UartDecoder(ProtocolDecoder):
    """UART async serial.

    Convención: idle = nivel alto, start = bit bajo, N data bits LSB primero,
    parity opcional, stop bit(s) alto(s).
    """

    name = "UART / RS-232 / RS-422 / RS-485"
    description = (
        "Async serial. A nivel de bits es idéntico para UART TTL, RS-232, "
        "RS-422 y RS-485 — solo cambia la capa física (niveles y "
        "diferencial). Captura la señal ya recuperada en lógica TTL.\n"
        "RS-232 invertido respecto a TTL: marcar 'Inverted' si vienes con "
        "sonda directa en línea RS-232 (sin transceiver MAX232)."
    )
    channels_needed = ["TX"]

    @classmethod
    def default_params(cls):
        return {
            "standard":  {"label": "Standard",  "default": "UART (TTL)",
                          "kind": "choice",
                          "options": ["UART (TTL)", "RS-232", "RS-422",
                                      "RS-485"]},
            "baud":      {"label": "Baud rate", "default": 9600,    "kind": "int"},
            "data_bits": {"label": "Data bits", "default": 8,       "kind": "choice",
                          "options": [5, 6, 7, 8, 9]},
            "parity":    {"label": "Parity",    "default": "none",  "kind": "choice",
                          "options": ["none", "even", "odd"]},
            "stop_bits": {"label": "Stop bits", "default": 1,       "kind": "choice",
                          "options": [1, 2]},
            "msb_first": {"label": "MSB first", "default": False,   "kind": "bool"},
            "invert":    {"label": "Inverted",  "default": False,   "kind": "bool"},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        v = channel_voltages["TX"]
        baud = max(1, int(params["baud"]))
        data_bits = int(params["data_bits"])
        parity = params["parity"]
        stop_bits = int(params["stop_bits"])
        msb_first = bool(params["msb_first"])
        invert = bool(params["invert"])

        # Histéresis ~10% del swing — robusta frente a ruido cerca del threshold.
        # Sin esto, una señal ruidosa produce centenares de cruces espurios.
        p_lo = float(np.quantile(v, 0.10))
        p_hi = float(np.quantile(v, 0.90))
        hyst = (p_hi - p_lo) * 0.10
        b = _binarize(time_arr, v, hysteresis=hyst)
        if invert:
            b = (1 - b).astype(np.uint8)

        bit_time = 1.0 / baud
        frames = []

        # Buscar flancos de bajada (start bits)
        # Idle high → start bit es flanco descendente
        falling = _find_edges(b, time_arr, "falling")
        i = 0
        while i < len(falling):
            idx_start = falling[i]
            t_start = time_arr[idx_start]
            # Muestrear el start bit en su mitad
            t_mid_start = t_start + bit_time * 0.5
            if _sample_at(b, time_arr, t_mid_start) != 0:
                # Falso start (glitch) — saltar
                i += 1
                continue

            # Decodificar data_bits
            bits = []
            for k in range(data_bits):
                t_sample = t_start + bit_time * (1.5 + k)
                bits.append(_sample_at(b, time_arr, t_sample))

            if msb_first:
                bits = bits[::-1]
            # Reconstruir valor (LSB first por defecto)
            value = 0
            for k, bit in enumerate(bits):
                value |= (bit & 1) << k

            t_data_end = t_start + bit_time * (1 + data_bits)

            # Parity
            parity_ok = True
            if parity != "none":
                t_parity = t_start + bit_time * (1.5 + data_bits)
                p_bit = _sample_at(b, time_arr, t_parity)
                ones = bin(value).count("1") + p_bit
                if parity == "even":
                    parity_ok = (ones % 2 == 0)
                else:  # odd
                    parity_ok = (ones % 2 == 1)
                t_data_end += bit_time

            # Stop bits
            stop_ok = True
            for k in range(stop_bits):
                t_stop_sample = t_data_end + bit_time * (0.5 + k)
                if _sample_at(b, time_arr, t_stop_sample) != 1:
                    stop_ok = False
                    break
            t_frame_end = t_data_end + bit_time * stop_bits

            # Construir frames legibles
            frames.append({
                "t_start": t_start,
                "t_end":   t_start + bit_time,
                "type":    "start",
                "value":   0,
                "label":   "S",
                "color":   "#7fdb7f",
                "error":   False,
            })
            frames.append({
                "t_start": t_start + bit_time,
                "t_end":   t_data_end,
                "type":    "data",
                "value":   value,
                "label":   cls._format_byte(value),
                "color":   "#5fa8ff",
                "error":   False,
            })
            if parity != "none":
                frames.append({
                    "t_start": t_data_end - bit_time,
                    "t_end":   t_data_end,
                    "type":    "parity",
                    "value":   int(parity_ok),
                    "label":   "P" if parity_ok else "!P",
                    "color":   "#cc99ff" if parity_ok else "#ff6b6b",
                    "error":   not parity_ok,
                })
            frames.append({
                "t_start": t_data_end,
                "t_end":   t_frame_end,
                "type":    "stop",
                "value":   int(stop_ok),
                "label":   "T" if stop_ok else "!T",
                "color":   "#888888" if stop_ok else "#ff6b6b",
                "error":   not stop_ok,
            })

            # Avanzar tras el frame para no detectar el mismo start
            # (saltar al siguiente flanco DENTRO del frame; el flanco que
            # marca el siguiente start, si cae justo al final del stop, debe
            # quedar disponible). Usamos strict < y un pequeño margen para
            # tolerar errores de sincronización.
            cutoff = t_frame_end - bit_time * 0.5
            while i < len(falling) and time_arr[falling[i]] < cutoff:
                i += 1
            # Si el siguiente flanco está muy cerca del t_frame_end (siguiente
            # start "pegado"), lo mantenemos para la próxima iteración.
            # Si no avanzamos nada (puntero atascado), forzar avance:
            if i < len(falling) and time_arr[falling[i]] <= t_start:
                i += 1

        return frames

    @staticmethod
    def _format_byte(v):
        """Formato legible: hex + ASCII si imprimible."""
        if 0x20 <= v <= 0x7e:
            return f"0x{v:02X} '{chr(v)}'"
        return f"0x{v:02X}"

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama UART: idle → start → 8 data bits (LSB..MSB) →
        stop bit → idle. Estilo R&S."""
        # Márgenes internos
        pad_l, pad_r = 50, 20
        pad_t, pad_b = 30, 30
        x0 = x + pad_l
        x1 = x + w - pad_r
        y_hi = y + pad_t              # nivel alto (idle / stop / bit=1)
        y_lo = y + h - pad_b          # nivel bajo (start / bit=0)

        plot_w = max(50, x1 - x0)

        # Estructura visual: idle | start | 8 datos | stop | idle
        # Anchos relativos (en "bits"): idle=1.5, start=1, datos=8x1, stop=1.5, idle=1.5
        # El patrón de bits 1,0,1,0,1,0,1,0 (LSB first) da una onda cuadrada
        # bonita y fácil de leer; es solo ilustrativo.
        segments = [
            ("idle",  1.5, True,  ""),
            ("start", 1.0, False, "Start"),
            ("d0",    1.0, True,  "LSB"),
            ("d1",    1.0, False, ""),
            ("d2",    1.0, True,  ""),
            ("d3",    1.0, False, ""),
            ("d4",    1.0, True,  ""),
            ("d5",    1.0, False, ""),
            ("d6",    1.0, True,  ""),
            ("d7",    1.0, False, "MSB"),
            ("stop",  1.5, True,  "Stop"),
            ("idle",  1.5, True,  ""),
        ]
        total = sum(s[1] for s in segments)
        unit = plot_w / total

        # Título arriba a la izquierda
        cls._dgm_label(canvas, x + 10, y + 10,
                       "UART frame  (idle high)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        # Construir línea de señal
        pts = []
        cursor = x0
        prev_hi = True
        for name, span, is_high, _label in segments:
            seg_w = span * unit
            this_hi = is_high
            y_here = y_hi if this_hi else y_lo
            # transición vertical desde el nivel previo
            if pts:
                pts.append((cursor, y_hi if prev_hi else y_lo))
                pts.append((cursor, y_here))
            else:
                pts.append((cursor, y_here))
            pts.append((cursor + seg_w, y_here))
            cursor += seg_w
            prev_hi = this_hi
        cls._dgm_signal_step(canvas, pts, theme["fg"], width=2)

        # Marcas verticales discontinuas + etiquetas
        cursor = x0
        for name, span, is_high, label in segments:
            seg_w = span * unit
            cx = cursor + seg_w / 2
            if label:
                cls._dgm_label(canvas, cx, y_hi - 8, label,
                               theme["accent"], font=("Segoe UI", 8, "bold"))
            # Marcas en bordes de campos no-idle
            if name in ("start", "stop") or name.startswith("d"):
                canvas.create_line(cursor, y_hi - 18, cursor, y_lo + 6,
                                   fill=theme["dim"], width=1, dash=(2, 2))
            cursor += seg_w
        # Cierre del stop (posición fin del stop = idle+start+8data+stop)
        stop_end = x0 + (1.5 + 1.0 + 8.0 + 1.5) * unit
        canvas.create_line(stop_end, y_hi - 18, stop_end, y_lo + 6,
                           fill=theme["dim"], width=1, dash=(2, 2))

        # Flecha "Frame" abarcando start → stop
        start_x = x0 + 1.5 * unit
        cls._dgm_arrow_h(canvas, start_x, stop_end, y_lo + 22,
                         theme["dim"], label="Frame",
                         text_color=theme["fg"])

        # Etiquetas de nivel a la izquierda
        cls._dgm_label(canvas, x + 8, y_hi, "H", theme["dim"], anchor="w")
        cls._dgm_label(canvas, x + 8, y_lo, "L", theme["dim"], anchor="w")

        return True
