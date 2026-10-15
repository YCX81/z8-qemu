# SPDX-License-Identifier: GPL-2.0-or-later
"""U16 storage to F0, independent full-sample decoding; no native NEF claim."""
import ctypes as C
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest

from test_lossless14_codec import unpack_f0

ROOT = Path(__file__).resolve().parents[1]


class LosslessU16(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='lossless-u16-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.encoders = []
        for opt in ('-O0', '-O2'):
            target = Path(cls.tmp.name) / ('codec' + opt + '.so')
            subprocess.run(['cc', '-shared', '-fPIC', '-std=c11', '-Wall',
                '-Wextra', '-Werror', opt, '-I', str(ROOT/'qemu'),
                str(ROOT/'tests/lossless_u16_wrapper.c'), '-o', str(target)],
                check=True, capture_output=True)
            f = C.CDLL(str(target)).lossless_u16_test
            f.argtypes = [C.c_void_p, C.c_size_t, C.c_uint, C.c_uint,
                C.c_uint, C.c_void_p, C.c_void_p, C.c_size_t, C.c_void_p]
            f.restype = C.c_bool
            cls.encoders.append(f)

    def encode(self, f, rows, *, padding=0xa5, seeds=(2048,)*4,
               capacity=None, overrides=None):
        width, height = len(rows[0]), len(rows)
        stride = (width*2 + 31) & ~31
        body = b''.join(struct.pack('<'+'H'*width, *row) +
                       bytes([padding])*(stride-width*2) for row in rows)
        cap = len(body)*2+64 if capacity is None else capacity
        source = C.create_string_buffer(body)
        output = (C.c_ubyte*(cap+32))(*([0x69]*(cap+32)))
        predictors = (C.c_uint16*4)(*seeds)
        written = C.c_size_t(12345)
        args = [source, len(body), width, height, stride, predictors,
                output, cap, C.byref(written)]
        for key, value in (overrides or {}).items():
            args[key] = value
        ok = f(*args)
        self.assertEqual(bytes(source)[:-1], body, 'Input changed')
        self.assertEqual(bytes(output[cap:]), bytes([0x69])*32)
        if not ok:
            self.assertEqual(written.value, 0)
        return ok, bytes(output[:written.value])

    def test_all_14bit_samples_and_predictor_extremes(self):
        values = list(range(16384))
        random.Random(560).shuffle(values)
        fixtures = [[values[i:i+256] for i in range(0, len(values), 256)],
                    [[0, 16383, 16383, 0]*16 for _ in range(16)]]
        for f in self.encoders:
            for rows in fixtures:
                ok, data = self.encode(f, rows)
                self.assertTrue(ok)
                self.assertEqual(unpack_f0(data, len(rows[0]), len(rows), (2048,)*4), rows)

    def test_current_hsfc_width_and_storage_padding(self):
        rows = [[(x*17+y*23) & 16383 for x in range(4140)] for y in range(6)]
        seeds = (0, 1, 2, 3)
        for f in self.encoders:
            ok, a = self.encode(f, rows, padding=0, seeds=seeds)
            ok2, b = self.encode(f, rows, padding=255, seeds=seeds)
            self.assertTrue(ok and ok2)
            self.assertEqual(a, b)
            self.assertEqual(unpack_f0(a, 4140, 6, seeds), rows)

    def test_invalid_high_bits_are_rejected_without_length(self):
        for f in self.encoders:
            for invalid in (16384, 32768, 65535):
                rows = [[0]*12 for _ in range(4)]
                rows[-1][-1] = invalid
                self.assertFalse(self.encode(f, rows)[0])

    def test_capacity_and_geometry_rejection(self):
        rows = [[0, 1, 2, 3]*8 for _ in range(4)]
        for f in self.encoders:
            for cap in (0, 1, 4):
                self.assertFalse(self.encode(f, rows, capacity=cap)[0])
            for overrides in ({0:None}, {1:1}, {2:0}, {2:3}, {2:16388},
                              {3:0}, {3:16385}, {4:0}, {4:1}, {5:None}, {6:None}):
                self.assertFalse(self.encode(f, rows, overrides=overrides)[0])
            self.assertFalse(self.encode(f, rows, seeds=(0, 1, 2, 16384))[0])


if __name__ == '__main__':
    unittest.main()
