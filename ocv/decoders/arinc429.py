"""ARINC 429 decoder."""
from ocv.decoders.base import (
    _binarize,
    _find_edges,
    _sample_at,
    ProtocolDecoder,
)


# -----------------------------
# ARINC 429 (avionics bus)
# -----------------------------
class Arinc429Decoder(ProtocolDecoder):
    """ARINC 429 — bus aviónico unidireccional broadcast.

    Palabra de 32 bits a 12.5 kbit/s (low speed) o 100 kbit/s (high speed),
    codificación bipolar return-to-zero (BPRZ). Aquí asumimos señal ya
    recuperada a TTL lógico — el usuario suministra la señal binaria.

    Estructura del campo de 32 bits (orden de transmisión, LSB primero):
      [1..8]   Label (8 bits)            — identificador del tipo de dato
      [9..10]  SDI (2 bits)              — Source/Destination Identifier
      [11..29] Data field (19 bits)
      [30..31] SSM (2 bits)              — Sign/Status Matrix
      [32]     Parity (1 bit, impar)

    El frame se separa por un gap mínimo de 4 bit-times (idle).
    """

    name = "ARINC 429"
    description = (
        "ARINC 429 avionics bus. 32-bit words at 12.5 or 100 kbit/s.\n"
        "Campos: Label · SDI · Data · SSM · Parity.\n"
        "Asume señal recuperada a TTL (idle = 0)."
    )
    channels_needed = ["A429"]

    @classmethod
    def default_params(cls):
        return {
            "bit_rate":  {"label": "Bit rate",
                          "default": "Low (12.5 kbit/s)",
                          "kind": "choice",
                          "options": ["Low (12.5 kbit/s)",
                                      "High (100 kbit/s)",
                                      "Custom"]},
            "bit_rate_custom": {"label": "  Custom (b/s)",
                                "default": 12500, "kind": "int"},
            "label_format": {"label": "Label format",
                             "default": "Octal",
                             "kind": "choice",
                             "options": ["Octal", "Hex", "Decimal"]},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        v = channel_voltages["A429"]
        rate_choice = str(params.get("bit_rate", "Low (12.5 kbit/s)"))
        if rate_choice.startswith("Low"):
            rate = 12500.0
        elif rate_choice.startswith("High"):
            rate = 100000.0
        else:
            rate = float(params.get("bit_rate_custom", 12500))
        bit_time = 1.0 / rate

        b = _binarize(time_arr, v)
        edges = _find_edges(b, time_arr, "rising")
        frames = []
        if len(edges) < 2:
            return frames

        lbl_fmt = str(params.get("label_format", "Octal"))

        i = 0
        while i < len(edges):
            # Buscar inicio de palabra: gap previo de ≥4 bit_times
            e_idx = int(edges[i])
            t_start = time_arr[e_idx]
            if i > 0:
                t_prev = time_arr[int(edges[i - 1])]
                if (t_start - t_prev) < 3.5 * bit_time:
                    # No es inicio de palabra (parte de la palabra anterior)
                    i += 1
                    continue
            # Muestrear 32 bits
            bits = []
            for k in range(32):
                t_s = t_start + (k + 0.5) * bit_time
                bits.append(int(_sample_at(b, time_arr, t_s)))

            # Reconstruir campos (LSB primero → bits[0]=LSB del label)
            def slice_bits(start, n):
                val = 0
                for j in range(n):
                    val |= (bits[start + j] & 1) << j
                return val

            label_raw = slice_bits(0, 8)
            sdi  = slice_bits(8, 2)
            data = slice_bits(10, 19)
            ssm  = slice_bits(29, 2)
            parity = bits[31]
            ones = sum(bits)
            parity_ok = (ones % 2) == 1  # paridad impar

            # Formatear label
            if lbl_fmt == "Octal":
                # ARINC convencional: octal
                label_str = f"{label_raw:03o}"
            elif lbl_fmt == "Hex":
                label_str = f"0x{label_raw:02X}"
            else:
                label_str = str(label_raw)

            t_end = t_start + 32 * bit_time
            frames.append({
                "t_start": t_start,
                "t_end":   t_end,
                "type":    "word",
                "value":   (label_raw, sdi, data, ssm, parity),
                "label":   (f"L={label_str} SDI={sdi} "
                           f"D=0x{data:05X} SSM={ssm}"
                           + ("" if parity_ok else " ⚠P")),
                "color":   "#5fa8ff" if parity_ok else "#cc4444",
                "error":   not parity_ok,
            })

            # Avanzar past este frame
            while i < len(edges) and time_arr[int(edges[i])] < t_end:
                i += 1
        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama ARINC 429: tabla de bits 32..1 con campos
        Parity · SSM · Data · SDI · Label."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 50, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10,
                       "ARINC 429  (32-bit word, LSB first transmitted)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")
        cls._dgm_label(canvas, x + 10, y + 28,
                       "Field layout (bit numbering ARINC):",
                       theme["dim"],
                       font=("Segoe UI", 8), anchor="w")

        # Convención ARINC: bit 32 (MSB) primero en la diagrama (izda),
        # bit 1 (LSB) último — coincide con el panel R&S.
        weights = [
            ("P\n32", 1, "ok"),
            ("SSM\n31-30", 2, "ctrl"),
            ("Data field\n29-11", 19, "data"),
            ("SDI\n10-9", 2, "ctrl"),
            ("Label\n8-1", 8, "accent"),
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
                         label="32-bit word",
                         text_color=theme["fg"])

        return True
