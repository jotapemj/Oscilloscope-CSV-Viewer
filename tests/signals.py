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


def can(frames, bitrate=500e3, oversample=16, idle_bits=12):
    """Varias tramas CAN en lógica TTL (recesivo = alto)."""
    bits = [1] * idle_bits
    for fr in frames:
        bits += can_frame_bits(**fr) + [1] * idle_bits
    t, (v,) = _expand([(b,) for b in bits], oversample, bitrate * oversample)
    return t, v
