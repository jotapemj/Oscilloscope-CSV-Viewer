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

    def test_generator_crc(self):
        """CRC del generador == CRC del decoder (vector común)."""
        bits = sig._bits(0x2A5, 15)
        self.assertEqual(sig.crc15_can(bits), _can_crc15(bits))


if __name__ == "__main__":
    unittest.main()
