# SPDX-License-Identifier: GPL-2.0-or-later
import os
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_slots_layout import Layout
from z8_slots_image import compose


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def test_c32_c64_all_patches_reversible_and_loaders_disjoint(self):
        for n in (32,64):
            L=Layout(n);plan=compose(self.source,L);image=bytearray(plan['image'])
            for patch in plan['patches']:
                off=patch['source_offset'];size=len(patch['expected'])
                self.assertEqual(image[off:off+size],patch['replacement'])
                image[off:off+size]=patch['expected']
            self.assertEqual(bytes(image),self.source)
            ranges=sorted((r['address'],r['address']+len(r['data'])) for r in plan['regions'])
            for a,b in zip(ranges,ranges[1:]):self.assertLessEqual(a[1],b[0])
            core=next(r for r in plan['regions'] if r['address']==L.data_base)
            self.assertEqual(len(core['data']),0x100000)
            self.assertEqual(struct.unpack_from('<I',plan['image'],0x43F93CF0-0x40160000)[0],0xA00000)


if __name__=='__main__':unittest.main()
