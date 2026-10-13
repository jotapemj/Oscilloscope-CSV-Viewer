"""I2S decoder."""
from ocv.decoders.base import _binarize, _find_edges, ProtocolDecoder


# -----------------------------
# I²S (Inter-IC Sound)
# -----------------------------
class I2SDecoder(ProtocolDecoder):
    """I²S audio bus. 3 señales: BCLK (bit clock), WS / LRCLK (word select,
    L = bajo, R = alto en Philips estándar), SDATA (datos serie).

    Soporta tres alineaciones:
      - Philips standard: dato empieza 1 BCLK tras flanco de WS
      - Left-justified:    dato empieza en el mismo flanco de WS
      - Right-justified:   dato termina en el siguiente flanco de WS
    """

    name = "I²S"
    description = (
        "Inter-IC Sound. 3 hilos: BCLK + WS (Word Select / LRCLK) + SDATA. "
        "Datos MSB first. WS bajo → canal izquierdo, alto → derecho "
        "(Philips). Soporta Philips std, left-justified, right-justified. "
        "Word size típico 16, 24 o 32 bits."
    )
    channels_needed = ["BCLK", "WS", "SDATA"]

    @classmethod
    def default_params(cls):
        return {
            "word_bits":  {"label": "Word bits", "default": 16,
                           "kind": "choice",
                           "options": [8, 16, 18, 20, 24, 32]},
            "format":     {"label": "Format",    "default": "Philips",
                           "kind": "choice",
                           "options": ["Philips", "Left-justified",
                                       "Right-justified"]},
            "ws_polarity": {"label": "WS low =", "default": "Left",
                            "kind": "choice",
                            "options": ["Left", "Right"]},
            "show_signed": {"label": "Show signed", "default": True,
                            "kind": "bool"},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        bclk = _binarize(time_arr, channel_voltages["BCLK"])
        ws = _binarize(time_arr, channel_voltages["WS"])
        sdata = _binarize(time_arr, channel_voltages["SDATA"])

        word_bits = int(params["word_bits"])
        fmt = params["format"]
        ws_low_is_left = (params["ws_polarity"] == "Left")
        show_signed = bool(params["show_signed"])

        # I²S: dato muestreado en rising edge de BCLK
        sample_edges = _find_edges(bclk, time_arr, "rising")
        if len(sample_edges) == 0:
            return []

        # Flancos de WS — cada uno arranca un word (con offset según format)
        ws_edges = _find_edges(ws, time_arr, "any")
        if len(ws_edges) == 0:
            return []

        frames = []

        # Para cada flanco de WS, identificamos qué bclk-edges corresponden
        # a su word. Philips: el primer bit empieza UN bclk DESPUÉS del flanco
        # de WS. Left-justified: el mismo flanco. Right-justified: alineado
        # al final del slot.
        # Nota: el primer "word" entre el inicio de captura y el primer flanco
        # WS NO se decodifica (no sabemos a qué canal pertenece sin un edge
        # de referencia). En la práctica esto solo afecta a la primera muestra
        # del stream, despreciable.
        for wi in range(len(ws_edges)):
            ws_idx = int(ws_edges[wi])
            next_ws_idx = (int(ws_edges[wi + 1])
                           if wi + 1 < len(ws_edges) else len(time_arr) - 1)
            ws_state = int(ws[ws_idx])
            # Bclks dentro del slot
            slot_edges = sample_edges[(sample_edges > ws_idx) &
                                       (sample_edges < next_ws_idx)]
            if len(slot_edges) < word_bits:
                continue

            if fmt == "Philips":
                # Skip primer bclk (delay de 1 ciclo)
                use_edges = slot_edges[1:1 + word_bits]
            elif fmt == "Left-justified":
                use_edges = slot_edges[:word_bits]
            else:  # Right-justified
                use_edges = slot_edges[-word_bits:]

            if len(use_edges) < word_bits:
                continue

            # MSB first siempre en I²S
            value = 0
            for e in use_edges:
                value = (value << 1) | int(sdata[e])

            # Determinar canal L/R según ws_state y polaridad
            if ws_low_is_left:
                channel = "L" if ws_state == 0 else "R"
            else:
                channel = "R" if ws_state == 0 else "L"

            t_start = time_arr[use_edges[0]]
            t_end = time_arr[use_edges[-1]]

            # Representación signed two's complement (audio típico)
            if show_signed and word_bits >= 8:
                sign_bit = 1 << (word_bits - 1)
                if value & sign_bit:
                    disp_value = value - (1 << word_bits)
                else:
                    disp_value = value
                label = f"{channel} {disp_value} (0x{value:0{word_bits//4 or 1}X})"
            else:
                label = f"{channel} 0x{value:0{word_bits//4 or 1}X}"

            frames.append({
                "t_start": t_start,
                "t_end":   t_end,
                "type":    "sample_L" if channel == "L" else "sample_R",
                "value":   value,
                "label":   label,
                "color":   "#5fa8ff" if channel == "L" else "#ff9f5f",
                "error":   False,
            })

        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama I²S: tres raíles (WS, BCLK, SDATA). WS marca canal
        L/R, BCLK reloj de bits, SDATA palabras MSB-first."""
        pad_l, pad_r = 64, 16
        pad_t, pad_b = 26, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)

        rail_gap = 4
        rail_h = (h - pad_t - pad_b - 2 * rail_gap) / 3
        ws_hi = y + pad_t
        ws_lo = ws_hi + rail_h
        bc_hi = ws_lo + rail_gap
        bc_lo = bc_hi + rail_h
        sd_hi = bc_lo + rail_gap
        sd_lo = sd_hi + rail_h

        cls._dgm_label(canvas, x + 10, y + 10, "I²S frame  (Philips std)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (ws_hi + ws_lo) / 2, "WS", theme)
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (bc_hi + bc_lo) / 2, "BCLK", theme)
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (sd_hi + sd_lo) / 2, "SDATA", theme)

        # 4 bits L + 4 bits R, total 8 ciclos clk
        n_bits = 8
        unit = plot_w / n_bits

        # WS: bajo durante 4 ciclos (L), alto los siguientes 4 (R)
        ws_pts = [
            (x0, ws_lo),
            (x0 + 4 * unit, ws_lo),
            (x0 + 4 * unit, ws_hi),
            (x0 + n_bits * unit, ws_hi),
        ]
        cls._dgm_signal_step(canvas, ws_pts, theme["fg"], width=1.5)

        # BCLK
        bc_pts = []
        cursor = x0
        for _ in range(n_bits):
            bc_pts.append((cursor, bc_lo))
            bc_pts.append((cursor, bc_hi))
            bc_pts.append((cursor + 0.5 * unit, bc_hi))
            bc_pts.append((cursor + 0.5 * unit, bc_lo))
            bc_pts.append((cursor + unit, bc_lo))
            cursor += unit
        cls._dgm_signal_step(canvas, bc_pts, theme["fg"], width=1.5)

        # SDATA: patrón L=1010, R=1100 (ilustrativo)
        bits = [1, 0, 1, 0, 1, 1, 0, 0]
        sd_pts = [(x0, sd_hi if bits[0] else sd_lo)]
        cursor = x0
        prev = bits[0]
        for b in bits:
            y_here = sd_hi if b else sd_lo
            sd_pts.append((cursor, sd_hi if prev else sd_lo))
            sd_pts.append((cursor, y_here))
            sd_pts.append((cursor + unit, y_here))
            cursor += unit
            prev = b
        cls._dgm_signal_step(canvas, sd_pts, theme["fg"], width=1.5)

        # Etiquetas L/R
        cls._dgm_label(canvas, x0 + 2 * unit, y + pad_t - 6,
                       "Left", theme["data"],
                       font=("Segoe UI", 8, "bold"), anchor="s")
        cls._dgm_label(canvas, x0 + 6 * unit, y + pad_t - 6,
                       "Right", "#ff9f5f",
                       font=("Segoe UI", 8, "bold"), anchor="s")

        return True
