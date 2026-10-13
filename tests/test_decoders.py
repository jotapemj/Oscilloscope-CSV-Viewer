"""Decoders de protocolo contra tramas sintéticas según spec."""
import unittest

import numpy as np

import ocv.decoders as dec
from ocv.decoders.can import _can_crc15
from tests import signals as sig


def params(decoder, **over):
    p = {k: v["default"] for k, v in decoder.default_params().items()}
    p.update(over)
    return p


def values(frames, kind):
    return [f["value"] for f in frames if f["type"] == kind]


def errors(frames):
    return [f for f in frames if f["error"]]


class Uart(unittest.TestCase):

    def decode(self, t, v, **over):
        return dec.UartDecoder.decode(t, {"TX": v},
                                      params(dec.UartDecoder, **over))

    def test_bytes(self):
        """'Hi!' + 0x00 + 0xFF a 115200 8N1."""
        t, v = sig.uart(b"Hi!\x00\xff")
        fr = self.decode(t, v, baud=115200)
        self.assertEqual(values(fr, "data"), list(b"Hi!\x00\xff"))
        self.assertEqual(errors(fr), [])

    def test_parity(self):
        """Paridad par correcta; leída como impar se marca error."""
        t, v = sig.uart(b"\x01\x03", parity="even")
        ok = self.decode(t, v, baud=115200, parity="even")
        self.assertEqual(values(ok, "data"), [1, 3])
        self.assertEqual(errors(ok), [])
        bad = self.decode(t, v, baud=115200, parity="odd")
        self.assertEqual(len([f for f in bad if f["type"] == "parity"
                              and f["error"]]), 2)

    def test_inverted(self):
        """RS-232 sin transceiver: señal invertida."""
        t, v = sig.uart(b"\x55")
        fr = self.decode(t, sig.HI - v, baud=115200, invert=True)
        self.assertEqual(values(fr, "data"), [0x55])


class I2C(unittest.TestCase):

    def decode(self, t, sda, scl):
        return dec.I2CDecoder.decode(t, {"SDA": sda, "SCL": scl},
                                     params(dec.I2CDecoder))

    def test_write(self):
        """Escritura a 0x50: addr, 2 datos, ACKs, START/STOP."""
        fr = self.decode(*sig.i2c(0x50, read=False, data=[0xA5, 0x0F]))
        self.assertEqual(values(fr, "address"), [0x50 << 1])
        self.assertEqual(values(fr, "data"), [0xA5, 0x0F])
        self.assertEqual(values(fr, "ack"), [0, 0, 0])
        self.assertEqual(len(values(fr, "start")), 1)
        self.assertEqual(len(values(fr, "stop")), 1)
        self.assertEqual([f["label"] for f in fr if f["type"] == "address"],
                         ["Addr 0x50 W"])

    def test_read_nack(self):
        """Lectura con NACK final del master."""
        fr = self.decode(*sig.i2c(0x3C, read=True, data=[0x12],
                                  nack_last=True))
        self.assertEqual([f["label"] for f in fr if f["type"] == "address"],
                         ["Addr 0x3C R"])
        self.assertEqual(values(fr, "ack"), [0, 1])


class SPI(unittest.TestCase):

    def test_modes(self):
        """Los 4 modos CPOL/CPHA con CS."""
        mosi_w, miso_w = [0x9F, 0x00, 0x3C], [0xFF, 0xEF, 0x40]
        for cpol in (0, 1):
            for cpha in (0, 1):
                with self.subTest(cpol=cpol, cpha=cpha):
                    t, sck, mosi, miso, cs = sig.spi(mosi_w, miso_w,
                                                     cpol=cpol, cpha=cpha)
                    fr = dec.SPIDecoder.decode(
                        t, {"SCK": sck, "MOSI": mosi, "MISO": miso, "CS": cs},
                        params(dec.SPIDecoder, cpol=cpol, cpha=cpha))
                    self.assertEqual(values(fr, "mosi"), mosi_w)
                    self.assertEqual(values(fr, "miso"), miso_w)

    def test_no_cs(self):
        """Sin CS: delimita por idle del reloj; solo MOSI."""
        t, sck, mosi, _, _ = sig.spi([0x12, 0x34], [0, 0])
        fr = dec.SPIDecoder.decode(t, {"SCK": sck, "MOSI": mosi},
                                   params(dec.SPIDecoder))
        self.assertEqual(values(fr, "mosi"), [0x12, 0x34])
        self.assertEqual(values(fr, "miso"), [])

    def test_no_cs_idle_split(self):
        """Sin CS: dos ráfagas separadas por idle > umbral = dos bloques."""
        t1, sck1, mosi1, _, _ = sig.spi([0xA1, 0xB2], [0, 0])
        gap = int(200e-6 * 20e6)           # 200 µs a 20 MS/s
        sck = np.concatenate([sck1, np.zeros(gap), sck1])
        mosi = np.concatenate([mosi1, np.zeros(gap), mosi1])
        t = np.arange(len(sck)) / 20e6
        fr = dec.SPIDecoder.decode(t, {"SCK": sck, "MOSI": mosi},
                                   params(dec.SPIDecoder, idle_time_us=100.0))
        self.assertEqual(values(fr, "mosi"), [0xA1, 0xB2, 0xA1, 0xB2])


class CAN(unittest.TestCase):

    def decode(self, frames, **over):
        t, v = sig.can(frames)
        return dec.CanDecoder.decode(t, {"CAN": v},
                                     params(dec.CanDecoder, **over))

    def test_standard(self):
        """ID 11 bits + 3 bytes, CRC correcto."""
        fr = self.decode([{"can_id": 0x123, "data": [0xDE, 0xAD, 0x01]}])
        self.assertEqual(values(fr, "id"), [0x123])
        self.assertEqual(values(fr, "ctrl"), [3])
        self.assertEqual(values(fr, "data"), [0xDE, 0xAD, 0x01])
        self.assertEqual(errors(fr), [])

    def test_extended(self):
        """ID 29 bits."""
        fr = self.decode([{"can_id": 0x18FEF100, "data": [1, 2],
                           "extended": True}])
        self.assertEqual(values(fr, "id"), [0x18FEF100])
        self.assertEqual(values(fr, "data"), [1, 2])
        self.assertEqual(errors(fr), [])

    def test_stuffing_heavy(self):
        """Datos 0x00/0xFF fuerzan muchos stuff bits."""
        data = [0x00, 0x00, 0xFF, 0xFF, 0x00, 0xFF, 0x00, 0x00]
        fr = self.decode([{"can_id": 0x000, "data": data}])
        self.assertEqual(values(fr, "data"), data)
        self.assertEqual(errors(fr), [])

    def test_two_frames_autobaud(self):
        """Dos tramas seguidas, bitrate automático."""
        fr = self.decode([{"can_id": 0x7FF, "data": [0x55]},
                          {"can_id": 0x001, "data": []}], bitrate=0)
        self.assertEqual(values(fr, "id"), [0x7FF, 0x001])

    def test_bad_crc(self):
        """CRC corrupto → frame marcado como error."""
        fr = self.decode([{"can_id": 0x100, "data": [0x42],
                           "corrupt_crc": True}])
        self.assertEqual([f["type"] for f in errors(fr)], ["crc"])

    def test_garbage_before_frame(self):
        """Ruido antes de una trama válida: se salta, no aborta."""
        t, v = sig.can([{"can_id": 0x2A0, "data": [0x11, 0x22]}])
        rng = np.random.default_rng(3)
        junk = np.repeat(rng.integers(0, 2, 300), 16) * sig.HI
        v = np.concatenate([np.full(16 * 12, sig.HI), junk, v])
        t = np.arange(len(v)) * (t[1] - t[0])
        for cls in (dec.CanDecoder, dec.J1939Decoder, dec.CanFdDecoder):
            with self.subTest(decoder=cls.__name__):
                fr = cls.decode(t, {"CAN": v}, params(cls))
                if cls is dec.CanDecoder:
                    self.assertIn(0x2A0, values(fr, "id"))

    def test_generator_crc(self):
        """CRC del generador == CRC del decoder (vector común)."""
        bits = sig._bits(0x2A5, 15)
        self.assertEqual(sig.crc15_can(bits), _can_crc15(bits))


class CANFD(unittest.TestCase):
    """CAN FD ISO 11898-1:2015 contra el generador de tests/signals.py."""

    def decode(self, frames, nominal=500e3, data_rate=2e6, classic=None):
        t, v = sig.can_fd(frames, nominal=nominal, data_rate=data_rate,
                          classic=classic)
        p = params(dec.CanFdDecoder, nominal_bitrate=int(nominal),
                   data_bitrate=int(data_rate))
        return dec.CanFdDecoder.decode(t, {"CAN": v}, p)

    def check(self, fr, can_id, data):
        self.assertEqual(values(fr, "id"), [can_id])
        self.assertEqual(values(fr, "data"), list(data))
        self.assertEqual(errors(fr), [], [f["label"] for f in errors(fr)])

    def test_generator_self_check(self):
        """Generador: CRC(mensaje + CRC) = 0 para CRC17 y CRC21."""
        msg = sig._bits(0x1234567, 27)
        for width, poly in ((17, 0x1685B), (21, 0x102899)):
            init = 1 << (width - 1)
            crc = sig.crc_msb(msg, poly, width, init)
            self.assertEqual(
                sig.crc_msb(msg + sig._bits(crc, width), poly, width, init), 0)

    def test_standard_brs(self):
        """ID 11 bits, 8 bytes, BRS 500k/2M, CRC17."""
        data = [0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF]
        fr = self.decode([{"can_id": 0x456, "data": data}])
        self.check(fr, 0x456, data)
        self.assertEqual(values(fr, "ctrl"), [8])

    def test_extended_crc21(self):
        """ID 29 bits, 20 bytes (DLC 11) → CRC21."""
        data = list(range(20))
        fr = self.decode([{"can_id": 0x1ABCDEF0, "data": data,
                           "extended": True}])
        self.check(fr, 0x1ABCDEF0, data)
        self.assertEqual(values(fr, "ctrl"), [11])

    def test_lengths(self):
        """Todos los tamaños de payload FD, de 0 a 64 bytes."""
        for n in sig.FD_DLC.values():
            with self.subTest(n=n):
                data = [(k * 37 + n) & 0xFF for k in range(n)]
                self.check(self.decode([{"can_id": 0x0F0, "data": data}]),
                           0x0F0, data)

    def test_no_brs(self):
        """BRS=0: todo a bitrate nominal."""
        data = [0xAA, 0x55, 0x0F]
        self.check(self.decode([{"can_id": 0x321, "data": data,
                                 "brs": False}]), 0x321, data)

    def test_heavy_stuffing(self):
        """0x00/0xFF en ID y datos: stuffing dinámico denso."""
        data = [0x00] * 6 + [0xFF] * 6 + [0x00] * 4
        self.check(self.decode([{"can_id": 0x000, "data": data}]),
                   0x000, data)
        self.check(self.decode([{"can_id": 0x7FF, "data": [0xFF] * 12}]),
                   0x7FF, [0xFF] * 12)

    def test_fast_data_phase(self):
        """1 Mbit/s nominal, 8 Mbit/s datos, 64 bytes."""
        data = [k ^ 0x5A for k in range(64)]
        self.check(self.decode([{"can_id": 0x123, "data": data}],
                               nominal=1e6, data_rate=8e6), 0x123, data)

    def test_clock_skew(self):
        """Emisor ±2 % fuera del bitrate configurado: resync en flancos."""
        data = list(range(64))
        p = params(dec.CanFdDecoder)
        for skew in (0.98, 1.02):
            with self.subTest(skew=skew):
                t, v = sig.can_fd([{"can_id": 0x123, "data": data}],
                                  nominal=500e3 * skew, data_rate=2e6 * skew)
                self.check(dec.CanFdDecoder.decode(t, {"CAN": v}, p),
                           0x123, data)

    def test_noise(self):
        """Ruido gaussiano de 0.3 V sobre 3.3 V."""
        data = list(range(64))
        t, v = sig.can_fd([{"can_id": 0x123, "data": data}])
        v = v + np.random.default_rng(0).normal(0, 0.3, len(v))
        self.check(dec.CanFdDecoder.decode(t, {"CAN": v},
                                           params(dec.CanFdDecoder)),
                   0x123, data)

    def test_back_to_back_and_classic(self):
        """Clásica + dos FD seguidas: solo salen las FD."""
        fr = self.decode([{"can_id": 0x101, "data": [1, 2, 3]},
                          {"can_id": 0x102, "data": list(range(32))}],
                         classic=[{"can_id": 0x100, "data": [9]}])
        self.assertEqual(values(fr, "id"), [0x101, 0x102])
        self.assertEqual(errors(fr), [])

    def test_bad_crc(self):
        """CRC corrupto → error en el campo CRC."""
        fr = self.decode([{"can_id": 0x200, "data": [7] * 12,
                           "corrupt_crc": True}])
        self.assertEqual([f["type"] for f in errors(fr)], ["crc"])

    def test_bad_stuff_count(self):
        """Stuff count corrupto → error aunque el CRC cuadre."""
        fr = self.decode([{"can_id": 0x200, "data": [7] * 12,
                           "corrupt_sbc": True}])
        self.assertTrue(errors(fr))

    def test_esi(self):
        """ESI=1 se refleja en la etiqueta de control."""
        fr = self.decode([{"can_id": 0x300, "data": [1], "esi": True}])
        ctrl = [f["label"] for f in fr if f["type"] == "ctrl"]
        self.assertEqual(len(ctrl), 1)
        self.assertIn("ESI", ctrl[0])


if __name__ == "__main__":
    unittest.main()
