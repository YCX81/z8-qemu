# SPDX-License-Identifier: GPL-2.0-or-later
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_slots_layout import Layout
from z8_slots_core import NativeSlots
from z8_slots_labels import build_label_plan
from z8_slots_imenu import build_menu_plan
from z8_picture_control import make_flexible_np3


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class GuiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def fixture(self,count=32):
        g=NativeSlots(self.source,Layout(count));self.addCleanup(g.close)
        g.uc.mem_map(0xA03200000,0x300000)
        return g

    def apply(self,g,plan):
        for region in plan['regions']:
            if region['permissions']=='rx':g.uc.mem_map(region['address'],len(region['data']))
            else:
                self.assertEqual(bytes(g.uc.mem_read(region['address'],len(region['data']))),bytes(len(region['data'])))
            g.uc.mem_write(region['address'],region['data'])
        for patch in plan['patches']:
            self.assertEqual(bytes(g.uc.mem_read(patch['address'],len(patch['expected']))),patch['expected'])
            g.uc.mem_write(patch['address'],patch['replacement'])

    def test_every_extended_slot_has_distinct_native_text_and_legacy_restores(self):
        g=self.fixture(64);self.apply(g,build_label_plan(self.source,g.layout))
        obj=g.layout.address('scratch');request=obj+0x400
        g.uc.mem_write(obj+12,struct.pack('<I',1))
        for slot in range(10,65):
            for variant in range(5):
                label=g.layout.label_id(slot,variant)
                self.assertEqual(g.call(0x403F645C,obj,label),1)
                self.assertEqual(struct.unpack('<I',g.uc.mem_read(obj+12,4))[0],3)
                g.uc.mem_write(request,struct.pack('<I',label))
                g.call(0x4032E70C,request,request+16,request+24,request+28)
                pointer,length,style=struct.unpack('<QII',g.uc.mem_read(request+16,16))
                self.assertEqual(bytes(g.uc.mem_read(pointer,length+1)),f'C-{slot}\0'.encode())
                self.assertEqual(style,0x21F)
        self.assertEqual(g.call(0x403F645C,obj,0x046A),1)
        self.assertEqual(struct.unpack('<I',g.uc.mem_read(obj+12,4))[0],1)

    def test_native_imenu_constructor_lists_all_slots_and_preserves_rebuild(self):
        from unicorn import UC_HOOK_CODE
        g=self.fixture();plan=build_menu_plan(self.source,g.layout);self.apply(g,plan)
        def policy(uc,address,_size,_data):
            uc.reg_write(g.r.UC_ARM64_REG_X0,1)
            uc.reg_write(g.r.UC_ARM64_REG_PC,uc.reg_read(g.r.UC_ARM64_REG_LR))
        hook=g.uc.hook_add(UC_HOOK_CODE,policy,begin=0x403F9E64,end=0x403F9E64)
        self.addCleanup(lambda:g.uc.hook_del(hook) if g.uc else None)
        pointer=g.layout.address('imenu')+0x7000
        for bank in (0,1):
            for slot in range(1,33):g.import_np3(slot,make_flexible_np3(f'C{slot} B{bank}',slot+bank*64),bank)
            for repeat in range(2):
                g.call(0x4045341C,1-bank,pointer)
                self.assertEqual(struct.unpack('<I',g.uc.mem_read(pointer,4))[0],32)
                selectors=struct.unpack('<32I',g.uc.mem_read(plan['arrays']['selectors'],128))
                self.assertEqual(selectors,tuple(g.layout.selector(slot,bank) for slot in range(1,33)))
                self.assertEqual(g.call(0x404A7040,62,1-bank),62)
        labels=struct.unpack('<32H',g.uc.mem_read(plan['arrays']['labels']+62,64))
        self.assertEqual(labels[-1],g.layout.label_id(32,1))


if __name__=='__main__':unittest.main()
