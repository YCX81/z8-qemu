# SPDX-License-Identifier: GPL-2.0-or-later
"""NP3 preflight accepts only the original zero-key terminator.

The original main parser reads a 4-byte key at 0x410add94 and stops on zero
(0x410add9c cbz).  Exported NP3 files end with exactly such a 4-byte zero key.
External real exports are not stored in the repository; set Z8_REAL_NP3_DIR
to a directory containing them to run the real-file cases.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc
from z8_slots_core import NativeSlots
from z8_slots_layout import Layout

REAL = {
    'ClrMode_Gn_01a.NP3': (
        '5ef3dafc6885d39d7e34a7e62b0b4131660e54579d9d8e5f04fdbaa05fd1e7b8', 'ClrMode_Gn_01a',
        'd0808080769e9471828676787f8882788072869e85847388768a856e7682008080800080808000808080b280'),
    'IndoorHarmony_Julia.NP3': (
        '5c47e9adcc5e71de44ca7a195667395e8491f10131c726208b9df4989448096f', 'IndoorHarmony_Julia',
        '6254554191808a80806e808075708072668084a080809e80806a74808080e5808b8024808b8030809180b280'),
}


class _Memory:
    def __init__(self): self.writes = []
    def mem_write(self, address, data): self.writes.append((address, bytes(data)))


class _PictureControlInput:
    def __init__(self): self.uc = _Memory()
    run = pc.PictureControl._input


class _SlotsInput:
    def __init__(self, count): self.uc = _Memory(); self.layout = Layout(count)
    run = NativeSlots._input


def _entries():
    return (('PictureControl', _PictureControlInput, 'Truncated NP3 TLV header'),
            ('NativeSlots32', lambda: _SlotsInput(32), 'TLV header'),
            ('NativeSlots64', lambda: _SlotsInput(64), 'TLV header'))


def _real_files():
    directory = os.environ.get('Z8_REAL_NP3_DIR')
    if not directory:
        return None
    result = {}
    for name, (digest, label, extra) in REAL.items():
        data = (Path(directory) / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise AssertionError(f'{name} is not the pinned real export')
        result[name] = (data, label, bytes.fromhex(extra))
    return result


class PaddingPreflight(unittest.TestCase):
    def check_accepts(self, blob):
        for name, factory, _ in _entries():
            with self.subTest(entry=name, size=len(blob)):
                target = factory()
                self.assertEqual(target.run(blob), len(blob))
                # The original bytes, terminator included, reach the native reader.
                self.assertEqual(target.uc.writes[0][1], blob)

    def check_rejects(self, blob, pattern=None):
        for name, factory, message in _entries():
            with self.subTest(entry=name, size=len(blob)):
                target = factory()
                with self.assertRaisesRegex(ValueError, pattern or message):
                    target.run(blob)
                self.assertEqual(target.uc.writes, [])

    def test_fixture_unchanged_and_accepted(self):
        blob = pc.make_flexible_np3('PAD FIXTURE', seed=19)
        self.assertEqual(len(blob), 954)
        self.assertEqual(blob, pc.make_flexible_np3('PAD FIXTURE', seed=19))
        self.check_accepts(blob)
        self.check_accepts(pc.make_np3('PAD BASIC', seed=3))

    def test_zero_key_terminator_of_4_to_7_bytes_is_accepted(self):
        base = pc.make_flexible_np3('PAD ZERO', seed=19)
        for size in range(4, 8):
            self.check_accepts(base + bytes(size))

    def test_short_zero_tail_is_still_truncation(self):
        base = pc.make_flexible_np3('PAD SHORT', seed=19)
        for size in range(1, 4):
            self.check_rejects(base + bytes(size))

    def test_nonzero_tail_is_still_truncation(self):
        base = pc.make_flexible_np3('PAD NONZERO', seed=19)
        for size in range(4, 8):
            for position in range(size):
                tail = bytearray(size); tail[position] = 1
                self.check_rejects(base + bytes(tail))
        self.check_rejects(base + b'\x01\x02\x03')

    def test_body_truncation_unchanged(self):
        base = pc.make_flexible_np3('PAD BODY', seed=19)
        self.check_rejects(base + struct.pack('>II', 0x1000, 8) + b'\0' * 4, 'TLV (body|data)')
        self.check_rejects(base[:-1], 'TLV (body|data)')

    def test_real_exports(self):
        real = _real_files()
        if real is None:
            self.skipTest('Set Z8_REAL_NP3_DIR to run real-export cases')
        for name, (blob, _, _) in real.items():
            with self.subTest(file=name):
                self.assertEqual(len(blob), 392)
                self.assertEqual(blob[0x184:], bytes(4))
                self.check_accepts(blob)


@unittest.skipUnless(os.environ.get('Z8_MAIN') and os.environ.get('Z8_REAL_NP3_DIR'),
                     'Set Z8_MAIN and Z8_REAL_NP3_DIR')
class RealExportsOriginalA64(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()
        cls.real = _real_files()

    def test_original_parser_builds_pinned_color44(self):
        for name, (blob, label, extra) in self.real.items():
            with self.subTest(file=name):
                g = pc.PictureControl(self.source); self.addCleanup(g.close)
                parsed = g.parse_np3(blob)
                self.assertEqual(parsed.extra, extra)
                selected = g.import_np3(10, blob)
                self.assertEqual(selected.extra, extra)
                self.assertEqual(selected.main[:20].split(b'\0')[0], label.encode())

    def test_original_slot_import_c1_c32_c64(self):
        for count, slots in ((32, (1, 32)), (64, (64,))):
            for slot in slots:
                for name, (blob, label, extra) in self.real.items():
                    with self.subTest(layout=count, slot=slot, file=name):
                        g = NativeSlots(self.source, Layout(count)); self.addCleanup(g.close)
                        before = g.records()
                        raw = g.import_np3(slot, blob, 0)
                        self.assertEqual(raw[0x10:0x24].split(b'\0')[0], label.encode())
                        self.assertEqual(struct.unpack_from('<H', raw, 0x120)[0], 32)
                        self.assertEqual(raw[0x370:0x39C], extra)
                        selector, current = g.current_selection(0)
                        self.assertEqual(selector, g.layout.selector(slot, 0))
                        self.assertEqual(current[0x12C:], extra)
                        after = g.records(); at = g.layout.record(slot, 0) - g.layout.address('records')
                        # The 128-byte header carries the selection; compare every other record.
                        head = g.layout.header_bytes
                        self.assertEqual(before[head:at], after[head:at])
                        self.assertEqual(before[at + 0x400:], after[at + 0x400:])


if __name__ == '__main__':
    unittest.main()
