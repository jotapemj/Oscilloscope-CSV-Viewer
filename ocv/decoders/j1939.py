"""SAE J1939 decoder (on top of CAN 2.0B)."""
from ocv.decoders.base import ProtocolDecoder
from ocv.decoders.can import CanDecoder


# -----------------------------
# SAE J1939 (sobre CAN 29-bit)
# -----------------------------
class J1939Decoder(ProtocolDecoder):
    """J1939 = capa aplicación sobre CAN 2.0B (siempre 29-bit). Parsea el
    ID en sus campos: Priority (3) | EDP+DP (2) | PF (8) | PS (8) | SA (8).
    Y deriva el PGN según PF:
       PF < 240 → PGN = (EDP DP PF 0x00) (PDU1, destino = PS)
       PF ≥ 240 → PGN = (EDP DP PF PS)    (PDU2, broadcast)
    """

    name = "J1939"
    description = (
        "SAE J1939 sobre CAN 29-bit. Decodifica el frame CAN y parsea el "
        "ID en sus campos: PRIO, R, DP, PF, PS, SA. Calcula el PGN según "
        "el tipo de PDU. Modos:\n"
        " • Decode IDs only: sólo cabecera/PGN/SA/DA\n"
        " • Decode IDs + payload: muestra también los 8 bytes de datos"
    )
    channels_needed = ["CAN"]
    channels_optional = ["CAN_H", "CAN_L"]

    @classmethod
    def default_params(cls):
        return {
            "input_mode":  {"label": "Input mode", "default": "Logical (TTL)",
                            "kind": "choice",
                            "options": ["Logical (TTL)",
                                        "Differential (H,L)"]},
            "bitrate":     {"label": "Bitrate (0=auto)", "default": 250000,
                            "kind": "int"},
            "decode_mode": {"label": "Decode",
                            "default": "IDs + payload",
                            "kind": "choice",
                            "options": ["IDs only", "IDs + payload"]},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        # Reutilizamos el decoder CAN clásico
        can_params = {
            "input_mode": params["input_mode"],
            "bitrate":    params["bitrate"],
            "show_stuffs": False,
        }
        can_frames = CanDecoder.decode(time_arr, channel_voltages, can_params)
        decode_payload = (params["decode_mode"] == "IDs + payload")

        out = []
        for f in can_frames:
            if f["type"] == "id":
                full_id = f["value"]
                # Si no es 29-bit el ID, no es J1939 válido — lo dejamos pero
                # con label informativo.
                if full_id <= 0x7FF:
                    f2 = dict(f)
                    f2["label"] = f"ID 0x{full_id:03X} (not 29-bit)"
                    f2["color"] = "#888888"
                    out.append(f2)
                    continue
                prio = (full_id >> 26) & 0x7
                edp  = (full_id >> 25) & 0x1
                dp   = (full_id >> 24) & 0x1
                pf   = (full_id >> 16) & 0xFF
                ps   = (full_id >> 8) & 0xFF
                sa   = full_id & 0xFF
                if pf < 240:
                    pgn = (edp << 17) | (dp << 16) | (pf << 8)
                    da  = ps
                    pdu = "PDU1"
                    pgn_label = f"PGN 0x{pgn:05X} DA=0x{da:02X}"
                else:
                    pgn = (edp << 17) | (dp << 16) | (pf << 8) | ps
                    pdu = "PDU2"
                    pgn_label = f"PGN 0x{pgn:05X} (bcast)"
                f2 = dict(f)
                f2["label"] = (f"PRI={prio} {pdu} {pgn_label} SA=0x{sa:02X}")
                f2["color"] = "#ffb84d"
                out.append(f2)
            elif f["type"] == "data" and not decode_payload:
                # Saltar payload en modo "IDs only"
                continue
            else:
                out.append(f)
        return out

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama J1939: descompone el ID 29-bit en sus campos
        Priority · EDP · DP · PF · PS · SA. Sobre CAN extendido."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 50, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10,
                       "J1939  (CAN 2.0B / 29-bit ID)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")
        cls._dgm_label(canvas, x + 10, y + 28,
                       "ID breakdown:", theme["dim"],
                       font=("Segoe UI", 8), anchor="w")

        # Bits por campo: Prio=3, EDP=1, DP=1, PF=8, PS=8, SA=8 = 29
        weights = [
            ("Priority\n(3 b)", 3, "accent"),
            ("E", 1, "ctrl"),
            ("D", 1, "ctrl"),
            ("PF\n(8 b)", 8, "data"),
            ("PS / GE\n(8 b)", 8, "data"),
            ("SA\n(8 b)", 8, "ok"),
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

        # Flecha total = 29 bits
        cls._dgm_arrow_h(canvas, x0, x0 + total * unit,
                         box_y + box_h + 12, theme["dim"],
                         label="29-bit identifier",
                         text_color=theme["fg"])

        return True
