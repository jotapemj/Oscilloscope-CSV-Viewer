"""Generadores de señales sintéticas para los tests."""
import numpy as np


def trapezoid(period=1e-3, high=3.3, low=0.0, rise=20e-6, fall=20e-6,
              width=300e-6, n_periods=3, fs=10e6, t0=-100e-6):
    """Tren de pulsos trapezoidal.

    Flanco de subida empieza en k*period, dura `rise`. Nivel alto dura
    `width`, luego flanco de bajada `fall`. Ancho medido a mid-level =
    width + rise/2 + fall/2.
    """
    t = t0 + np.arange(int(n_periods * period * fs)) / fs
    ph = np.mod(t, period)
    knots_t = [0.0, rise, rise + width, rise + width + fall, period]
    knots_v = [low, high, high, low, low]
    return t, np.interp(ph, knots_t, knots_v)


def sine(freq=1e3, amp=1.0, dc=0.0, phase_deg=0.0, n_periods=5, fs=1e6):
    t = np.arange(int(n_periods * fs / freq)) / fs
    v = dc + amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))
    return t, v


# -----------------------------
# Protocolos digitales
# -----------------------------
# Cada generador construye la trama según la spec, sin usar código del
# decoder. Niveles 0 / 3.3 V.

HI = 3.3


def _expand(states, samples_per_state, fs):
    """Lista de tuplas de niveles lógicos → (t, arrays por canal)."""
    arr = np.repeat(np.array(states, dtype=float), samples_per_state, axis=0)
    t = np.arange(len(arr)) / fs
    return t, [arr[:, k] * HI for k in range(arr.shape[1])]


def uart(data, baud=115200, data_bits=8, parity="none", stop_bits=1,
         idle_bits=3, oversample=20):
    """Bytes LSB primero, idle alto."""
    bits = [1] * idle_bits
    for byte in data:
        word = [(byte >> k) & 1 for k in range(data_bits)]
        bits += [0] + word
        if parity != "none":
            ones = sum(word)
            bits.append(ones % 2 if parity == "even" else 1 - ones % 2)
        bits += [1] * stop_bits
    bits += [1] * idle_bits
    t, (v,) = _expand([(b,) for b in bits], oversample, baud * oversample)
    return t, v


def i2c(address, read, data, nack_last=False, freq=100e3, oversample=40):
    """Transacción 7-bit: START, addr+R/W, ACK, datos, STOP.
    Estados a cuartos de bit: (SDA, SCL)."""
    q = [(1, 1)] * 8                        # idle
    q += [(0, 1)] * 2                       # START: SDA cae con SCL alto
    q += [(0, 0)]

    def send(bit):
        q.extend([(bit, 0), (bit, 1), (bit, 1), (bit, 0)])

    bytes_out = [(address << 1) | int(read)] + list(data)
    for n, byte in enumerate(bytes_out):
        for k in range(7, -1, -1):
            send((byte >> k) & 1)
        last = n == len(bytes_out) - 1
        send(1 if (nack_last and last) else 0)
    q += [(0, 0), (0, 1), (0, 1)]           # SCL sube con SDA bajo
    q += [(1, 1)] * 8                       # STOP: SDA sube con SCL alto
    t, (sda, scl) = _expand(q, oversample // 4, freq * oversample)
    return t, sda, scl


def spi(mosi_words, miso_words, cpol=0, cpha=0, word_bits=8,
        freq=1e6, oversample=20):
    """Frame con CS activo bajo, MSB primero. Estados a medio bit:
    (SCK, MOSI, MISO, CS)."""
    h = [(cpol, 0, 0, 1)] * 4               # CS inactivo
    h += [(cpol, 0, 0, 0)] * 2
    for mw, sw in zip(mosi_words, miso_words):
        for k in range(word_bits - 1, -1, -1):
            mb, sb = (mw >> k) & 1, (sw >> k) & 1
            if cpha == 0:
                h += [(cpol, mb, sb, 0), (1 - cpol, mb, sb, 0)]
            else:
                h += [(1 - cpol, mb, sb, 0), (cpol, mb, sb, 0)]
    h += [(cpol, 0, 0, 0)] * 2
    h += [(cpol, 0, 0, 1)] * 4
    t, (sck, mosi, miso, cs) = _expand(h, oversample // 2, freq * oversample)
    return t, sck, mosi, miso, cs


def crc15_can(bits):
    """CRC-15/CAN, poly 0x4599, init 0 (ISO 11898-1)."""
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def _bits(value, n):
    return [(value >> k) & 1 for k in range(n - 1, -1, -1)]


def _stuff(bits):
    """Tras 5 bits iguales se inserta uno opuesto (cuenta para la racha)."""
    out, run, prev = [], 0, None
    for b in bits:
        out.append(b)
        run = run + 1 if b == prev else 1
        prev = b
        if run == 5:
            out.append(1 - b)
            prev, run = 1 - b, 1
    return out


def can_frame_bits(can_id, data, extended=False, corrupt_crc=False):
    """Trama CAN 2.0 completa (con stuffing) desde SOF hasta EOF."""
    if extended:
        head = ([0] + _bits(can_id >> 18, 11) + [1, 1] +
                _bits(can_id & 0x3FFFF, 18) + [0, 0, 0])
    else:
        head = [0] + _bits(can_id, 11) + [0, 0, 0]
    body = head + _bits(len(data), 4)
    for byte in data:
        body += _bits(byte, 8)
    crc = crc15_can(body) ^ (1 if corrupt_crc else 0)
    stuffed = _stuff(body + _bits(crc, 15))
    return stuffed + [1, 0, 1] + [1] * 7     # delim, ACK, delim, EOF


FD_DLC = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
          9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}
_GRAY3 = [0b000, 0b001, 0b011, 0b010, 0b110, 0b111, 0b101, 0b100]


def crc_msb(bits, poly, width, init):
    """CRC genérico MSB primero. `poly` sin el término x^width."""
    top, mask = 1 << (width - 1), (1 << width) - 1
    crc = init
    for b in bits:
        fb = b ^ (1 if crc & top else 0)
        crc = (crc << 1) & mask
        if fb:
            crc ^= poly
    return crc


def can_fd_frame_bits(can_id, data, extended=False, brs=True, esi=False,
                      corrupt_crc=False, corrupt_sbc=False):
    """Trama CAN FD ISO 11898-1:2015 → lista de (bit, fase).

    fase: "n" = bitrate nominal, "d" = bitrate de datos (solo con BRS).
    """
    dlc = next(k for k, n in FD_DLC.items() if n == len(data))
    if extended:
        arb = ([0] + _bits(can_id >> 18, 11) + [1, 1] +
               _bits(can_id & 0x3FFFF, 18) + [0])
    else:
        arb = [0] + _bits(can_id, 11) + [0, 0]
    ctrl_n = arb + [1, 0, int(brs)]                    # FDF, res, BRS
    ctrl_d = [int(esi)] + _bits(dlc, 4)
    for byte in data:
        ctrl_d += _bits(byte, 8)
    d = "d" if brs else "n"
    raw = [(b, "n") for b in ctrl_n] + [(b, d) for b in ctrl_d]

    # Stuffing dinámico SOF..datos. El stuff bit va ENTRE bits, con la
    # fase del bit siguiente. Al final de los datos no se inserta: ahí va
    # el bit fijo, que tiene el mismo valor.
    stuffed, run, prev, n_dyn = [], 0, None, 0
    for k, (b, ph) in enumerate(raw):
        stuffed.append((b, ph))
        run = run + 1 if b == prev else 1
        prev = b
        if run == 5 and k + 1 < len(raw):
            stuffed.append((1 - b, raw[k + 1][1]))
            prev, run, n_dyn = 1 - b, 1, n_dyn + 1

    gray = _GRAY3[n_dyn % 8] ^ (1 if corrupt_sbc else 0)
    g = _bits(gray, 3)
    sbc = g + [g[0] ^ g[1] ^ g[2]]                       # paridad par

    width, poly = (17, 0x1685B) if len(data) <= 16 else (21, 0x102899)
    crc = crc_msb([b for b, _ in stuffed] + sbc, poly, width,
                  1 << (width - 1))
    crc ^= 1 if corrupt_crc else 0

    # Bits fijos: antes del SBC y tras cada 4 bits de SBC+CRC
    tail = sbc + _bits(crc, width)
    fixed, last = [], stuffed[-1][0]
    for k, b in enumerate(tail):
        if k % 4 == 0:
            fixed.append(1 - last)
            last = 1 - last
        fixed.append(b)
        last = b
    out = stuffed + [(b, d) for b in fixed]
    out += [(b, "n") for b in [1, 0, 1] + [1] * 7]     # delim, ACK, EOF
    return out


def can_fd(frames, nominal=500e3, data_rate=2e6, samples_per_data_bit=16,
           idle_bits=12, classic=None):
    """Tramas CAN FD (y opcionalmente CAN clásicas) en lógica TTL.

    `classic`: lista de dicts para can_frame_bits, puestos antes de las FD.
    """
    fs = data_rate * samples_per_data_bit
    n_nom = int(round(fs / nominal))
    seq = [(1, "n")] * idle_bits
    for fr in classic or []:
        seq += [(b, "n") for b in can_frame_bits(**fr)] + [(1, "n")] * idle_bits
    for fr in frames:
        seq += can_fd_frame_bits(**fr) + [(1, "n")] * idle_bits
    v = np.concatenate([np.full(n_nom if ph == "n" else samples_per_data_bit,
                                b * HI) for b, ph in seq])
    return np.arange(len(v)) / fs, v


def can(frames, bitrate=500e3, oversample=16, idle_bits=12):
    """Varias tramas CAN en lógica TTL (recesivo = alto)."""
    bits = [1] * idle_bits
    for fr in frames:
        bits += can_frame_bits(**fr) + [1] * idle_bits
    t, (v,) = _expand([(b,) for b in bits], oversample, bitrate * oversample)
    return t, v


def peak_detect(t, v, spread):
    """Simula un export en modo peak detect: cada instante sale dos veces,
    como (mín, máx) separados `spread` (ruido dentro del intervalo)."""
    t2 = np.repeat(t, 2)
    v2 = np.empty(2 * len(v))
    v2[0::2] = v - spread / 2
    v2[1::2] = v + spread / 2
    return t2, v2
