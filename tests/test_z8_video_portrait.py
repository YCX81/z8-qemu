# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored video portrait coordinates preserve glyph sizes and original MENU."""
import os
import struct
from pathlib import Path
import unittest
from z8_video_layout import portrait_rect, patch_coordinates, TABLE_START, TABLE_END
from z8_orientation_unlock import build_plan, IMAGE_BASE


class Coordinates(unittest.TestCase):
    def test_compound_controls_move_without_scaling_or_internal_spacing_changes(self):
        groups=((0x1f18,(208,434,238,462),-184,240),
                (0x1f19,(238,434,326,470),-184,240),
                (0x1f29,(494,432,524,452),-142,244),
                (0x1f2b,(524,434,614,470),-142,244),
                (0x1dc5,(79,461,178,475),0,163),
                (0x1dd9,(172,449,176,459),0,163),
                (0x1e2a,(48,180,76,212),152,430),
                (0x1e2c,(76,178,112,214),152,430))
        for ident,rect,dx,dy in groups:
            with self.subTest(ident=ident):
                self.assertEqual(portrait_rect(ident,rect),tuple(v+(dy if n%2 else dx) for n,v in enumerate(rect)))

    def test_mode_and_focus_align_with_photo_top_row(self):
        self.assertEqual(portrait_rect(0x1f08,(13,44,49,80)),(13,6,49,42))
        self.assertEqual(portrait_rect(0x1ef4,(194,44,244,80)),(180,6,230,42))
        self.assertEqual(portrait_rect(0x2780,(166,134,554,330)),(46,250,434,446))

    def test_invalid_or_oversize_rectangles_are_not_silently_clipped(self):
        for rect in ((0,0,0,1),(-1,2,10,20),(0,0,721,480),(0,0,600,480)):
            with self.subTest(rect=rect),self.assertRaises(ValueError):portrait_rect(0,rect)


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class PortraitPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def test_patch_scope_preserves_landscape_and_existing_native_portrait_fields(self):
        plan=build_plan(self.source,video=True,video_portrait=True)
        self.assertFalse(plan['menu']);self.assertTrue(plan['video_portrait'])
        self.assertFalse(plan['video_marker_retained'])
        edits=plan['portrait_coordinate_edits'];self.assertGreater(len(edits),200)
        for row in edits:
            offset=row['address']-IMAGE_BASE
            old=self.source[offset:offset+68];new=plan['image'][offset:offset+68]
            self.assertEqual(old[32:40],bytes(8))
            self.assertEqual(new[:12],old[:12]);self.assertEqual(new[16:20],old[16:20])
            self.assertEqual(new[24:32],old[24:32]);self.assertEqual(new[56:],old[56:])
            a=struct.unpack_from('<4h',old,24);b=struct.unpack_from('<4h',new,32)
            self.assertEqual((a[2]-a[0],a[3]-a[1]),(b[2]-b[0],b[3]-b[1]))
            self.assertTrue(0<=b[0]<b[2]<=480 and 0<=b[1]<b[3]<=720)
        restored=bytearray(plan['image'])
        for row in plan['patch_sites']:
            a=row['address']-IMAGE_BASE;size=row['bytes'];restored[a:a+size]=self.source[a:a+size]
        self.assertEqual(bytes(restored),self.source)

    def test_explicit_video_scope_and_identity(self):
        for flags in ({'menu':True,'video_portrait':True},{'video':True,'video_portrait':1},
                      {'video':True,'menu':True,'video_portrait':True},
                      {'video':True,'video_layout':True,'video_portrait':True}):
            with self.subTest(flags=flags),self.assertRaises(ValueError):build_plan(self.source,**flags)
        with self.assertRaises(ValueError):patch_coordinates(bytes(len(self.source)),bytearray(self.source))

    def test_original_portrait_selection_and_menu_lock_run_without_size_override(self):
        import test_z8_orientation_menu_layout as helpers
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_LR
        helper=helpers.MenuLayout();helper.source=self.source
        plan=build_plan(self.source,video=True,video_portrait=True)
        for direction in (1,2):
            u=helper.fixture(plan,direction=direction)
            for mask,expected_direction,expected_portrait in ((1,direction,1),(0x20,0,0)):
                u.reg_write(UC_ARM64_REG_X0,mask);u.reg_write(UC_ARM64_REG_X1,1)
                u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                u.emu_start(0x4038e3e4,0xa07000000,count=1000)
                for pc,expected in ((0x4038d59c,expected_direction),(0x4038d658,expected_portrait)):
                    u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                    u.emu_start(pc,0xa07000000,count=1000)
                    self.assertEqual(u.reg_read(UC_ARM64_REG_X0),expected)
            _,dimensions=helper.size_getter(u,(480,720))
            self.assertEqual(dimensions,(480,720))


if __name__=='__main__':unittest.main()
