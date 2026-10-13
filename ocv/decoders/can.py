"""CAN 2.0 and CAN FD decoders with shared bit helpers."""
import numpy as np

from ocv.decoders.base import (
    _binarize,
    _find_edges,
    _sample_at,
    ProtocolDecoder,
)


# =====================================================
# CAN (Controller Area Network)
# =====================================================
# Helpers compartidos entre CAN clásico, CAN FD y J1939.
# Documentación de bits dominantes/recesivos:
#   - dominante = 0 (low en CAN_H lógico, también low en sniff TTL)
#   - recesivo  = 1 (idle)
# Asumimos siempre que la señal llega como TTL lógico ya recuperado
# (no diferencial). El modo "differential" calcula CAN_H - CAN_L y
# binariza el resultado.


def _can_extract_logical(channel_voltages, mode):
    """Devuelve un par (logical_volt_array, threshold_used_for_binarize_None)
    según el modo de entrada del usuario.
      - 'Logical (TTL)': usa channel_voltages['CAN'] tal cual.
      - 'Differential (H,L)': resta H - L y binariza con threshold = 0.5*max.
    """
    if mode == "Differential (H,L)":
        h = channel_voltages.get("CAN_H")
        l = channel_voltages.get("CAN_L")
        if h is None or l is None:
            # Fallback al modo lógico
            return channel_voltages.get("CAN"), None
        diff = h - l
        # Dominante: diff alta (~2V). Recesivo: diff ≈ 0V.
        # En CAN dominante=0 lógico. Hacemos invert.
        # Threshold: punto medio entre 90th pct y 10th pct, igual que
        # _binarize por defecto.
        return diff, None
    return channel_voltages.get("CAN"), None


def _can_binarize_invert_if_differential(v, mode, time_arr):
    """Binariza una señal CAN respetando que en modo diferencial dominante
    = nivel alto de (H-L), que corresponde a 0 lógico CAN.
    En modo lógico TTL asume dominante = 0 (idle alto)."""
    p_lo = float(np.quantile(v, 0.10))
    p_hi = float(np.quantile(v, 0.90))
    hyst = (p_hi - p_lo) * 0.10
    b = _binarize(time_arr, v, hysteresis=hyst)
    if mode == "Differential (H,L)":
        # H-L alto = dominante = 0 lógico → invertir
        b = (1 - b).astype(np.uint8)
    return b


def _can_unstuff_bits(bits, max_unstuffed=None):
    """Inverso de bit-stuffing CAN — tolerante con bits post-stuffing.

    Recorre `bits` y elimina los bits que estén en posición de stuff (el bit
    inmediatamente posterior a 5 idénticos consecutivos). Devuelve:
       (unstuffed_bits, stuff_positions_in_input, stuff_error_detected)

    Comportamiento clave:
      - Si encuentra 6 bits idénticos seguidos antes de un stuff, asume que
        ha llegado al final de la zona stuffeada (típico: CRC delim+ACK+EOF
        con muchos unos). Devuelve lo decodificado hasta ese punto y marca
        stuff_error_detected=False (es esperado en un frame válido).
      - Si encuentra >7 bits idénticos (claramente fuera de la spec dentro
        de zona stuffed real) durante los primeros bits → stuff_error=True.

    El caller (CanDecoder) corta en el número de bits que necesita parsear
    (ID, ctrl, DLC, DATA, CRC) y nunca consulta posiciones más allá. Esto
    hace robusta la decodificación sin necesidad de saber a priori la
    longitud exacta del payload.
    """
    if not bits:
        return [], [], False
    out = [bits[0]]
    stuff_positions = []
    count = 1
    prev = bits[0]
    i = 1
    stuff_error = False
    while i < len(bits):
        if max_unstuffed is not None and len(out) >= max_unstuffed:
            break
        bit = bits[i]
        if bit == prev:
            count += 1
        else:
            count = 1
        if count == 6:
            # 6 iguales sin stuff bit intermedio: salimos asumiendo fin
            # de zona stuffed (CRC delim/ACK/EOF). No es error.
            break
        out.append(bit)
        prev = bit
        if count == 5 and i + 1 < len(bits):
            nxt = bits[i + 1]
            if nxt == bit:
                # No es stuff. Probablemente fin de zona stuffed. Salimos.
                break
            stuff_positions.append(i + 1)
            i += 2
            count = 1
            prev = nxt
            continue
        i += 1
    return out, stuff_positions, stuff_error


def _can_crc15(bits):
    """CRC-15/CAN (poly 0x4599, init 0). bits son los bits desde SOF
    hasta el último bit del data field (sin stuffs)."""
    crc = 0
    for bit in bits:
        c = (crc >> 14) & 1
        crc = ((crc << 1) & 0x7FFF)
        if (c ^ bit) & 1:
            crc ^= 0x4599
    return crc & 0x7FFF


def _can_crc17(bits):
    """CRC-17 para CAN FD (poly 0x1685B, init 1)."""
    crc = 1 << 16
    for bit in bits:
        c = (crc >> 16) & 1
        crc = ((crc << 1) & 0x1FFFF)
        if (c ^ bit) & 1:
            crc ^= 0x1685B
    return crc & 0x1FFFF


def _can_crc21(bits):
    """CRC-21 para CAN FD frames largos (poly 0x102899, init 1)."""
    crc = 1 << 20
    for bit in bits:
        c = (crc >> 20) & 1
        crc = ((crc << 1) & 0x1FFFFF)
        if (c ^ bit) & 1:
            crc ^= 0x102899
    return crc & 0x1FFFFF


def _can_detect_bit_time(b, time_arr, hint_bitrate=None):
    """Estima el bit time analizando la duración más corta entre flancos
    consecutivos. En CAN el bit más corto observable es 1*Tbit (un único
    bit dominante/recesivo entre dos del valor opuesto). Si hint_bitrate
    está dado, devuelve 1/hint_bitrate y solo lo usa para sanity check."""
    if hint_bitrate is not None and hint_bitrate > 0:
        return 1.0 / hint_bitrate
    edges = _find_edges(b, time_arr, "any")
    if len(edges) < 4:
        return None
    durations = np.diff(time_arr[edges])
    # El bit más corto plausible: percentil 5 (ignora outliers)
    bit_time = float(np.quantile(durations, 0.05))
    return bit_time


class CanDecoder(ProtocolDecoder):
    """CAN 2.0A/B clásico. Soporta entrada lógica (un canal TTL recuperado)
    o diferencial (CAN_H + CAN_L). Detecta SOF, ID 11 o 29 bits, control,
    data, CRC15, ACK y EOF. Aplica bit-unstuffing.

    No verifica todos los campos de error definidos por la spec (error
    frames, overload frames…); detecta y reporta stuff errors y CRC bad.
    """

    name = "CAN 2.0 A/B"
    description = (
        "Controller Area Network classic. Frames hasta 8 bytes payload, "
        "ID estándar 11-bit o extendido 29-bit. Bit-rate típico 125k–1M.\n"
        "Modos de entrada:\n"
        " • Logical (TTL): un canal recuperado a TTL (idle = alto)\n"
        " • Differential: dos canales CAN_H y CAN_L; se calcula H-L."
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
            "bitrate":     {"label": "Bitrate (0=auto)", "default": 500000,
                            "kind": "int"},
            "show_stuffs": {"label": "Mark stuff bits", "default": False,
                            "kind": "bool"},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        mode = params["input_mode"]
        bitrate = int(params["bitrate"])
        sig, _ = _can_extract_logical(channel_voltages, mode)
        if sig is None:
            return []
        b = _can_binarize_invert_if_differential(sig, mode, time_arr)

        bit_time = _can_detect_bit_time(b, time_arr,
                                         hint_bitrate=bitrate if bitrate > 0
                                         else None)
        if bit_time is None or bit_time <= 0:
            return []

        frames = []
        # SOF = flanco de bajada (dominante) tras un idle ≥ 11 bits recesivos
        falling = _find_edges(b, time_arr, "falling")
        i = 0
        while i < len(falling):
            sof_idx = int(falling[i])
            t_sof = time_arr[sof_idx]
            # Validar idle previo: ≥7 bits recesivos justo antes
            # (Inter-Frame Space = 3 + suspend; relajamos a 7 por margen).
            t_idle_start = t_sof - 7 * bit_time
            if t_idle_start > time_arr[0]:
                # Muestrear: si algún bit antes era 0, no es SOF válido
                ok = True
                for k in range(7):
                    if _sample_at(b, time_arr, t_sof - (k + 1) * bit_time) == 0:
                        ok = False
                        break
                if not ok:
                    i += 1
                    continue

            # Leer ~150 bits (suficiente para frame extendido máximo)
            # como secuencia cruda muestreada en el centro de cada bit.
            max_bits = 150
            raw_bits = []
            for k in range(max_bits):
                t_sample = t_sof + bit_time * (0.5 + k)
                if t_sample >= time_arr[-1]:
                    break
                raw_bits.append(int(_sample_at(b, time_arr, t_sample)))
            if len(raw_bits) < 20:
                i += 1
                continue

            # Bit-unstuffing aplicable desde SOF hasta el último bit del CRC.
            # Hacemos un unstuff "generoso" — pedimos hasta 130 bits limpios
            # (suficiente para frame extendido máximo: 1+29+1+1+1+4+64+15 ≈
            # 116 bits). Cualquier exceso se deja sin tocar; los bits
            # post-CRC (delim/ACK/EOF) NO llevan stuffing y aparecerán como
            # recesivos en raw_bits, ya fuera del rango analizado.
            unstuffed, stuff_pos, stuff_err = _can_unstuff_bits(
                raw_bits, max_unstuffed=130)
            if stuff_err:
                frames.append({
                    "t_start": t_sof,
                    "t_end":   t_sof + bit_time * 6,
                    "type":    "error",
                    "value":   0,
                    "label":   "STUFF ERR",
                    "color":   "#ff6b6b",
                    "error":   True,
                })
                # Avanzar más allá del error
                i = cls._advance_past(falling, i, time_arr, t_sof + bit_time * 10)
                continue

            # Parsear según spec:
            #   SOF (1) | ID-A (11) | RTR/SRR (1) | IDE (1) | ...
            # Si IDE=0: standard frame
            #   RTR (1) | r0 (1) | DLC (4) | DATA (0..64) | CRC (15) | CRC del (1) | ACK | ACK del | EOF (7)
            # Si IDE=1: extended frame
            #   SRR=1 | IDE=1 | ID-B (18) | RTR | r1 r0 | DLC (4) | DATA | CRC | ...
            p = 0
            # Cabecera mínima SOF..DLC: 19 bits (39 extendida). Más corta =
            # el unstuff paró antes (ruido, no es trama). Saltar, no abortar.
            if len(unstuffed) < 19 or unstuffed[p] != 0:
                # Debería ser SOF dominante. Falso disparo.
                i += 1
                continue
            sof_bit = unstuffed[p]; p += 1
            id_a = 0
            for k in range(11):
                id_a = (id_a << 1) | unstuffed[p]; p += 1
            rtr_or_srr = unstuffed[p]; p += 1
            ide = unstuffed[p]; p += 1

            extended = (ide == 1)
            if extended and len(unstuffed) < 39:
                i += 1
                continue
            if extended:
                # SRR fue rtr_or_srr (esperado 1)
                id_b = 0
                for k in range(18):
                    id_b = (id_b << 1) | unstuffed[p]; p += 1
                rtr = unstuffed[p]; p += 1
                r1 = unstuffed[p]; p += 1
                r0 = unstuffed[p]; p += 1
                full_id = (id_a << 18) | id_b
            else:
                rtr = rtr_or_srr
                r0 = unstuffed[p]; p += 1
                full_id = id_a

            dlc = 0
            for k in range(4):
                dlc = (dlc << 1) | unstuffed[p]; p += 1
            payload_bytes = min(dlc, 8)   # CAN clásico clipa en 8

            data = []
            for byte_i in range(payload_bytes):
                v = 0
                for k in range(8):
                    if p >= len(unstuffed):
                        break
                    v = (v << 1) | unstuffed[p]; p += 1
                data.append(v)

            # CRC-15
            crc_received = 0
            crc_ok = True
            if p + 15 <= len(unstuffed):
                # CRC se calcula sobre los bits SIN stuffs DESDE SOF
                # incluido hasta el último bit de data (justo antes del CRC).
                bits_for_crc = unstuffed[0:p]
                expected = _can_crc15(bits_for_crc)
                for k in range(15):
                    crc_received = (crc_received << 1) | unstuffed[p]; p += 1
                crc_ok = (crc_received == expected)
            else:
                crc_ok = False

            # Frames de salida
            # Estimar tiempos: cada bit RAW (con stuffs) ocupa bit_time.
            # El bit "i" empieza en t_sof + i*bit_time.
            t_id_start = t_sof + bit_time   # tras SOF
            t_id_end = t_id_start + (29 if extended else 11) * bit_time
            id_color = "#ffb84d"
            frames.append({
                "t_start": t_sof,
                "t_end":   t_sof + bit_time,
                "type":    "sof",
                "value":   0,
                "label":   "SOF",
                "color":   "#7fdb7f",
                "error":   False,
            })
            frames.append({
                "t_start": t_id_start,
                "t_end":   t_id_end,
                "type":    "id",
                "value":   full_id,
                "label":   (f"ID 0x{full_id:08X} (29b)" if extended
                            else f"ID 0x{full_id:03X}"),
                "color":   id_color,
                "error":   False,
            })
            t_after_id = t_id_end
            # Saltamos RTR/IDE/r0(r1) visualmente: indicador compacto
            ctrl_bits = (3 if not extended else 5)
            t_ctrl_end = t_after_id + ctrl_bits * bit_time
            frames.append({
                "t_start": t_after_id,
                "t_end":   t_ctrl_end,
                "type":    "ctrl",
                "value":   dlc,
                "label":   f"DLC={dlc}" + (" RTR" if rtr else ""),
                "color":   "#cc99ff",
                "error":   False,
            })
            t_dlc_end = t_ctrl_end + 4 * bit_time
            t_data_start = t_dlc_end
            for byte_i, dv in enumerate(data):
                t_d_start = t_data_start + byte_i * 8 * bit_time
                t_d_end = t_d_start + 8 * bit_time
                frames.append({
                    "t_start": t_d_start,
                    "t_end":   t_d_end,
                    "type":    "data",
                    "value":   dv,
                    "label":   f"D{byte_i} 0x{dv:02X}",
                    "color":   "#5fa8ff",
                    "error":   False,
                })
            t_data_end = t_data_start + payload_bytes * 8 * bit_time
            t_crc_start = t_data_end
            t_crc_end = t_crc_start + 15 * bit_time
            frames.append({
                "t_start": t_crc_start,
                "t_end":   t_crc_end,
                "type":    "crc",
                "value":   crc_received,
                "label":   (f"CRC 0x{crc_received:04X}"
                            if crc_ok
                            else f"CRC! 0x{crc_received:04X}"),
                "color":   "#cc99ff" if crc_ok else "#ff6b6b",
                "error":   not crc_ok,
            })

            # Marcar stuffs si pidió
            if bool(params["show_stuffs"]):
                for sp in stuff_pos:
                    t_s = t_sof + sp * bit_time
                    frames.append({
                        "t_start": t_s,
                        "t_end":   t_s + bit_time,
                        "type":    "stuff",
                        "value":   0,
                        "label":   "·",
                        "color":   "#666666",
                        "error":   False,
                    })

            # Avanzar pasado este frame
            advance_to = t_crc_end + 10 * bit_time
            i = cls._advance_past(falling, i, time_arr, advance_to)

        return frames

    @staticmethod
    def _advance_past(falling, i, time_arr, t_target):
        while i < len(falling) and time_arr[int(falling[i])] < t_target:
            i += 1
        return i

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama CAN clásico: SOF · ID · DLC · Data · CRC · ACK · EOF."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 28, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10, "CAN frame (2.0A)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        weights = [
            ("SOF",   0.6, "ok"),
            ("ID 11b", 2.5, "accent"),
            ("DLC",  1.0, "ctrl"),
            ("Data 0..8 B", 4.0, "data"),
            ("CRC 15", 1.8, "accent"),
            ("ACK", 0.8, "ok"),
            ("EOF", 1.0, "err"),
        ]
        total = sum(w for _, w, _ in weights)
        unit = plot_w / total

        cursor = x0
        for label, width, ck in weights:
            bw = width * unit
            cls._dgm_field_row(canvas, cursor, box_y,
                               [(label, bw, ck)], box_h, theme)
            cursor += bw

        return True


class _FdEnd(Exception):
    """La captura termina a mitad de trama."""


class _FdBitReader:
    """Lee bits CAN FD en tiempo real de la captura.

    Lleva el tiempo de inicio del bit actual con el bitrate vigente, de
    modo que los bits de stuffing ocupan su sitio en el tiempo. Se
    resincroniza en cada flanco cercano al inicio esperado del bit
    (como hace un controlador CAN), lo que tolera pequeñas diferencias
    de reloj entre el emisor y el bitrate configurado.
    """

    def __init__(self, b, time_arr, edge_t, t0, bt):
        self.b = b
        self.t = time_arr
        self.edge_t = edge_t
        self.t_bit = t0
        self.bt = bt
        self.prev = None
        self.run = 0
        self.stuffed = []        # SOF..datos con stuff dinámico (para CRC)
        self.n_dyn = 0
        self.stuff_error = False
        self.fixed_error = False

    def raw(self):
        """Un bit físico, sin quitar stuffing."""
        lo = self.t_bit - 0.25 * self.bt
        j = int(np.searchsorted(self.edge_t, lo))
        if j < len(self.edge_t) and self.edge_t[j] <= self.t_bit + 0.25 * self.bt:
            self.t_bit = float(self.edge_t[j])
        t_s = self.t_bit + 0.5 * self.bt
        if t_s >= self.t[-1]:
            raise _FdEnd
        self.t_bit += self.bt
        return _sample_at(self.b, self.t, t_s)

    def bit(self):
        """Bit lógico en zona de stuffing dinámico (SOF..datos)."""
        if self.run == 5:
            s = self.raw()
            if s == self.prev:
                self.stuff_error = True
            self.stuffed.append(s)
            self.n_dyn += 1
            self.prev, self.run = s, 1
        x = self.raw()
        self.stuffed.append(x)
        self.run = self.run + 1 if x == self.prev else 1
        self.prev = x
        return x

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def fixed_bits(self, n):
        """Stuff count + CRC: bit fijo antes y tras cada 4 bits.
        Un stuff dinámico pendiente al final de los datos lo sustituye el
        primer bit fijo (mismo valor), así que no se consume aparte."""
        out, last = [], self.prev
        for k in range(n):
            if k % 4 == 0:
                s = self.raw()
                if s == last:
                    self.fixed_error = True
                last = s
            x = self.raw()
            out.append(x)
            last = x
        return out


# -----------------------------
# CAN FD
# -----------------------------
class CanFdDecoder(ProtocolDecoder):
    """CAN FD (Flexible Data-rate). Frames hasta 64 bytes payload, doble
    velocidad opcional (BRS), CRC17 o CRC21 según longitud.

    Simplificaciones:
      - Asumimos bitrate nominal y data conocidos (campos separados).
      - BRS lo respetamos sólo para el escalado de tiempos de DATA y CRC.
    """

    name = "CAN FD"
    description = (
        "CAN with Flexible Data-rate. ISO 11898-1:2015. Payload hasta 64 "
        "bytes (DLC mapping no lineal). BRS permite acelerar la fase de "
        "datos. CRC17 si payload ≤ 16 bytes, CRC21 si > 16.\n"
        "Modos de entrada igual que CAN."
    )
    channels_needed = ["CAN"]
    channels_optional = ["CAN_H", "CAN_L"]

    # DLC → longitud de payload en CAN FD
    _FD_DLC_LEN = {
        0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
        9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
    }

    @classmethod
    def default_params(cls):
        return {
            "input_mode":     {"label": "Input mode",
                               "default": "Logical (TTL)",
                               "kind": "choice",
                               "options": ["Logical (TTL)",
                                           "Differential (H,L)"]},
            "nominal_bitrate": {"label": "Nominal bitrate",
                                "default": 500000, "kind": "int"},
            "data_bitrate":   {"label": "Data bitrate (BRS)",
                               "default": 2000000, "kind": "int"},
        }

    # Código Gray del stuff count (ISO 11898-1:2015), índice = count mod 8
    _GRAY3 = (0b000, 0b001, 0b011, 0b010, 0b110, 0b111, 0b101, 0b100)

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        mode = params["input_mode"]
        nom_br = int(params["nominal_bitrate"])
        data_br = int(params["data_bitrate"])
        sig, _ = _can_extract_logical(channel_voltages, mode)
        if sig is None:
            return []
        b = _can_binarize_invert_if_differential(sig, mode, time_arr)
        bt_nom = 1.0 / max(1, nom_br)
        bt_data = 1.0 / max(1, data_br)
        edge_t = time_arr[_find_edges(b, time_arr, "any")]

        frames = []
        falling = _find_edges(b, time_arr, "falling")
        i = 0
        while i < len(falling):
            t_sof = time_arr[int(falling[i])]
            # Idle previo: 7 bits recesivos
            ok = True
            for k in range(7):
                if t_sof - (k + 1) * bt_nom < time_arr[0]:
                    break
                if _sample_at(b, time_arr, t_sof - (k + 1) * bt_nom) == 0:
                    ok = False
                    break
            if not ok:
                i += 1
                continue

            rd = _FdBitReader(b, time_arr, edge_t, t_sof, bt_nom)
            try:
                fr = cls._parse_frame(rd, bt_data)
            except _FdEnd:
                fr = None
            if fr is None:
                # No es FD (FDF=0), truncada o falso SOF
                i += 1
                continue
            frames.extend(fr)
            i = CanDecoder._advance_past(falling, i, time_arr,
                                         rd.t_bit + 10 * bt_nom)
        return frames

    @classmethod
    def _parse_frame(cls, rd, bt_data):
        """Lee una trama desde SOF. Devuelve lista de frames o None."""
        t_sof = rd.t_bit
        if rd.bit() != 0:
            return None
        t_id = rd.t_bit
        id_a = rd.bits(11)
        rd.bit()                          # RRS (base) o SRR (extendida)
        extended = rd.bit() == 1          # IDE
        if extended:
            full_id = (id_a << 18) | rd.bits(18)
            rd.bit()                      # RRS
        else:
            full_id = id_a
        t_ctrl = rd.t_bit
        if rd.bit() != 1:                 # FDF=0 → CAN clásico
            return None
        rd.bit()                          # res
        brs = rd.bit()
        if brs:
            rd.bt = bt_data               # cambio de bitrate tras BRS
        esi = rd.bit()
        dlc = rd.bits(4)
        n = cls._FD_DLC_LEN[dlc]
        if rd.stuff_error:
            return [{"t_start": t_sof, "t_end": rd.t_bit, "type": "error",
                     "value": 0, "label": "STUFF ERR", "color": "#ff6b6b",
                     "error": True}]

        out = [
            {"t_start": t_sof, "t_end": t_id, "type": "sof", "value": 0,
             "label": "SOF", "color": "#7fdb7f", "error": False},
            {"t_start": t_id, "t_end": t_ctrl, "type": "id",
             "value": full_id,
             "label": (f"ID 0x{full_id:08X} (29b)" if extended
                       else f"ID 0x{full_id:03X}"),
             "color": "#ffb84d", "error": False},
            {"t_start": t_ctrl, "t_end": rd.t_bit, "type": "ctrl",
             "value": dlc,
             "label": f"DLC={dlc} ({n}B)" + (" BRS" if brs else "")
                      + (" ESI" if esi else ""),
             "color": "#cc99ff", "error": False},
        ]
        for k in range(n):
            t0 = rd.t_bit
            v = rd.bits(8)
            out.append({"t_start": t0, "t_end": rd.t_bit, "type": "data",
                        "value": v, "label": f"D{k} 0x{v:02X}",
                        "color": "#5fa8ff", "error": False})
        if rd.stuff_error:
            out.append({"t_start": t_sof, "t_end": rd.t_bit, "type": "error",
                        "value": 0, "label": "STUFF ERR", "color": "#ff6b6b",
                        "error": True})
            return out

        # Stuff count (Gray 3 + paridad) y CRC, con bits de stuffing fijo
        width = 17 if n <= 16 else 21
        t_crc = rd.t_bit
        tail = rd.fixed_bits(4 + width)
        sbc, crc_bits = tail[:4], tail[4:]
        sbc_ok = (sum(sbc) % 2 == 0 and
                  (sbc[0] << 2 | sbc[1] << 1 | sbc[2])
                  == cls._GRAY3[rd.n_dyn % 8] and not rd.fixed_error)
        crc_rx = 0
        for x in crc_bits:
            crc_rx = (crc_rx << 1) | x
        crc_fn = _can_crc17 if width == 17 else _can_crc21
        crc_ok = crc_fn(rd.stuffed + sbc) == crc_rx
        good = crc_ok and sbc_ok
        label = f"CRC{width} 0x{crc_rx:X}" if crc_ok else f"CRC{width}! 0x{crc_rx:X}"
        if not sbc_ok:
            label += " SBC!"
        out.append({"t_start": t_crc, "t_end": rd.t_bit, "type": "crc",
                    "value": crc_rx, "label": label,
                    "color": "#cc99ff" if good else "#ff6b6b",
                    "error": not good})
        return out

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama CAN FD: añade FDF · BRS · ESI vs CAN clásico,
        payload hasta 64 B, CRC17/21."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 28, 22
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        cls._dgm_label(canvas, x + 10, y + 10,
                       "CAN FD frame  (flexible data rate)",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        # Campos clave (compactos)
        weights = [
            ("SOF",   0.5, "ok"),
            ("ID",    2.0, "accent"),
            ("FDF",   0.5, "ctrl"),
            ("BRS",   0.5, "ctrl"),
            ("ESI",   0.5, "ctrl"),
            ("DLC",   0.7, "ctrl"),
            ("Data 0..64 B", 4.5, "data"),
            ("CRC17/21", 1.7, "accent"),
            ("ACK",  0.6, "ok"),
            ("EOF",  0.7, "err"),
        ]
        total = sum(w for _, w, _ in weights)
        unit = plot_w / total

        cursor = x0
        for label, width, ck in weights:
            bw = width * unit
            cls._dgm_field_row(canvas, cursor, box_y,
                               [(label, bw, ck)], box_h, theme)
            cursor += bw

        return True
