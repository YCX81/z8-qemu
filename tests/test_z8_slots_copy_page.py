# SPDX-License-Identifier: GPL-2.0-or-later
"""IND-1: copy-to-camera destination page (opt-in extend_copy_page). Simulation only."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_slots_core import NativeSlots
from z8_slots_layout import Layout
from z8_slots_main_menu import build_main_menu_plan

OLD_PAIRS, OTHER_PAIRS = 0x42B2BE48, 0x42B2BD70
FC_LABELS = 0x42B2C930
DRAW, DRAW_RESUME = 0x405FEF34, 0x405FEF38
VALID, VALID_RESUME = 0x405FF7AC, 0x405FF7B0
LABEL_ENTRY, SETTER = 0x405FF058, 0x403F645C


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Pinned original MAIN required')
class CopyPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def fixture(self, n=32):
        g = NativeSlots(self.source, Layout(n)); self.addCleanup(g.close)
        g.uc.mem_map(0xA03200000, 0x300000)
        plan = build_main_menu_plan(self.source, g.layout, extend_copy_page=True)
        for r in plan['regions']:
            if r['permissions'] == 'rx':
                g.uc.mem_map(r['address'], len(r['data']))
            g.uc.mem_write(r['address'], r['data'])
        for p in plan['patches']:
            self.assertEqual(bytes(g.uc.mem_read(p['address'], len(p['expected']))), p['expected'])
            g.uc.mem_write(p['address'], p['replacement'])
        return g, plan

    def step(self, g, entry, until, **regs):
        for name, value in regs.items():
            g.uc.reg_write(getattr(g.r, 'UC_ARM64_REG_' + name), value)
        g.uc.reg_write(g.r.UC_ARM64_REG_SP, g.layout.address('stack') + 0x8000)
        g.uc.emu_start(entry, until, count=100)
        self.assertEqual(g.uc.reg_read(g.r.UC_ARM64_REG_PC), until)

    def reg(self, g, name):
        return g.uc.reg_read(getattr(g.r, 'UC_ARM64_REG_' + name))

    def test_row_loops_switch_only_the_copy_table(self):
        for n in (32, 64):
            g, p = self.fixture(n)
            page = p['copy_page']
            self.step(g, DRAW, DRAW_RESUME, X21=OLD_PAIRS, X22=0)
            self.assertEqual((self.reg(g, 'X21'), self.reg(g, 'X22')), (page['pairs'], n))
            self.step(g, VALID, VALID_RESUME, X22=OLD_PAIRS, X8=0)
            self.assertEqual((self.reg(g, 'X22'), self.reg(g, 'X8') & 0xffffffff), (page['pairs'], n))

    def test_other_tables_keep_original_path_and_count(self):
        g, _ = self.fixture(32)
        for table in (OTHER_PAIRS, 0x42B2BFF8, 0x42B2C358, 0x42B2C1A8):
            self.step(g, DRAW, DRAW_RESUME, X21=table, X22=0)
            self.assertEqual((self.reg(g, 'X21'), self.reg(g, 'X22')), (table, 9))
            self.step(g, VALID, VALID_RESUME, X22=table, X8=0)
            self.assertEqual((self.reg(g, 'X22'), self.reg(g, 'X8') & 0xffffffff), (table, 9))

    def test_c1_to_c9_indices_are_unchanged(self):
        g, p = self.fixture(32)
        old = self.source[OLD_PAIRS - 0x40160000:OLD_PAIRS - 0x40160000 + 9 * 8]
        new = bytes(g.uc.mem_read(p['copy_page']['pairs'], 9 * 8))
        self.assertEqual(new, old)
        for i in range(9):
            key = struct.unpack_from('<I', old, i * 8 + 4)[0]
            self.assertEqual(g.call(0x403EB788, OLD_PAIRS, 9, key, 0xFFFFFFFF), i)
            self.assertEqual(g.call(0x403EB788, p['copy_page']['pairs'], 32, key, 0xFFFFFFFF), i)

    def test_extended_selector_label_never_reads_fc_table(self):
        from unicorn import UC_HOOK_MEM_READ
        g, _ = self.fixture(32)
        reads = []
        hook = g.uc.hook_add(UC_HOOK_MEM_READ, lambda uc, access, address, size, value, data: reads.append(address),
                             begin=FC_LABELS, end=FC_LABELS + 9 * 8 - 1)
        self.addCleanup(lambda: g.uc and g.uc.hook_del(hook))
        sp = g.layout.address('stack') + 0x8000
        for slot in (10, 32):
            reads.clear()
            g.uc.mem_write(sp + 0x4C, b'\x01')
            g.uc.reg_write(g.r.UC_ARM64_REG_SP, sp)
            for name, value in dict(X0=0x20, X27=g.layout.selector(slot, 0), X25=1).items():
                g.uc.reg_write(getattr(g.r, 'UC_ARM64_REG_' + name), value)
            g.uc.emu_start(LABEL_ENTRY, 0x405FF0F4, count=200)
            self.assertEqual(self.reg(g, 'PC'), 0x405FF0F4)
            self.assertEqual(self.reg(g, 'X1') & 0xffffffff, g.layout.label_id(slot, 1))
            self.assertEqual(reads, [])


if __name__ == '__main__':
    unittest.main()
