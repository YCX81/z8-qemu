# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent zlib/struct check of an authored, non-Nikon simulation packet."""
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]


class BayerPacketHelpers(unittest.TestCase):
    def check(self, optimized):
        with tempfile.TemporaryDirectory(prefix='bayer-packet-') as folder:
            folder = Path(folder)
            binary, output = folder / 'packet-test', folder / 'packet.bin'
            command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                       *(['-O2', '-DNDEBUG'] if optimized else ['-O0']),
                       '-I', str(ROOT / 'qemu'), str(ROOT / 'tests/test_bayer_packet.c'),
                       '-lz', '-o', str(binary)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run([str(binary), str(output)], capture_output=True, text=True)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn('bayer packet scenarios passed: 12', tested.stdout)
            packet = output.read_bytes()
            self.assertEqual(packet[:8], b'NZ8BAYR1')
            self.assertEqual(struct.unpack_from('<6I', packet, 8), (80, 1, 64, 32, 128, 14))
            raw_size, encoded_size, frame, timestamp = struct.unpack_from('<4Q', packet, 32)
            self.assertEqual((raw_size, frame, timestamp), (4096, 7, 123456789))
            raw_crc, encoded_crc, total_size, header_crc = struct.unpack_from('<4I', packet, 64)
            self.assertEqual(total_size, len(packet))
            self.assertEqual(header_crc, zlib.crc32(packet[:76]))
            encoded = packet[80:80 + encoded_size]
            self.assertEqual(encoded_crc, zlib.crc32(encoded))
            original = bytes((i * 13 + (i // 128) * 7) & 255 for i in range(4096))
            self.assertEqual(zlib.decompress(encoded), original)
            self.assertEqual(raw_crc, zlib.crc32(original))
            self.assertFalse(any(packet[80 + encoded_size:]))

    def test_normal(self):
        self.check(False)

    def test_optimized(self):
        self.check(True)


if __name__ == '__main__':
    unittest.main()
