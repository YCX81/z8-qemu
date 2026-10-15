# SPDX-License-Identifier: GPL-2.0-or-later
"""Original A64 rotation policy, with explicit setting/layout getter boundaries.

These tests do not establish a running video UI or a physical camera result.
No original firmware instruction bytes are embedded in the public test.
"""
import hashlib
import os
from pathlib import Path
import struct
import unittest

MAIN_SHA='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
MASK=0xa03228308
DIRECTION=0xa03228244


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned local Z8 MAIN required')
class NativeRotationPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
        if hashlib.sha256(cls.source).hexdigest()!=MAIN_SHA:raise ValueError('Wrong Z8 MAIN identity')

    def fixture(self,*,enabled=1,video=0,layout=0,mask=0,direction=2):
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_LR,UC_ARM64_REG_PC,UC_ARM64_REG_SP
        u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        u.mem_map(0x40160000,(len(self.source)+4095)&~4095)
        u.mem_write(0x40160000,self.source)
        u.mem_map(MASK&~4095,4096);u.mem_map(0xa07000000,0x10000)
        u.mem_write(MASK,struct.pack('<I',mask));u.mem_write(DIRECTION,struct.pack('<I',direction))
        u.reg_write(UC_ARM64_REG_SP,0xa0700fff0);u.reg_write(UC_ARM64_REG_LR,0xa07000000)
        calls=[]
        def boundary(u,pc,size,unused):
            if pc==0x403ec398:
                key=u.reg_read(UC_ARM64_REG_X0)
                if key not in (0x02001dcf,0x218c):raise AssertionError(f'Unexpected setting {key:x}')
                value=enabled if key==0x02001dcf else video;calls.append(('setting',key,value))
            elif pc==0x40469730:
                value=layout;calls.append(('layout',value))
            else:return
            u.reg_write(UC_ARM64_REG_X0,value);u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
        u.hook_add(UC_HOOK_CODE,boundary)
        return u,calls

    def test_effective_direction_requires_switch_and_clear_mask(self):
        from unicorn.arm64_const import UC_ARM64_REG_X0
        for enabled in (0,1):
            for mask in (0,1,2,0x20):
                for direction in (0,1,2):
                    with self.subTest(enabled=enabled,mask=mask,direction=direction):
                        u,calls=self.fixture(enabled=enabled,mask=mask,direction=direction)
                        u.emu_start(0x4038d59c,0xa07000000,count=1000)
                        self.assertEqual(u.reg_read(UC_ARM64_REG_X0),direction if enabled and not mask else 0)
                        self.assertEqual(calls,[('setting',0x02001dcf,enabled)])

    def test_video_menu_site_sets_bit_one_without_erasing_other_locks(self):
        for video in (0,1):
            for layout in (0,1):
                with self.subTest(video=video,layout=layout):
                    u,calls=self.fixture(video=video,layout=layout,mask=0x23)
                    # Native loads 218c, compares with 1, calls 404a5354.
                    # Stop before the following unrelated UI update.
                    u.emu_start(0x4046dcf0,0x4046dd04,count=2000)
                    actual=struct.unpack('<I',u.mem_read(MASK,4))[0]
                    self.assertEqual(actual,0x20|(1 if video else 0)|(2 if layout else 0))
                    self.assertEqual(calls,[('setting',0x218c,video),('layout',layout)])

    def test_main_menu_set_and_clear_only_its_own_bit(self):
        u,_=self.fixture(mask=1)
        u.emu_start(0x40507144,0x40507150,count=1000)
        self.assertEqual(struct.unpack('<I',u.mem_read(MASK,4))[0],0x21)
        u.emu_start(0x40507e44,0x40507e50,count=1000)
        self.assertEqual(struct.unpack('<I',u.mem_read(MASK,4))[0],1)


if __name__=='__main__':unittest.main()
