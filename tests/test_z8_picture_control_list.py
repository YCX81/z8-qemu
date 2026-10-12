# SPDX-License-Identifier: GPL-2.0-or-later
"""Original nine-byte callers must safely rebuild all ten owned C10 slots."""
import copy
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc
import z8_picture_control_list as listing


class ListPlanContract(unittest.TestCase):
    def test_wrong_main_is_rejected(self):
        with self.assertRaises(ValueError): listing.build_list_plan(bytes(64))


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to the original C3.11 MAIN')
class OriginalListContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.source = Path(os.environ['Z8_MAIN']).read_bytes()
    def setUp(self):
        self.g = pc.PictureControl(self.source); self.addCleanup(self.g.close)
    def install(self):
        plan = listing.build_list_plan(self.source, core_plan=self.g.plan)
        for patch in plan['patches']:
            self.assertEqual(bytes(self.g.uc.mem_read(patch['address'], len(patch['expected']))), patch['expected'])
            self.g.uc.mem_write(patch['address'], patch['replacement'])
        for region in plan['regions']:
            self.g.uc.mem_write(region['address'], region['data'])
        return plan
    def owned(self, slots, mode=0):
        for slot in range(1, 11):
            self.g.uc.mem_write(pc._record(slot, mode), struct.pack('<I', int(slot in slots)))
    def output(self, mode=0):
        descriptor = 0xa02983cb0 + mode * 0x18
        total = struct.unpack('<I', self.g.uc.mem_read(descriptor + 8, 4))[0]
        self.assertTrue(31 <= total <= 41)
        return list(struct.unpack('<' + 'I' * (total - 31),
                    self.g.uc.mem_read((pc.LIST1 if mode else pc.LIST0) + 0x7c, 4 * (total - 31))))

    def test_caller_nine_bytes_at_unmapped_boundary_cannot_be_read_as_ten(self):
        self.g.uc.mem_map(0x47000000, 0x1000)
        pointer = 0x47000ff7
        self.g.uc.mem_write(pointer, b'\x01' * 9)
        self.owned({10})
        with self.assertRaises(RuntimeError): self.g.call(listing.NATIVE_LIST, pointer, 0)
        # Start the candidate in a fresh instruction fixture; the baseline
        # stopped mid-function and Unicorn may cache its translated entry.
        self.g.close(); self.g = pc.PictureControl(self.source); self.addCleanup(self.g.close)
        self.g.uc.mem_map(0x47000000, 0x1000)
        self.g.uc.mem_write(pointer, b'\x01' * 9); self.owned({10})
        self.install()
        self.g.call(listing.NATIVE_LIST, pointer, 0)
        self.assertEqual(self.output(), [40])
        self.assertEqual(bytes(self.g.uc.mem_read(pointer, 9)), b'\x01' * 9)
        self.assertTrue(self.g.guards_intact())

    def test_no_tenth_only_tenth_and_all_ten_use_owned_flags_in_each_bank(self):
        self.install()
        for mode in (0, 1):
            for slots in (set(), {1, 9}, {10}, set(range(1, 11))):
                with self.subTest(mode=mode, slots=slots):
                    self.owned(slots, mode)
                    self.g.call(listing.NATIVE_LIST, 0, mode)  # Entire caller bitmap may be absent.
                    self.assertEqual(self.output(mode), [40 if s == 10 else 27 + s for s in sorted(slots)])
                    self.assertTrue(self.g.guards_intact())
        self.assertIn(listing.TRAMPOLINE, self.g.executed)
        self.assertIn(listing.NATIVE_LIST + 4, self.g.executed)
        self.assertIn(0x410aada4, self.g.executed)  # Original publication call.
        self.assertIn(0x410aadb4, self.g.executed)  # Original mutex-release tail.

    def test_original_rebuild_caller_still_executes_both_banks(self):
        self.install(); self.owned({2, 10}, 0); self.owned({8}, 1)
        # Original caches deliberately disagree with restored owned records.
        for base in (0xa047fbf68, 0xa048016e8):
            for index in range(9):
                self.g.uc.mem_write(base + 0x21e8 + index * 0x118, struct.pack('<I', 1))
        original = self.source[listing.NATIVE_REBUILD - pc.BASE:listing.NATIVE_REBUILD - pc.BASE + 0xbc]
        self.assertEqual(bytes(self.g.uc.mem_read(listing.NATIVE_REBUILD, len(original))), original)
        self.g.call(listing.NATIVE_REBUILD)
        self.assertEqual(self.output(0), [29, 40]); self.assertEqual(self.output(1), [35])
        self.assertTrue({0x410d9578, 0x410d95c8}.issubset(self.g.executed))

    def test_valid_flag_must_equal_one_and_invalid_mode_keeps_original_noop(self):
        self.install(); self.owned({3}, 0)
        self.g.uc.mem_write(pc._record(10), struct.pack('<I', 2))
        self.g.call(listing.NATIVE_LIST, 0, 0)
        self.assertEqual(self.output(), [30])
        before = bytes(self.g.uc.mem_read(pc.LIST0, 0x200))
        self.g.call(listing.NATIVE_LIST, 0, 2)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.LIST0, 0x200)), before)
        self.assertTrue(self.g.guards_intact())

    def test_plan_only_uses_reserved_zero_core_tail_and_one_guarded_entry_patch(self):
        plan = self.install()
        self.assertEqual([p['address'] for p in plan['patches']], [listing.NATIVE_LIST])
        patch = plan['patches'][0]
        self.assertEqual(patch['expected'], bytes.fromhex('f553bea9'))
        self.assertEqual(len(patch['replacement']), 4)
        self.assertEqual(len(plan['regions']), 1)
        region = plan['regions'][0]
        self.assertEqual(region['address'], 0x45003a00)
        self.assertEqual(len(region['data']), 0x600)
        broken = copy.deepcopy(self.g.plan)
        target = next(r for r in broken['regions'] if r['address'] == pc.CODE_BASE)
        altered = bytearray(target['data']); altered[0x3a00] = 1; target['data'] = bytes(altered)
        with self.assertRaisesRegex(ValueError, 'zero'):
            listing.build_list_plan(self.source, core_plan=broken)

    def test_plan_checks_its_core_dependencies_and_can_build_them(self):
        self.assertEqual(listing.build_list_plan(self.source)['caller_bitmap_bytes_read'], 0)
        broken = copy.deepcopy(self.g.plan); broken['regions'] = []
        with self.assertRaisesRegex(ValueError, 'zero core tail'):
            listing.build_list_plan(self.source, core_plan=broken)
        broken = copy.deepcopy(self.g.plan)
        broken['patches'] = [p for p in broken['patches'] if p['address'] != 0x410aad14]
        with self.assertRaisesRegex(ValueError, 'ten-entry'):
            listing.build_list_plan(self.source, core_plan=broken)


if __name__ == '__main__': unittest.main()
