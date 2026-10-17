# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free checks plus an explicitly opted-in external-source run."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import z8_he_history_audit as audit


class SyntheticFixtures(unittest.TestCase):
    def test_config_crc_and_ceiling(self):
        for frames in (1, 5, 9, 15, 30):
            c = audit.config(frames)
            self.assertEqual(len(c), 64)
            self.assertEqual(struct.unpack_from('<I', c, 20)[0], frames)
            self.assertEqual(zlib.crc32(c[:60]), struct.unpack_from('<I', c, 60)[0])

    def test_invalid_ceiling_rejected(self):
        for frames in (0, 31, -1):
            with self.assertRaisesRegex(ValueError, 'ceiling'):
                audit.config(frames)

    def test_authored_tiff_offsets_and_inline_values(self):
        raw = audit.tiff([(1, 4, 1, struct.pack('<I', 37)), (2, 7, 6, b'abcdef')])
        self.assertEqual(raw[:8], b'II*\0\x08\0\0\0')
        self.assertEqual(struct.unpack_from('<I', raw, 18)[0], 37)
        offset = struct.unpack_from('<I', raw, 30)[0]
        self.assertEqual(raw[offset:offset + 6], b'abcdef')

    def test_header_has_bounded_app1_and_mpf_size_binding(self):
        h = audit.synthetic_header()
        self.assertGreaterEqual(len(h), 0x400)
        app1 = int.from_bytes(h[4:6], 'big')
        mpf_at = 4 + app1
        self.assertEqual(h[mpf_at:mpf_at + 2], b'\xff\xe2')
        mpf = h[mpf_at + 8:]
        offset = struct.unpack_from('<I', mpf, 18)[0]
        sizes = [struct.unpack_from('<I', mpf, offset + i * 16 + 4)[0] for i in range(3)]
        self.assertEqual(sizes, [len(h) + 2, 4, 4])

    def test_pin_has_three_distinct_hooks_and_no_payload(self):
        pin = json.loads(audit.PIN.read_text())
        self.assertEqual(len(set(pin['hooks'].values())), 3)
        self.assertEqual(len(pin['commit']), 40)
        self.assertTrue(all(len(x) == 64 for x in pin['source_sha256'].values()))

    def test_service_addresses_are_distinct(self):
        self.assertEqual(len(set(audit.SERVICES.values())), len(audit.SERVICES))

    def test_source_drift_rejected_before_compiler(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'z8precapture'
            path.mkdir()
            (path / 'firmware.c').write_text('not the pinned source')
            with mock.patch.object(audit.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Source drift'):
                    audit.compile_source(temp, 'unused-compiler')
            run.assert_not_called()


@unittest.skipUnless(os.environ.get('Z8_HE_SOURCE') and os.environ.get('Z8_HE_CLANG'),
                     'External pinned source and compiler are opt-in; no downloads in unit tests')
class ExternalHooks(unittest.TestCase):
    def test_bounded_execution(self):
        code, symbols, provenance = audit.compile_source(os.environ['Z8_HE_SOURCE'], os.environ['Z8_HE_CLANG'])
        rows = audit.run_cases(code, symbols)
        self.assertTrue(provenance['source_hashes_verified'])
        self.assertGreaterEqual(len(rows), 30)
        self.assertEqual(sum(r['result'] == 'known_limitation_reproduced' for r in rows), 2)
        self.assertEqual(sum(r['result'] == 'call_once_precondition_reproduced' for r in rows), 3)


if __name__ == '__main__':
    unittest.main()
