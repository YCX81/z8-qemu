# SPDX-License-Identifier: GPL-2.0-or-later
"""Original A64 parser/current objects, with declared environmental services."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_slots_core import NativeSlots, build_plan
from z8_slots_layout import Layout
from z8_picture_control import make_flexible_np3, make_np3


class InputGuards(unittest.TestCase):
    def test_unknown_main_is_rejected(self):
        with self.assertRaises(ValueError): build_plan(bytes(128), Layout())


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Pinned firmware required')
class NativeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def fixture(self, count=32):
        g = NativeSlots(self.source, Layout(count)); self.addCleanup(g.close)
        return g

    def test_all_64_c32_records_import_select_and_list_without_aliasing(self):
        g=self.fixture()
        for bank in (0,1):
            for slot in range(1,33):
                g.import_np3(slot, make_flexible_np3(f'C{slot:02} B{bank}', (slot+bank*64)%256), bank)
                selector,current=g.current_selection(bank)
                self.assertEqual(selector,g.layout.selector(slot,bank))
                record=g.record(slot,bank)
                self.assertEqual(current[:0x114],record[0x10:0x124])
                self.assertEqual(current[0x12c:],record[0x370:0x39c])
            self.assertEqual(g.slots(bank),list(range(1,33)))
        for bank in (0,1):
            for slot in range(1,33):
                self.assertEqual(g.record(slot,bank)[0x10:0x24].split(b'\0')[0],f'C{slot:02} B{bank}'.encode())
        self.assertTrue(g.guards_intact())

    def test_c64_namespace_group_boundary_and_current_cache(self):
        g=self.fixture(64)
        for bank in (0,1):
            for slot in (9,10,14,15,19,20,21,32,33,51,52,53,63,64):
                g.import_np3(slot,make_flexible_np3(f'C{slot} B{bank}',slot+bank*64),bank)
                selector,current=g.current_selection(bank)
                self.assertEqual(selector,g.layout.selector(slot,bank))
                self.assertEqual(current[0x12c:],g.record(slot,bank)[0x370:0x39c])
                pointer=struct.unpack_from('<Q',current,0x120)[0]
                self.assertNotEqual(pointer,0)
        self.assertGreater(g.layout.keys(64,1)[0] & 0xFFFF,0x7CFF)
        self.assertTrue(g.guards_intact())

    def test_follow_photo_and_independent_movie_have_correct_source(self):
        g=self.fixture()
        for b in (0,1): g.import_np3(32,make_flexible_np3(f'SOURCE {b}',21+b),b)
        self.assertNotEqual(g.record(32,0),g.record(32,1))
        self.assertNotEqual(g.call(g.entries['select_full'],32,0,1),0)
        self.assertEqual(g.current_selection(1)[0],g.layout.selector(32,0))
        self.assertEqual(g.current_selection(1)[1][:0x114],g.record(32,0)[0x10:0x124])
        self.assertEqual(g.call(g.entries['sync_current']),1)
        self.assertEqual(struct.unpack('<4I',g.uc.mem_read(g.layout.address('records')+48,16)),(32,32,0,0))
        g.select(32,1)
        self.assertEqual(g.call(g.entries['sync_current']),1)
        self.assertEqual(struct.unpack('<4I',g.uc.mem_read(g.layout.address('records')+48,16)),(32,32,0,1))

    def test_invalid_import_and_hidden_slots_preserve_existing_records(self):
        g=self.fixture();g.import_np3(32,make_np3('LAST'),0)
        before=g.records()
        with self.assertRaises(ValueError):g.import_np3(33,make_np3('HIDDEN'),0)
        with self.assertRaises(ValueError):g.import_np3(32,b'NOPE'+make_np3('BAD')[4:],0)
        self.assertEqual(g.records(),before)
        self.assertEqual(g.call(g.entries['select'],0,0),0)
        self.assertEqual(g.call(g.entries['select'],33,0),0)

    def test_native_validation_checks_keys_flags_and_hidden_capacity(self):
        g=self.fixture();g.import_np3(32,make_flexible_np3('C32'),1)
        raw=g.records();stage=g.layout.address('load')
        g.uc.mem_write(stage,raw)
        self.assertEqual(g.call(g.entries['validate'],stage),1)
        for offset in (g.layout.header_bytes+4,g.layout.header_bytes+12,
                       g.layout.header_bytes+32*0x400,64):
            bad=bytearray(raw);bad[offset]^=1;g.uc.mem_write(stage,bytes(bad))
            self.assertEqual(g.call(g.entries['validate'],stage),0,offset)


if __name__=='__main__':unittest.main()
