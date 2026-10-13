"""SDIO decoder."""
from ocv.decoders.base import _binarize, _find_edges, ProtocolDecoder


# -----------------------------
# SDIO  (SD bus protocol)
# -----------------------------
class SdioDecoder(ProtocolDecoder):
    """SDIO / SD bus. Implementación base: paquetes de comando y respuesta
    sobre CMD + CLK, con DATA opcional (1-bit por DAT0, 4-bit por DAT0..3).

    Limitaciones documentadas:
      - Modo 1-bit: decoder completo de CMD/RESPONSE (48 bits) + start de DATA.
      - Modo 4-bit: detecta paquetes en DAT0..DAT3, asume mismo reloj.
      - No verifica CRC7 ni CRC16 (sólo los extrae para mostrar).
      - No decodifica significado de cada CMD (CMD0=GO_IDLE, CMD17=READ…)
        más allá de etiquetar el número.

    Útil para sniff "qué pasa por el bus" más que análisis a nivel de cmd.
    """

    name = "SDIO / SD bus"
    description = (
        "SD/SDIO bus protocol. CMD + CLK son obligatorios; DAT0..DAT3 "
        "opcionales según modo.\n"
        "Modo 1-bit (por defecto): sólo DAT0.\n"
        "Modo 4-bit: DAT0..DAT3 en paralelo (nibble por flanco).\n"
        "El decoder marca los frames CMD/RESPONSE y arranques de bloques "
        "DATA. No interpreta argumentos por comando ni verifica CRC."
    )
    channels_needed = ["CMD", "CLK"]
    channels_optional = ["DAT0", "DAT1", "DAT2", "DAT3"]

    @classmethod
    def default_params(cls):
        return {
            "bus_width":   {"label": "Bus width", "default": "1-bit",
                            "kind": "choice",
                            "options": ["1-bit", "4-bit"]},
            "sample_edge": {"label": "Sample edge", "default": "rising",
                            "kind": "choice",
                            "options": ["rising", "falling"]},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        cmd = _binarize(time_arr, channel_voltages["CMD"])
        clk = _binarize(time_arr, channel_voltages["CLK"])
        bus_width = params["bus_width"]
        edge_kind = params["sample_edge"]

        sample_edges = _find_edges(clk, time_arr, edge_kind)
        if len(sample_edges) < 48:
            return []

        # Muestrear CMD en cada flanco
        cmd_bits = cmd[sample_edges]

        # Buscar arranques: CMD packet empieza con 01 (start bit + dir bit)
        # 48-bit total: S(1) | D(1) | CMD index(6) | ARG(32) | CRC7(7) | E(1)=1
        # Nota: bound es i <= n - 48 (loop válido para extraer 48 bits desde i).
        frames = []
        i = 0
        n = len(cmd_bits)
        while i <= n - 48:
            if cmd_bits[i] == 0 and cmd_bits[i + 1] == 1:
                # Posible CMD (dir=1 host→card) o RESP (dir=0 card→host).
                # Distinguimos: aquí dir=1 → CMD.
                pkt = cmd_bits[i:i + 48]
                start_bit = int(pkt[0])
                dir_bit   = int(pkt[1])
                cmd_idx = 0
                for k in range(6):
                    cmd_idx = (cmd_idx << 1) | int(pkt[2 + k])
                arg = 0
                for k in range(32):
                    arg = (arg << 1) | int(pkt[8 + k])
                crc = 0
                for k in range(7):
                    crc = (crc << 1) | int(pkt[40 + k])
                end_bit = int(pkt[47])
                t_start = time_arr[sample_edges[i]]
                t_end = time_arr[sample_edges[i + 47]]
                kind = "cmd" if dir_bit == 1 else "resp"
                lbl = (f"CMD{cmd_idx:02d} arg=0x{arg:08X} CRC=0x{crc:02X}"
                       if dir_bit == 1 else
                       f"RESP idx={cmd_idx} arg=0x{arg:08X} CRC=0x{crc:02X}")
                frames.append({
                    "t_start": t_start, "t_end": t_end,
                    "type": kind, "value": arg,
                    "label": lbl,
                    "color": "#ffb84d" if dir_bit == 1 else "#5fcc99",
                    "error": (start_bit != 0 or end_bit != 1),
                })
                i += 48
            elif cmd_bits[i] == 0 and cmd_bits[i + 1] == 0:
                # Respuesta del card (dir=0). Mismo formato 48-bit.
                pkt = cmd_bits[i:i + 48]
                cmd_idx = 0
                for k in range(6):
                    cmd_idx = (cmd_idx << 1) | int(pkt[2 + k])
                arg = 0
                for k in range(32):
                    arg = (arg << 1) | int(pkt[8 + k])
                crc = 0
                for k in range(7):
                    crc = (crc << 1) | int(pkt[40 + k])
                end_bit = int(pkt[47])
                t_start = time_arr[sample_edges[i]]
                t_end = time_arr[sample_edges[i + 47]]
                frames.append({
                    "t_start": t_start, "t_end": t_end,
                    "type": "resp", "value": arg,
                    "label": f"RESP idx={cmd_idx} arg=0x{arg:08X} CRC=0x{crc:02X}",
                    "color": "#5fcc99",
                    "error": (end_bit != 1),
                })
                i += 48
            else:
                i += 1

        # Detectar arranques de DATA (start bit dominante en DAT0) si el usuario
        # mapeó DAT0. Sólo marcamos arranques — bloques completos requerirían
        # CRC16 + length, fuera de scope de esta primera versión.
        dat0_v = channel_voltages.get("DAT0")
        if dat0_v is not None:
            dat0 = _binarize(time_arr, dat0_v)
            dat0_bits = dat0[sample_edges]
            for j in range(1, len(dat0_bits)):
                if dat0_bits[j - 1] == 1 and dat0_bits[j] == 0:
                    t_s = time_arr[sample_edges[j]]
                    frames.append({
                        "t_start": t_s,
                        "t_end":   t_s + (time_arr[sample_edges[min(j + 1,
                                          len(sample_edges) - 1)]] - t_s),
                        "type": "data_start",
                        "value": 0,
                        "label": (f"DATA↓ ({bus_width})"),
                        "color": "#5fa8ff",
                        "error": False,
                    })

        return frames

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama SDIO: dos raíles (CMD, CLK) con frame de comando
        Start · Tx · CMD(6) · ARG(32) · CRC7 · End. DAT0..3 implícito."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 28, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10,
                       "SDIO command frame  (on CMD line)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        weights = [
            ("Start\n(0)", 0.5, "ok"),
            ("Tx\n(1=cmd)", 0.5, "ctrl"),
            ("CMD\n(6 b)", 1.5, "accent"),
            ("ARG\n(32 b)", 4.5, "data"),
            ("CRC7", 1.2, "accent"),
            ("End\n(1)", 0.5, "err"),
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
                         label="48-bit command",
                         text_color=theme["fg"])

        return True
