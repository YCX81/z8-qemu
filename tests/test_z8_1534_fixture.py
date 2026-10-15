# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate authored DMA fixture against the independent packed12 equation."""
import struct,unittest
from types import SimpleNamespace
from test_z8_1534_reference import source_setup,program,STRIDE,BYTES,A
class FixtureTests(unittest.TestCase):
    def test_source_and_program_geometry_agree_with_packed12_dma(self):
        rows=[]
        g=SimpleNamespace(configure=lambda c,m:rows.append((dict(c),m)),write=lambda *a:None,
                          enable=lambda *a:None,periodic=lambda:None)
        source_setup(g);c,mask=rows[0]
        w,h=c[4]>>16,c[8]>>16;pitch=(((w*12+7)//8)+31)&~31
        self.assertEqual(c[0],2);self.assertEqual(mask,0x4000)
        self.assertEqual(c[0x100],pitch);self.assertEqual(STRIDE,pitch);self.assertEqual(BYTES,pitch*h)
        b=program();self.assertEqual(len(b),0x7c0)
        self.assertEqual(struct.unpack_from('<I',b,0)[0]&3,2)
        self.assertEqual(struct.unpack_from('<I',b,4)[0]&0x7fff,pitch)
        dim=struct.unpack_from('<I',b,0x90)[0]
        self.assertEqual((dim&0x3fff,(dim>>16)&0x3fff),(w,h))
        for n in range(4):self.assertEqual(c[0x120+n*8]|(c[0x124+n*8]<<32),A)
if __name__=='__main__':unittest.main()
