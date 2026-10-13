"""MIL-STD-1553 decoder."""
from ocv.decoders.base import (
    _binarize,
    _find_edges,
    _sample_at,
    ProtocolDecoder,
)


# -----------------------------
# MIL-STD-1553 (avionics / military data bus)
# -----------------------------
class MilStd1553Decoder(ProtocolDecoder):
    """MIL-STD-1553B básico — decodifica Manchester II y delimita por
    sync de comando vs sync de data.

    Implementación simplificada (objetivo: visualizar palabras del bus,
    no validar todo el protocolo):
      - Sync 3 bits (1.5 bit-times alto + 1.5 bit-times bajo = command/status,
        invertido = data).
      - 16 bits de payload codificados Manchester II
        (transición a media bit: 0=alto→bajo, 1=bajo→alto, NRZ-L invertido).
      - Parity bit (impar, 1 bit).
    Total: 20 bit-times por palabra.

    El bit rate fijado por la spec es 1 Mbit/s. El usuario lo puede ajustar
    para señales recuperadas/escaladas.

    Notas:
      - Espera la señal ya decodificada a TTL lógico (recuperada del par
        diferencial). No demodula la portadora.
      - No diferencia transmitter/receiver address — solo extrae los 16
        bits y el tipo (CMD/STAT vs DATA).
    """

    name = "MIL-STD-1553"
    description = (
        "MIL-STD-1553B avionics bus (1 Mbit/s, Manchester II).\n"
        "Decodifica el sync, los 16 bits de palabra y la paridad.\n"
        "Diferencia palabras de Comando/Status vs Data por la polaridad del sync."
    )
    channels_needed = ["BUS"]

    @classmethod
    def default_params(cls):
        return {
            "bit_rate":   {"label": "Bit rate (b/s)",
                           "default": 1_000_000, "kind": "int"},
            "sync_polarity": {"label": "Sync detection",
                              "default": "Auto",
                              "kind": "choice",
                              "options": ["Auto",
                                          "CMD/STAT first",
                                          "DATA first"]},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        """Decodificador básico Manchester. Identifica syncs por su
        duración (~3 bits = 3 * 1/bit_rate) y luego muestrea 16+1 bits
        a media-bit en los flancos esperados."""
        v = channel_voltages["BUS"]
        bit_time = 1.0 / float(params.get("bit_rate", 1_000_000))
        b = _binarize(time_arr, v)
        edges = _find_edges(b, time_arr, "any")
        frames = []
        if len(edges) < 3:
            return frames

        # Detección heurística de syncs: dos intervalos consecutivos de
        # ~1.5 * bit_time del mismo nivel.
        i = 0
        while i < len(edges) - 1:
            e0 = int(edges[i])
            e1 = int(edges[i + 1])
            dt = time_arr[e1] - time_arr[e0]
            # Sync candidato: pulso de ~1.5 bits
            if 1.2 * bit_time < dt < 1.8 * bit_time:
                level_first = int(b[e0])
                t_sync = time_arr[e0]
                # 16 bits de payload + 1 parity
                payload = 0
                t_cur = t_sync + 3 * bit_time  # final del sync
                for k in range(16):
                    t_s = t_cur + (k + 0.75) * bit_time
                    bit = _sample_at(b, time_arr, t_s)
                    payload = (payload << 1) | int(bit)
                t_par = t_cur + 16.75 * bit_time
                parity_bit = _sample_at(b, time_arr, t_par)
                ones = bin(payload).count("1") + int(parity_bit)
                parity_ok = (ones % 2) == 1  # paridad impar
                wtype = "CMD/STAT" if level_first == 1 else "DATA"
                col = "#ffb84d" if wtype == "CMD/STAT" else "#5fa8ff"
                frames.append({
                    "t_start": t_sync,
                    "t_end":   t_par + bit_time,
                    "type":    "word",
                    "value":   payload,
                    "label":   f"{wtype} 0x{payload:04X}"
                              + ("" if parity_ok else " ⚠P"),
                    "color":   col,
                    "error":   not parity_ok,
                })
                # Avanzar índice de edges hasta después del último bit
                t_done = t_par + bit_time
                while i < len(edges) and time_arr[int(edges[i])] < t_done:
                    i += 1
                continue
            i += 1
        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama MIL-STD-1553: Sync · 16 b · Parity. Manchester II."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 28, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10,
                       "MIL-STD-1553  (Manchester II · 1 Mbit/s)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        # 3 bits sync + 16 data + 1 parity = 20 bit-times
        weights = [
            ("Sync\n(3 bt)", 3.0, "err"),
            ("Data / Command\n(16 bits)", 16.0, "data"),
            ("P\n(1)", 1.0, "ok"),
        ]
        total = sum(w for _, w, _ in weights)
        unit = plot_w / total

        cursor = x0
        for label, width, ck in weights:
            bw = width * unit
            col = theme.get(ck, theme["data"])
            canvas.create_rectangle(cursor, box_y, cursor + bw,
                                    box_y + box_h, outline=col, width=1.5)
            canvas.create_text(cursor + bw / 2, box_y + box_h / 2,
                               text=label, fill=col,
                               font=("Segoe UI", 8, "bold"),
                               justify="center")
            cursor += bw

        cls._dgm_arrow_h(canvas, x0, x0 + total * unit,
                         box_y + box_h + 12, theme["dim"],
                         label="20 bit-times / word",
                         text_color=theme["fg"])

        return True
