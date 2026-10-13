"""SPI decoder."""
import numpy as np

from ocv.decoders.base import _binarize, _find_edges, ProtocolDecoder


# -----------------------------
# SPI
# -----------------------------
class SPIDecoder(ProtocolDecoder):
    """SPI con CPOL y CPHA configurables. Captura MOSI y MISO opcionales,
    permitiendo sniff de half-duplex (solo MOSI o solo MISO) y modo sin CS
    (free-running clock; útil cuando sondeas un slave permanente).

    Modo sin CS: si no se mapea canal CS, el decoder usa el parámetro
    `idle_time_us` para delimitar frames por gaps de inactividad del
    SCK. Cuando el SCK no tiene flancos durante más que ese umbral, se
    considera fin del frame y el siguiente flanco arranca uno nuevo.
    Equivalente al modo "SPI no CS" en R&S."""

    name = "SPI"
    description = (
        "Serial Peripheral Interface. Síncrono, full-duplex. Solo SCK es "
        "obligatorio. MOSI/MISO opcionales (sniff half-duplex). CS opcional: "
        "si no se asigna, se delimita por idle time del SCK (modo sin CS)."
    )
    channels_needed = ["SCK"]
    channels_optional = ["MOSI", "MISO", "CS"]

    @classmethod
    def default_params(cls):
        return {
            "cpol":      {"label": "CPOL",      "default": 0,    "kind": "choice",
                          "options": [0, 1]},
            "cpha":      {"label": "CPHA",      "default": 0,    "kind": "choice",
                          "options": [0, 1]},
            "word_bits": {"label": "Word bits", "default": 8,    "kind": "choice",
                          "options": [4, 8, 16, 24, 32]},
            "msb_first": {"label": "MSB first", "default": True, "kind": "bool"},
            "cs_active": {"label": "CS active", "default": "low","kind": "choice",
                          "options": ["low", "high"]},
            # Tiempo de inactividad del SCK para delimitar frames cuando NO
            # se ha mapeado un canal CS. Si el clock está parado más que
            # este valor (en µs), se considera que terminó un frame y el
            # siguiente flanco arranca uno nuevo. Equivalente al "Idle
            # time" en SPI no CS de R&S.
            # Ignorado cuando hay CS mapeado.
            "idle_time_us": {"label": "Idle time (µs, no CS)",
                             "default": 100.0,
                             "kind": "float"},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        sck = _binarize(time_arr, channel_voltages["SCK"])
        # Canales opcionales — puede que el usuario no los haya mapeado.
        mosi_v = channel_voltages.get("MOSI")
        miso_v = channel_voltages.get("MISO")
        cs_v   = channel_voltages.get("CS")
        mosi = _binarize(time_arr, mosi_v) if mosi_v is not None else None
        miso = _binarize(time_arr, miso_v) if miso_v is not None else None

        cpol = int(params["cpol"])
        cpha = int(params["cpha"])
        word_bits = int(params["word_bits"])
        msb_first = bool(params["msb_first"])
        cs_active_low = (params["cs_active"] == "low")

        # En reposo, SCK = cpol. Los datos se capturan en el flanco
        # "leading" (cpha=0) o "trailing" (cpha=1). Para cpol=0,cpha=0:
        # capturar en rising edge. Para cpol=0,cpha=1: en falling. Y viceversa.
        if (cpol == 0 and cpha == 0) or (cpol == 1 and cpha == 1):
            sample_kind = "rising"
        else:
            sample_kind = "falling"

        sample_edges = _find_edges(sck, time_arr, sample_kind)

        # Construir lista de (cs_start, cs_end) — bloques durante los que
        # debemos decodificar. Tres casos:
        #
        # 1) CS mapeado: usar sus flancos activos para delimitar.
        # 2) Sin CS y con idle_time_us > 0: dividir por gaps del SCK
        #    mayores que ese umbral (modo R&S "SPI no CS"). Cada gap
        #    largo entre flancos del clock indica fin de frame.
        # 3) Sin CS y idle_time_us == 0: un único bloque para toda
        #    la captura (comportamiento legacy: free-running clock).
        if cs_v is not None:
            cs = _binarize(time_arr, cs_v)
            cs_active_state = 0 if cs_active_low else 1
            cs_diff = np.diff(cs.astype(np.int8))
            cs_starts = []
            cs_ends = []
            if cs[0] == cs_active_state:
                cs_starts.append(0)
            for i, d in enumerate(cs_diff):
                new = cs[i + 1]
                if new == cs_active_state and d != 0:
                    cs_starts.append(i + 1)
                elif new != cs_active_state and d != 0:
                    cs_ends.append(i + 1)
            if cs[-1] == cs_active_state:
                cs_ends.append(len(cs) - 1)
            blocks = list(zip(cs_starts, cs_ends))
        else:
            idle_time_us = float(params.get("idle_time_us", 100.0))
            idle_time_s = idle_time_us * 1e-6
            if idle_time_s > 0 and len(sample_edges) >= 2:
                # Trocear por gaps largos entre flancos de muestreo del SCK.
                # Cada gap > idle_time_s separa un frame del siguiente.
                edge_times = time_arr[sample_edges]
                gaps = np.diff(edge_times)
                # Índices (en sample_edges) donde empieza un nuevo frame
                # (justo después de un gap largo).
                gap_idx = np.where(gaps > idle_time_s)[0]
                # Construir bloques en términos de sample_indices (de time_arr)
                blocks = []
                start_e = int(sample_edges[0])
                for gi in gap_idx:
                    # Fin del bloque actual = último flanco antes del gap
                    end_e = int(sample_edges[gi])
                    blocks.append((start_e, end_e))
                    # Inicio del siguiente bloque = primer flanco después del gap
                    start_e = int(sample_edges[gi + 1])
                # Último bloque hasta el final
                end_e = int(sample_edges[-1])
                blocks.append((start_e, end_e))
            else:
                blocks = [(0, len(time_arr) - 1)]

        frames = []
        for cs_s, cs_e in blocks:
            t_cs_start = time_arr[cs_s]
            t_cs_end = time_arr[cs_e]
            if cs_v is not None:
                frames.append({
                    "t_start": t_cs_start,
                    "t_end":   t_cs_start + (t_cs_end - t_cs_start) * 0.02,
                    "type":    "cs_start",
                    "value":   0,
                    "label":   "↓CS",
                    "color":   "#7fdb7f",
                    "error":   False,
                })

            # Recoger flancos de muestreo dentro del rango. Sin CS los
            # bordes del bloque SON flancos de muestreo: incluirlos.
            if cs_v is not None:
                sample_in = [e for e in sample_edges if cs_s < e < cs_e]
            else:
                sample_in = [e for e in sample_edges if cs_s <= e <= cs_e]
            mosi_word = 0
            miso_word = 0
            bit_count = 0
            word_start_t = None
            for e in sample_in:
                if bit_count == 0:
                    word_start_t = time_arr[e]
                mbit = int(mosi[e]) if mosi is not None else 0
                sbit = int(miso[e]) if miso is not None else 0
                if msb_first:
                    mosi_word = (mosi_word << 1) | mbit
                    miso_word = (miso_word << 1) | sbit
                else:
                    mosi_word |= mbit << bit_count
                    miso_word |= sbit << bit_count
                bit_count += 1
                if bit_count >= word_bits:
                    word_end_t = time_arr[e]
                    hex_w = word_bits // 4 or 1
                    if mosi is not None:
                        frames.append({
                            "t_start": word_start_t,
                            "t_end":   word_end_t,
                            "type":    "mosi",
                            "value":   mosi_word,
                            "label":   f"M:0x{mosi_word:0{hex_w}X}",
                            "color":   "#5fa8ff",
                            "error":   False,
                        })
                    if miso is not None:
                        frames.append({
                            "t_start": word_start_t,
                            "t_end":   word_end_t,
                            "type":    "miso",
                            "value":   miso_word,
                            "label":   f"S:0x{miso_word:0{hex_w}X}",
                            "color":   "#5fcc99",
                            "error":   False,
                        })
                    mosi_word = 0
                    miso_word = 0
                    bit_count = 0

            if cs_v is not None:
                frames.append({
                    "t_start": t_cs_end - (t_cs_end - t_cs_start) * 0.02,
                    "t_end":   t_cs_end,
                    "type":    "cs_end",
                    "value":   0,
                    "label":   "↑CS",
                    "color":   "#cc4444",
                    "error":   False,
                })

        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama SPI: tres raíles (CS, CLK, MOSI). 4 ciclos de reloj
        ilustrativos. CS activo low. CPOL=0/CPHA=0 ilustrativo."""
        pad_l, pad_r = 56, 16
        pad_t, pad_b = 24, 24
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)

        # Tres raíles
        rail_gap = 4
        rail_h = (h - pad_t - pad_b - 2 * rail_gap) / 3
        cs_hi = y + pad_t
        cs_lo = cs_hi + rail_h
        clk_hi = cs_lo + rail_gap
        clk_lo = clk_hi + rail_h
        mo_hi = clk_lo + rail_gap
        mo_lo = mo_hi + rail_h

        # Título
        cls._dgm_label(canvas, x + 10, y + 8, "SPI frame  (CPOL=0)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (cs_hi + cs_lo) / 2, "CS", theme)
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (clk_hi + clk_lo) / 2, "CLK", theme)
        cls._dgm_rail_label(canvas, x + pad_l - 6,
                            (mo_hi + mo_lo) / 2, "MOSI", theme)

        # Reparto: pre-CS (0.5) + 8 ciclos clk (8) + post-CS (0.5) = 9
        n_clk = 8
        total = 0.5 + n_clk + 0.5
        unit = plot_w / total

        # CS: alto, baja, mantenido bajo durante datos, sube al final
        cs_pts = [
            (x0, cs_hi),
            (x0 + 0.5 * unit, cs_hi),
            (x0 + 0.5 * unit, cs_lo),
            (x0 + (0.5 + n_clk) * unit, cs_lo),
            (x0 + (0.5 + n_clk) * unit, cs_hi),
            (x0 + total * unit, cs_hi),
        ]
        cls._dgm_signal_step(canvas, cs_pts, theme["fg"], width=1.5)

        # CLK: idle bajo, n pulsos cuadrados durante CS activo
        clk_pts = [(x0, clk_lo), (x0 + 0.5 * unit, clk_lo)]
        cursor = x0 + 0.5 * unit
        for _ in range(n_clk):
            clk_pts.append((cursor, clk_lo))
            clk_pts.append((cursor, clk_hi))
            clk_pts.append((cursor + 0.5 * unit, clk_hi))
            clk_pts.append((cursor + 0.5 * unit, clk_lo))
            clk_pts.append((cursor + unit, clk_lo))
            cursor += unit
        clk_pts.append((cursor + 0.5 * unit, clk_lo))
        cls._dgm_signal_step(canvas, clk_pts, theme["fg"], width=1.5)

        # MOSI: patrón 1,0,1,1,0,1,0,1 (ilustrativo)
        bits = [1, 0, 1, 1, 0, 1, 0, 1]
        mo_pts = [(x0, mo_hi), (x0 + 0.5 * unit, mo_hi)]
        cursor = x0 + 0.5 * unit
        prev = 1
        for b in bits:
            y_here = mo_hi if b else mo_lo
            mo_pts.append((cursor, mo_hi if prev else mo_lo))
            mo_pts.append((cursor, y_here))
            mo_pts.append((cursor + unit, y_here))
            cursor += unit
            prev = b
        cls._dgm_signal_step(canvas, mo_pts, theme["fg"], width=1.5)

        # Etiquetas MSB/LSB
        cls._dgm_label(canvas, x0 + 0.5 * unit + 0.5 * unit, mo_hi - 6,
                       "MSB", theme["accent"],
                       font=("Segoe UI", 8, "bold"), anchor="s")
        cls._dgm_label(canvas, x0 + 0.5 * unit + (n_clk - 0.5) * unit,
                       mo_hi - 6, "LSB", theme["accent"],
                       font=("Segoe UI", 8, "bold"), anchor="s")

        # Flecha "8 clocks"
        cls._dgm_arrow_h(canvas,
                         x0 + 0.5 * unit,
                         x0 + (0.5 + n_clk) * unit,
                         mo_lo + 14, theme["dim"],
                         label=f"{n_clk} clocks", text_color=theme["fg"])

        return True
