# SPDX-License-Identifier: GPL-2.0-or-later
"""Native envelope-to-meter conversion, with explicit synthetic input words."""
import hashlib,os,struct,unittest
from pathlib import Path
import test_z8_orientation_policy as policy
from z8_resources import MAIN_SHA


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class NativeMeter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
        if hashlib.sha256(cls.source).hexdigest()!=MAIN_SHA:raise ValueError('Wrong MAIN')

    def test_native_zero_and_peak_under_both_sample_packings(self):
        from unicorn import UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_PC,UC_ARM64_REG_LR
        for mode in (0,1):
            for word,expected in ((0,0),(0x007f7f00,14)):
                helper=policy.NativeRotationPolicy();helper.source=self.source
                u,_=helper.fixture();u.mem_map(0xa02d4c000,4096)
                u.mem_write(0xa02d4cb50,struct.pack('<I',mode))
                def input_boundary(m,pc,size,data):
                    if pc==0x40c7de0c:
                        m.mem_write(m.reg_read(UC_ARM64_REG_X0),struct.pack('<2I',word,word))
                        m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
                u.hook_add(UC_HOOK_CODE,input_boundary)
                out=0xa07000300
                u.reg_write(UC_ARM64_REG_X0,out);u.reg_write(UC_ARM64_REG_X1,out+4)
                u.emu_start(0x40c83950,0xa07000000,count=10000)
                self.assertEqual(struct.unpack('<2i',u.mem_read(out,8)),(expected,expected))


if __name__=='__main__':unittest.main()
