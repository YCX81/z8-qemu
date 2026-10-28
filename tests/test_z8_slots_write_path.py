# SPDX-License-Identifier: GPL-2.0-or-later
"""IND-1: native main-parameter writer 0x410d9968 split for extended slots.

Simulation only. Extended slots (owned settings groups 0x7c/0x7d) are written
through the original settings store into their Layout record; every other key
must execute the original writer instruction for instruction.
"""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_slots_core import NativeSlots
from z8_slots_layout import Layout

WRITER, RESUME = 0x410D9968, 0x410D996C
NATIVE = (0x40160000, 0x45000000)


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Pinned original MAIN required')
class WritePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def fixture(self, n=32, original_writer=False):
        g = NativeSlots(self.source, Layout(n)); self.addCleanup(g.close)
        if original_writer:
            g.uc.mem_write(WRITER, self.source[WRITER - 0x40160000:WRITER - 0x40160000 + 4])
        return g

    def params(self, g, seed):
        buf = g.layout.address('scratch') + 0x200
        data = bytes((seed + i) & 0xFF for i in range(0x114))
        g.uc.mem_write(buf, data)
        return buf, data

    def trace(self, g, entry, args, limit=4000):
        from unicorn import UC_HOOK_CODE, UcError
        pcs = []
        def code(uc, address, size, data):
            pcs.append(address)
            if len(pcs) >= limit:
                uc.emu_stop()
        hook = g.uc.hook_add(UC_HOOK_CODE, code)
        error = None
        try:
            result = g.call(entry, *args)
        except (UcError, Exception) as exc:  # identical failures are part of the comparison
            result, error = None, type(exc).__name__
        finally:
            g.uc.hook_del(hook)
        return pcs, result, error

    def test_extended_slots_write_record_and_register_without_original_writer(self):
        for n in (32, 64):
            for bank in (0, 1):
                for slot in (10, 20, n):
                    g = self.fixture(n)
                    buf, data = self.params(g, slot + bank)
                    pcs, result, error = self.trace(g, g.entries['main_write'], (g.layout.selector(slot, bank), buf, 0))
                    self.assertIsNone(error)
                    self.assertEqual(result, 1)
                    self.assertNotIn(RESUME, pcs)                      # original writer not entered
                    record = g.record(slot, bank)
                    self.assertEqual(struct.unpack_from('<I', record)[0], 1)  # registered
                    self.assertEqual(record[0x10:0x124], data)
                    key = g.layout.keys(slot, bank)[0]
                    self.assertIn((key >> 8) & 0xFF, (0x7C, 0x7D))

    def test_c1_to_c9_execute_the_original_writer_instruction_for_instruction(self):
        for bank in (0, 1):
            for slot in range(1, 10):
                new = self.fixture(32)
                old = self.fixture(32, original_writer=True)
                args = []
                for g in (new, old):
                    buf, _ = self.params(g, slot)
                    args.append((g.layout.selector(slot, bank), buf, 0))
                pcs_new, res_new, err_new = self.trace(new, new.entries['main_write'], args[0])
                pcs_old, res_old, err_old = self.trace(old, WRITER, args[1])
                native_new = [pc for pc in pcs_new if NATIVE[0] <= pc < NATIVE[1] and pc != WRITER]
                native_old = [pc for pc in pcs_old if NATIVE[0] <= pc < NATIVE[1] and pc != WRITER]
                self.assertIn(RESUME, pcs_new, (slot, bank))
                self.assertEqual(native_new, native_old, (slot, bank))
                self.assertEqual((res_new, err_new), (res_old, err_old), (slot, bank))
                # record bytes identical apart from the pre-existing registration word
                rec_new, rec_old = new.record(slot, bank), old.record(slot, bank)
                self.assertEqual(rec_new[4:], rec_old[4:], (slot, bank))

    def test_out_of_range_and_null_params_keep_original_path(self):
        g = self.fixture(32)
        buf, _ = self.params(g, 1)
        for selector, params in ((0, buf), (5, buf), (0x123456, buf), (g.layout.selector(32, 0), 0)):
            pcs, _result, _error = self.trace(g, g.entries['main_write'], (selector, params, 0))
            self.assertIn(RESUME, pcs, hex(selector))


if __name__ == '__main__':
    unittest.main()
