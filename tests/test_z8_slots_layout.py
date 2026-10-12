# SPDX-License-Identifier: GPL-2.0-or-later
"""Capacity and namespace invariants, independent of firmware execution."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_slots_layout import Layout


class LayoutTests(unittest.TestCase):
    def test_c32_and_c64_have_stable_noncolliding_keys_and_selectors(self):
        a, b = Layout(32), Layout(64)
        for bank in (0, 1):
            for slot in range(1, 33):
                self.assertEqual(a.selector(slot, bank), b.selector(slot, bank))
                self.assertEqual(a.keys(slot, bank), b.keys(slot, bank))
                self.assertEqual(a.record(slot, bank), b.record(slot, bank))
        selectors = [b.selector(s, m) for m in (0, 1) for s in range(1, 65)]
        self.assertEqual(len(set(selectors)), 128)
        self.assertFalse(set(selectors) & (set(range(0, 31)) | set(range(50, 81)) | set(range(400, 701))))
        keys = [k for m in (0, 1) for s in range(1, 65) for k in b.keys(s, m)]
        self.assertEqual(len(set(keys)), 384)
        self.assertEqual(b.code(9), 36)
        self.assertEqual(b.code(10), 40)
        self.assertEqual(b.code(64), 94)
        self.assertEqual([b.decode(v) for v in selectors], [(s,m) for m in (0,1) for s in range(1,65)])

    def test_full_c64_state_fits_two_blocks_per_copy_without_crossing_io(self):
        layout = Layout(32)
        self.assertEqual(layout.record_bytes, Layout(64).record_bytes)
        self.assertEqual(layout.record_bytes % 2048, 0)
        self.assertGreaterEqual(layout.record_bytes, 128 + 128 * 1024)
        self.assertEqual(layout.payload_chunks, ((1, 63, 0), (64, 2, 63 * 2048)))
        pages = set()
        for base in layout.media_bases:
            owned = {base+i for i in range(128)}
            self.assertFalse(pages & owned); pages |= owned
            for first,count,_ in layout.payload_chunks:
                self.assertEqual((base+first)//64, (base+first+count-1)//64)
        self.assertLess(max(pages), 2048*64)

    def test_region_budgets_are_disjoint_inside_explicit_reservation(self):
        layout = Layout(64)
        spans = sorted((v[0],v[0]+v[1],k) for k,v in layout.regions.items())
        for left,right in zip(spans, spans[1:]):
            self.assertLessEqual(left[1],right[0],(left,right))
        self.assertGreaterEqual(spans[0][0],layout.data_base)
        self.assertLessEqual(spans[-1][1],layout.data_base+layout.reserved_bytes)
        self.assertEqual(layout.general_bytes,0xA00000)

    def test_invalid_or_hidden_slots_are_not_accepted(self):
        for n in (0, 10, 31, 33, 65, True):
            with self.assertRaises(ValueError): Layout(n)
        a = Layout(32)
        for slot, bank in ((0,0),(33,0),(64,1),(1,2),(True,0)):
            with self.assertRaises(ValueError): a.selector(slot,bank)
        for value in (40,90,0x1000,0x2000,0x1020,0x2040,0xFFFFFFFF):
            self.assertIsNone(a.decode(value))


if __name__ == '__main__': unittest.main()
