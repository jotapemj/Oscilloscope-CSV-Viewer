"""I2C decoder."""
import numpy as np

from ocv.decoders.base import _binarize, _find_edges, ProtocolDecoder


# -----------------------------
# I2C
# -----------------------------
class I2CDecoder(ProtocolDecoder):
    """I2C estándar (7-bit address). No soporta 10-bit ni clock stretching
    extremo, pero los frames "normales" se decodifican correctamente."""

    name = "I2C"
    description = (
        "Inter-Integrated Circuit. Bus de 2 hilos (SDA + SCL), half-duplex, "
        "multi-master, hasta 5 MHz (Ultra Fast). Soporta 7-bit address y "
        "8-bit (address+R/W combinado)."
    )
    channels_needed = ["SDA", "SCL"]

    @classmethod
    def default_params(cls):
        return {
            "address_format": {"label": "Address fmt",
                               "default": "7-bit",
                               "kind": "choice",
                               "options": ["7-bit", "8-bit (with R/W)"]},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        sda = _binarize(time_arr, channel_voltages["SDA"])
        scl = _binarize(time_arr, channel_voltages["SCL"])

        # START = SDA cae mientras SCL está alto
        # STOP  = SDA sube  mientras SCL está alto
        # Bit data = SDA estable durante flanco de subida de SCL
        scl_rising = _find_edges(scl, time_arr, "rising")
        sda_diff = np.diff(sda.astype(np.int8))

        # Detectar STARTs y STOPs
        starts = []  # indices de start
        stops = []
        for i in range(1, len(sda)):
            if sda_diff[i - 1] == -1 and scl[i] == 1:
                starts.append(i)
            elif sda_diff[i - 1] == 1 and scl[i] == 1:
                stops.append(i)

        frames = []
        # Para cada START, leer hasta el siguiente STOP (o repeated start)
        s_set = set(starts)
        stop_set = set(stops)

        i_start = 0
        addr_fmt = params.get("address_format", "7-bit")

        while i_start < len(starts):
            start_idx = starts[i_start]
            t_start = time_arr[start_idx]
            # START frame
            frames.append({
                "t_start": t_start - 1e-9,  # un nanosegundo de "ancho" visible
                "t_end":   t_start + 1e-9,
                "type":    "start",
                "value":   0,
                "label":   "S",
                "color":   "#7fdb7f",
                "error":   False,
            })

            # Leer los flancos de subida de SCL que estén después de start_idx
            # y antes del próximo START o STOP
            next_stop_idx = next((s for s in stops if s > start_idx),
                                 len(time_arr))
            next_start_idx = (starts[i_start + 1]
                              if i_start + 1 < len(starts)
                              else len(time_arr))
            end_idx = min(next_stop_idx, next_start_idx)

            sclr_in_range = [r for r in scl_rising
                             if start_idx < r < end_idx]

            # Decodificar bytes de 9 bits (8 + ACK)
            bit_count = 0
            byte_val = 0
            byte_start_t = None
            byte_idx_in_msg = 0  # 0 = address, 1+ = data

            for r in sclr_in_range:
                bit = int(sda[r])
                if bit_count == 0:
                    byte_start_t = time_arr[r]
                if bit_count < 8:
                    byte_val = (byte_val << 1) | bit
                    bit_count += 1
                else:
                    # Este es el ACK bit (9º)
                    ack = bit  # 0 = ACK, 1 = NACK
                    byte_end_t = time_arr[r]
                    if byte_idx_in_msg == 0:
                        # Address byte
                        if addr_fmt == "7-bit":
                            addr = byte_val >> 1
                            rw = byte_val & 1
                            label = f"Addr 0x{addr:02X} {'R' if rw else 'W'}"
                            value_str = f"0x{addr:02X}"
                        else:
                            label = f"Addr 0x{byte_val:02X}"
                            value_str = f"0x{byte_val:02X}"
                        frames.append({
                            "t_start": byte_start_t,
                            "t_end":   byte_end_t,
                            "type":    "address",
                            "value":   byte_val,
                            "label":   label,
                            "color":   "#ffb84d",
                            "error":   False,
                        })
                    else:
                        # Data byte
                        frames.append({
                            "t_start": byte_start_t,
                            "t_end":   byte_end_t,
                            "type":    "data",
                            "value":   byte_val,
                            "label":   f"0x{byte_val:02X}",
                            "color":   "#5fa8ff",
                            "error":   False,
                        })
                    # ACK frame
                    frames.append({
                        "t_start": byte_end_t,
                        "t_end":   byte_end_t + (time_arr[r] - byte_start_t) / 9.0
                                   if r + 1 < len(time_arr) else byte_end_t + 1e-9,
                        "type":    "ack",
                        "value":   ack,
                        "label":   "A" if ack == 0 else "N",
                        "color":   "#cc99ff" if ack == 0 else "#ff9999",
                        "error":   False,
                    })
                    byte_idx_in_msg += 1
                    bit_count = 0
                    byte_val = 0

            # STOP frame si aplica
            if next_stop_idx < next_start_idx and next_stop_idx < len(time_arr):
                t_stop = time_arr[next_stop_idx]
                frames.append({
                    "t_start": t_stop - 1e-9,
                    "t_end":   t_stop + 1e-9,
                    "type":    "stop",
                    "value":   0,
                    "label":   "P",
                    "color":   "#cc4444",
                    "error":   False,
                })

            i_start += 1

        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama I2C: dos raíles (SCL arriba, SDA abajo) con
        START, 7 bits ADDR, R/W, ACK, 8 bits DATA, ACK, STOP."""
        pad_l, pad_r = 56, 16
        pad_t, pad_b = 28, 24
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)

        # Dos raíles: SCL arriba, SDA abajo. Cada raíl tiene H y L.
        rail_gap = 6
        rail_h = (h - pad_t - pad_b - rail_gap) / 2
        scl_hi = y + pad_t
        scl_lo = scl_hi + rail_h
        sda_hi = scl_lo + rail_gap
        sda_lo = sda_hi + rail_h

        # Título y etiquetas de raíl
        cls._dgm_label(canvas, x + 10, y + 10,
                       "I²C frame",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")
        cls._dgm_rail_label(canvas, x + pad_l - 6, (scl_hi + scl_lo) / 2,
                            "SCL", theme)
        cls._dgm_rail_label(canvas, x + pad_l - 6, (sda_hi + sda_lo) / 2,
                            "SDA", theme)

        # Reparto horizontal: start(0.8) + 8 pulsos addr+rw(8) + ack(1) +
        #                     8 pulsos data(8) + ack(1) + stop(0.8) = 27.6
        # Cada "pulso" = 1 unidad. Total ≈ 27.6 unidades.
        total_units = 0.8 + 8 + 1 + 8 + 1 + 0.8
        unit = plot_w / total_units

        # ---- SCL: idle alto, pulsos durante ADDR+RW+ACK+DATA+ACK,
        #      alto durante START y STOP ----
        scl_pts = [(x0, scl_hi)]
        cursor = x0
        # Start: SCL alto
        cursor += 0.8 * unit
        scl_pts.append((cursor, scl_hi))
        # 8 pulsos (7 addr + RW)
        for _ in range(8):
            half = 0.5 * unit
            scl_pts.append((cursor, scl_hi))
            scl_pts.append((cursor, scl_lo))
            scl_pts.append((cursor + half, scl_lo))
            scl_pts.append((cursor + half, scl_hi))
            scl_pts.append((cursor + unit, scl_hi))
            cursor += unit
        # ACK pulse
        scl_pts.append((cursor, scl_hi))
        scl_pts.append((cursor, scl_lo))
        scl_pts.append((cursor + 0.5 * unit, scl_lo))
        scl_pts.append((cursor + 0.5 * unit, scl_hi))
        scl_pts.append((cursor + unit, scl_hi))
        cursor += unit
        # 8 pulsos data
        for _ in range(8):
            scl_pts.append((cursor, scl_hi))
            scl_pts.append((cursor, scl_lo))
            scl_pts.append((cursor + 0.5 * unit, scl_lo))
            scl_pts.append((cursor + 0.5 * unit, scl_hi))
            scl_pts.append((cursor + unit, scl_hi))
            cursor += unit
        # ACK pulse
        scl_pts.append((cursor, scl_hi))
        scl_pts.append((cursor, scl_lo))
        scl_pts.append((cursor + 0.5 * unit, scl_lo))
        scl_pts.append((cursor + 0.5 * unit, scl_hi))
        scl_pts.append((cursor + unit, scl_hi))
        cursor += unit
        # Stop: alto
        scl_pts.append((cursor + 0.8 * unit, scl_hi))
        cls._dgm_signal_step(canvas, scl_pts, theme["fg"], width=1.5)

        # ---- SDA: idle alto, START baja al inicio (con SCL alto),
        #      luego datos (cambia mientras SCL bajo), STOP sube al final ----
        # Para legibilidad, marcamos zonas de cambio como una "envolvente"
        # estilo bus: dos líneas paralelas que se cruzan a media data.
        sda_pts = [(x0, sda_hi)]
        cursor = x0
        # Mantiene alto hasta caer en START (SCL alto): caída a mitad del start
        cursor += 0.8 * unit
        sda_pts.append((cursor, sda_hi))
        sda_pts.append((cursor, sda_lo))
        # ADDR+RW+ACK+DATA+ACK = 8+1+8+1 = 18 unidades.
        # Dibujamos una línea zigzag baja simulando datos.
        for i in range(18):
            level = sda_lo if (i % 2 == 0) else sda_hi
            sda_pts.append((cursor + unit, level))
            cursor += unit
        # STOP: SDA sube con SCL alto
        sda_pts.append((cursor, sda_hi))
        sda_pts.append((cursor + 0.8 * unit, sda_hi))
        cls._dgm_signal_step(canvas, sda_pts, theme["fg"], width=1.5)

        # ---- Etiquetas de campos ----
        cursor = x0 + 0.4 * unit
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "S",
                       theme["ok"], font=("Segoe UI", 8, "bold"),
                       anchor="s")
        cursor = x0 + 0.8 * unit + (8 * unit) / 2
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "ADDR + R/W",
                       theme["data"], font=("Segoe UI", 8, "bold"),
                       anchor="s")
        cursor = x0 + 0.8 * unit + 8 * unit + 0.5 * unit
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "A",
                       theme["accent"], font=("Segoe UI", 8, "bold"),
                       anchor="s")
        cursor = x0 + 0.8 * unit + 8 * unit + 1 * unit + (8 * unit) / 2
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "DATA",
                       theme["data"], font=("Segoe UI", 8, "bold"),
                       anchor="s")
        cursor = x0 + 0.8 * unit + 8 * unit + 1 * unit + 8 * unit + 0.5 * unit
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "A",
                       theme["accent"], font=("Segoe UI", 8, "bold"),
                       anchor="s")
        cursor = x0 + 0.8 * unit + 8 * unit + 1 * unit + 8 * unit + 1 * unit + 0.4 * unit
        cls._dgm_label(canvas, cursor, y + pad_t - 6, "P",
                       theme["err"], font=("Segoe UI", 8, "bold"),
                       anchor="s")

        return True
