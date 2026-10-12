# SPDX-License-Identifier: GPL-2.0-or-later
import os
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_slots_core import NativeSlots
from z8_slots_layout import Layout
from z8_slots_main_menu import build_main_menu_plan
from z8_picture_control import make_flexible_np3


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class MainMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
    def fixture(self,n=32):
        g=NativeSlots(self.source,Layout(n));self.addCleanup(g.close)
        plan=build_main_menu_plan(self.source,g.layout)
        for r in plan['regions']:
            if r['permissions']=='rx':g.uc.mem_map(r['address'],len(r['data']))
            else:self.assertEqual(bytes(g.uc.mem_read(r['address'],len(r['data']))),bytes(len(r['data'])))
            g.uc.mem_write(r['address'],r['data'])
        for p in plan['patches']:
            self.assertEqual(bytes(g.uc.mem_read(p['address'],len(p['expected']))),p['expected'])
            g.uc.mem_write(p['address'],p['replacement'])
        return g,plan

    def test_photo_and_movie_rows_keep_originals_and_have_all_new_events(self):
        for n in (32,64):
            g,p=self.fixture(n)
            for bank,page in enumerate(p['lcd_pages']):
                self.assertEqual(page['count'],32+n+bank)
                self.assertEqual(len(page['extensions']),n-9)
                pairs=bytes(g.uc.mem_read(page['pairs'],(31+n)*8))
                for slot in range(10,n+1):
                    self.assertEqual(struct.unpack_from('<II',pairs,(31+slot-1)*8),(g.layout.selector(slot,bank),g.layout.row_key(slot,bank)))
                    row=page['extensions'][slot-10]
                    for offset in (0xC8,0x168):
                        self.assertEqual(struct.unpack('<Q',g.uc.mem_read(row['events']+offset,8))[0],row['handler'])

    def test_owned_lookup_and_validity_accept_last_slot_but_reject_unknown(self):
        g,p=self.fixture();g.import_np3(32,make_flexible_np3('C32',32),0)
        page=p['lcd_pages'][0]
        self.assertEqual(g.call(0x403EB6A8,page['pairs'],40,g.layout.selector(32,0),40),62)
        self.assertEqual(g.call(0x403EB6A8,page['pairs'],40,0x123456,40),63)
        self.assertEqual(g.call(0x410DB078,g.layout.selector(32,0)),1)
        self.assertEqual(g.call(0x410DB078,g.layout.selector(31,0)),0)

    def test_original_deferred_service_keeps_c32_and_follow_photo(self):
        from unicorn import UC_HOOK_CODE
        g,p=self.fixture()
        for bank in (0,1):g.import_np3(32,make_flexible_np3(f'BANK{bank}',32+bank),bank)
        follow=[0]
        def setting(uc,address,_size,_):
            key=uc.reg_read(g.r.UC_ARM64_REG_X0)
            values={0x07000633:62,0x0700122B:62,0x0300122C:follow[0]}
            if key not in values:raise AssertionError(hex(key))
            uc.reg_write(g.r.UC_ARM64_REG_X0,values[key]);uc.reg_write(g.r.UC_ARM64_REG_PC,uc.reg_read(g.r.UC_ARM64_REG_LR))
        hook=g.uc.hook_add(UC_HOOK_CODE,setting,begin=0x41012C84,end=0x41012C84)
        self.addCleanup(lambda:g.uc.hook_del(hook) if g.uc else None)
        request=g.layout.address('scratch')+0x800;g.uc.mem_write(request,struct.pack('<III',29,0,0))
        for target,mirror,bank in ((0,0,0),(1,0,1),(1,1,0)):
            follow[0]=mirror;g.call(0x410A86DC,target,request)
            selector,current=g.current_selection(target)
            self.assertEqual(selector,g.layout.selector(32,bank))
            self.assertEqual(current[:0x114],g.record(32,bank)[0x10:0x124])
            self.assertEqual(current[0x12C:],g.record(32,bank)[0x370:0x39C])

    def test_label_choice_is_unique_per_slot_across_bitmap_boundary(self):
        g,p=self.fixture(64)
        for slot in (10,14,15,20,32,33,64):
            self.assertEqual(g.call(p['label_choose'],0x20,1,1,slot),g.layout.label_id(slot,1))
            self.assertEqual(g.call(p['label_choose'],1,1,0,slot),g.layout.label_id(slot,3))

    def test_native_membership_has_no_bit64_wrap_for_either_bank(self):
        g,p=self.fixture(64)
        for point in p['membership']:
            register=getattr(g.r,'UC_ARM64_REG_X'+point['reg'][1:])
            for bank in (0,1):
                for slot in (1,9,10,14,15,20,32,33,64):
                    g.uc.reg_write(register,g.layout.selector(slot,bank))
                    g.uc.emu_start(point['entry'],point['success'],count=500)
                    self.assertEqual(g.uc.reg_read(g.r.UC_ARM64_REG_PC),point['success'],(point,slot,bank))

if __name__=='__main__':unittest.main()
