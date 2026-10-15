# SPDX-License-Identifier: GPL-2.0-or-later
"""Video selector and record key transport; not recording acceptance."""
import hashlib
import os
from pathlib import Path
import struct
import unittest
from z8_video import input_body
from z8_resources import MAIN_SHA


class Input(unittest.TestCase):
    def test_selector_is_retained_while_buttons_press_and_release(self):
        for mode in ('photo','video'):
            for key,offset in ((None,None),('record',9),('half',7),('menu',0x43)):
                data=input_body(mode,key)
                self.assertEqual(len(data),124)
                self.assertEqual(data[0x57],int(mode=='video'))
                expected={0:1,4:1}
                if mode=='video':expected[0x57]=1
                if offset is not None:expected[offset]=1
                self.assertEqual({n:v for n,v in enumerate(data) if v},expected)

    def test_unknown_modes_and_keys_are_rejected(self):
        for mode in (None,0,True,'movie','VIDEO',''):
            with self.assertRaises(ValueError):input_body(mode)
        for key in (0,True,'ok','start','stop',''):
            with self.assertRaises(ValueError):input_body('video',key)


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned local Z8 MAIN required')
class NativeDecoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
        if hashlib.sha256(cls.source).hexdigest()!=MAIN_SHA:raise ValueError('Wrong MAIN')

    def test_original_decoder_distinguishes_selector_and_record_button(self):
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
        from unicorn.arm64_const import (UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,
                                        UC_ARM64_REG_LR,UC_ARM64_REG_SP,UC_ARM64_REG_PC)
        for mode in ('photo','video'):
            for key in (None,'record','half','menu'):
                with self.subTest(mode=mode,key=key):
                    u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
                    u.mem_map(0x40160000,(len(self.source)+4095)&~4095);u.mem_write(0x40160000,self.source)
                    u.mem_map(0xa03e17000,0x1000);u.mem_map(0xa07000000,0x10000)
                    raw=0xa07001000;result=0xa07002000;stop=0xa07000000
                    u.mem_write(raw,input_body(mode,key))
                    u.reg_write(UC_ARM64_REG_X0,raw);u.reg_write(UC_ARM64_REG_X2,result)
                    u.reg_write(UC_ARM64_REG_LR,stop);u.reg_write(UC_ARM64_REG_SP,0xa0700fff0)
                    def boundary(u,pc,size,unused):
                        if pc==0x4127ab40:
                            u.mem_write(u.reg_read(UC_ARM64_REG_X0),bytes([u.reg_read(UC_ARM64_REG_X1)&255])*u.reg_read(UC_ARM64_REG_X2))
                            u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
                    u.hook_add(UC_HOOK_CODE,boundary);u.emu_start(0x41020844,stop,count=2000)
                    self.assertEqual(u.reg_read(UC_ARM64_REG_PC),stop)
                    abstract=struct.unpack('<Q',u.mem_read(result,8))[0]
                    payload=struct.unpack('<Q',u.mem_read(abstract+8,8))[0]
                    data=u.mem_read(payload,44)
                    self.assertEqual(struct.unpack_from('<H',data,0x26)[0],int(mode=='video'))
                    sw0,sw1=struct.unpack_from('<II',data)
                    self.assertEqual(bool(sw1&0x2000),key=='record')
                    self.assertEqual(bool(sw1&0x8000),key=='menu')
                    self.assertEqual(bool(sw0&0x80000000),key=='half')


if __name__=='__main__':unittest.main()
