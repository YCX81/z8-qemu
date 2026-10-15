# SPDX-License-Identifier: GPL-2.0-or-later
"""Video-only layout policy; original MENU lock remains authoritative."""
import os,struct,unittest
from pathlib import Path
import test_z8_orientation_menu_layout as helpers
from z8_orientation_unlock import build_plan,IMAGE_BASE

@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class VideoLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def test_video_layout_rejects_menu_unlock_and_ambiguous_scope(self):
        for flags in ({'menu':True,'video_layout':True},{'video':True,'video_layout':1},
                      {'video':True,'menu':True,'video_layout':True},
                      {'video':True,'menu':True,'menu_layout':True,'video_layout':True}):
            with self.subTest(flags=flags),self.assertRaises(ValueError):build_plan(self.source,**flags)

    def test_video_direction_respects_every_other_lock_and_setting(self):
        from unicorn.arm64_const import UC_ARM64_REG_X0
        helper=helpers.MenuLayout();helper.source=self.source
        plan=build_plan(self.source,video=True,video_layout=True)
        for mask in (0,1,2,3,0x20,0x21,0x80,0x81):
            for enabled in (0,1):
                for direction in (0,1,2):
                    with self.subTest(mask=mask,enabled=enabled,direction=direction):
                        u=helper.fixture(plan,mask=mask,enabled=enabled,direction=direction)
                        u.emu_start(0x4038d59c,0xa07000000,count=1000)
                        self.assertEqual(u.reg_read(UC_ARM64_REG_X0),direction if enabled and not(mask&~1) else 0)

    def test_geometry_changes_only_video_portrait_pair(self):
        helper=helpers.MenuLayout();helper.source=self.source
        plan=build_plan(self.source,video=True,video_layout=True)
        for mask in (0,1,0x20):
            for dimensions in ((480,720),(720,480),(1024,682),(480,721)):
                u=helper.fixture(plan,mask=mask)
                _,actual=helper.size_getter(u,dimensions)
                self.assertEqual(actual,(720,480) if mask&1 and dimensions==(480,720) else dimensions)

    def test_policy_keeps_native_menu_marker_and_all_original_bytes_outside_guarded_sites(self):
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1
        helper=helpers.MenuLayout();helper.source=self.source
        plan=build_plan(self.source,video=True,video_layout=True)
        restored=bytearray(plan['image'])
        for row in plan['patch_sites']:
            offset=row['address']-IMAGE_BASE;size=row['bytes']
            restored[offset:offset+size]=self.source[offset:offset+size]
        self.assertEqual(bytes(restored),self.source)
        self.assertFalse(plan['menu']);self.assertEqual(plan['suppressed_mask'],0)
        self.assertEqual(plan['effective_direction_ignored_mask'],1)
        self.assertTrue(plan['video_marker_retained'])
        for initial,requested in ((0,1),(1,0x20),(0x20,1),(0x21,2)):
            u=helper.fixture(plan,mask=initial)
            u.reg_write(UC_ARM64_REG_X0,requested);u.reg_write(UC_ARM64_REG_X1,1)
            u.emu_start(0x4038e3e4,0xa07000000,count=1000)
            self.assertEqual(struct.unpack('<I',u.mem_read(0xa03228308,4))[0],initial|requested)

if __name__=='__main__':unittest.main()
