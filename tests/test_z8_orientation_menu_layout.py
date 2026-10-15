# SPDX-License-Identifier: GPL-2.0-or-later
"""Original A64 layer conversion and pose notification, with display getters bounded."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_orientation_unlock import build_plan,IMAGE_BASE


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned local MAIN required')
class MenuLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def test_requires_menu_unlock_and_an_explicit_boolean(self):
        for flags in ({'video':True,'menu_layout':True},{'menu':True,'menu_layout':1}):
            with self.subTest(flags=flags),self.assertRaises(ValueError):build_plan(self.source,**flags)

    def test_only_guarded_policy_getter_and_rectangle_site_change(self):
        plan=build_plan(self.source,menu=True,video=True,menu_layout=True)
        spans=[(0x4038e3e4,64),(0x4038d554,4),(0x4038d57c,4),(0x4038d59c,76),(0x4038d62c,4),(0x40340be0,4)]
        repaired=bytearray(plan['image'])
        for address,size in spans:
            offset=address-IMAGE_BASE;repaired[offset:offset+size]=self.source[offset:offset+size]
        self.assertEqual(bytes(repaired),self.source)
        self.assertEqual(plan['source_sha256'],hashlib.sha256(self.source).hexdigest())
        self.assertTrue(plan['menu_marker_retained'])
        self.assertEqual(plan['effective_direction_ignored_mask'],0x20)
        self.assertEqual(plan['suppressed_mask'],1)
        self.assertEqual(len(plan['patch_sites']),6)
        self.assertEqual({r['address'] for r in plan['patch_sites']},{a for a,_ in spans})
        self.assertFalse(plan['new_executable_mapping'])
        self.assertEqual(plan['additional_stack_bytes'],0)

    def fixture(self,plan,**kwargs):
        from test_z8_orientation_policy import NativeRotationPolicy
        helper=NativeRotationPolicy();helper.source=self.source
        u,_=helper.fixture(**kwargs)
        if plan:
            for row in plan['patch_sites']:
                at=row['address'];size=row['bytes'];off=at-IMAGE_BASE
                u.mem_write(at,plan['image'][off:off+size])
        return u

    def size_getter(self,u,dimensions,surface=1):
        from unicorn import UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,UC_ARM64_REG_PC,UC_ARM64_REG_LR
        context=0xa07000300;out=0xa07000140
        u.mem_write(context+0x90,struct.pack('<2H',*dimensions))
        u.mem_write(context+0x1e8,struct.pack('<2H',*dimensions));u.mem_write(out,b'????')
        def boundary(machine,pc,size,unused):
            if pc==0x4033d8cc:
                machine.reg_write(UC_ARM64_REG_X0,context)
                machine.reg_write(UC_ARM64_REG_PC,machine.reg_read(UC_ARM64_REG_LR))
        hook=u.hook_add(UC_HOOK_CODE,boundary)
        for reg,value in ((UC_ARM64_REG_X0,1),(UC_ARM64_REG_X1,surface),(UC_ARM64_REG_X2,0),
            (UC_ARM64_REG_X3,out),(UC_ARM64_REG_LR,0xa07000000)):u.reg_write(reg,value)
        u.emu_start(0x40340b9c,0xa07000000,count=2000);u.hook_del(hook)
        self.assertEqual(struct.unpack('<2H',u.mem_read(context+0x1e8,4)),dimensions)
        return u.reg_read(UC_ARM64_REG_X0),struct.unpack('<2H',u.mem_read(out,4))

    def test_shared_size_getter_changes_only_the_menu_portrait_pair(self):
        plan=build_plan(self.source,menu=True,video=True,menu_layout=True)
        for mask in (0,2,0x20,0x22):
            for dimensions in ((480,720),(720,480),(1024,682),(1280,960),(480,721),(0,0)):
                for surface in (0,1,2,3):
                    with self.subTest(mask=mask,dimensions=dimensions,surface=surface):
                        u=self.fixture(plan,mask=mask)
                        result,actual=self.size_getter(u,dimensions,surface)
                        if surface==3:
                            self.assertEqual(result,0);self.assertEqual(actual,(0x3f3f,0x3f3f))
                        else:
                            self.assertEqual(result,1)
                            self.assertEqual(actual,(720,480) if mask&0x20 and dimensions==(480,720) else dimensions)

    def test_native_layer_converter_fits_background_list_scrollbar_and_dirty_region(self):
        from fractions import Fraction
        from unicorn import UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_PC,UC_ARM64_REG_LR,UC_ARM64_REG_SP,UC_ARM64_REG_X19
        plan=build_plan(self.source,menu=True,video=True,menu_layout=True)
        scale=Fraction(682,720);offset=(1024-480*scale)/2
        rectangles=((0,0,720,480),(76,58,708,480),(708,58,720,131),(76,58,708,174))
        for mask in (0,0x20):
            for rotation in (0,1,3):
                for rect in rectangles:
                    with self.subTest(mask=mask,rotation=rotation,rect=rect):
                        results=[]
                        for patched in (False,True):
                            u=self.fixture(plan if patched else None,mask=mask)
                            u.mem_map(0xa02f34000,4096)
                            # Fixed display-format getters only. Rectangle
                            # rotation, clipping and fixed-point arithmetic
                            # execute the actual 40332820/40332ca0 routines.
                            def boundary(machine,pc,size,unused):
                                if pc not in (0x4032e7e0,0x40330a6c):return
                                machine.reg_write(UC_ARM64_REG_X0,0 if pc==0x4032e7e0 else 0xc4)
                                machine.reg_write(UC_ARM64_REG_PC,machine.reg_read(UC_ARM64_REG_LR))
                            u.hook_add(UC_HOOK_CODE,boundary)
                            address=0xa07000080;data=bytearray(112)
                            width,height=rect[2]-rect[0],rect[3]-rect[1]
                            # Texture and parent-canvas sizes are separate.
                            # The bad portrait parent is observed in QEMU;
                            # its retained row texture is still complete.
                            _,parent=self.size_getter(u,(480,720) if rotation else (720,480))
                            struct.pack_into('<12h',data,0,736,2440,*parent,0,0,width,height,*rect)
                            struct.pack_into('<II2h',data,0x18,100,rotation,1024,682)
                            u.mem_write(address,bytes(data))
                            for reg,value in ((UC_ARM64_REG_X0,1),(UC_ARM64_REG_X1,1),
                                (UC_ARM64_REG_X2,address),(UC_ARM64_REG_X19,0x12345678),
                                (UC_ARM64_REG_LR,0xa07000000)):u.reg_write(reg,value)
                            u.emu_start(0x40332820,0xa07000000,count=50000)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_X19),0x12345678)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_SP),0xa0700fff0)
                            results.append(struct.unpack('<4i',u.mem_read(address+0x24,16)))
                        if mask and rotation:
                            if rotation==1:
                                logical=(480-rect[3],rect[0],480-rect[1],rect[2])
                            else:logical=(rect[1],720-rect[2],rect[3],720-rect[0])
                            expected=[float(v*scale+(offset if n%2==0 else 0)) for n,v in enumerate(logical)]
                            actual=[v/65536 for v in results[1]]
                            self.assertTrue(all(abs(a-b)<1 for a,b in zip(actual,expected)),(actual,expected))
                            self.assertTrue(0<=actual[0]<actual[2]<=1024)
                            self.assertTrue(0<=actual[1]<actual[3]<=682)
                        else:self.assertEqual(results[1],results[0])

    def test_native_direction_notification_and_layout_are_separate(self):
        from test_z8_orientation_policy import MASK,DIRECTION
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_LR
        for video in (False,True):
            plan=build_plan(self.source,menu=True,video=video,menu_layout=True)
            for enabled in (0,1):
                for other in (0,2,0x80000000):
                    for menu in (0,0x20):
                        for old,new in ((0,1),(1,2),(2,0),(2,2)):
                            with self.subTest(video=video,enabled=enabled,other=other,menu=menu,old=old,new=new):
                                u=self.fixture(plan,enabled=enabled,mask=menu|other,direction=old)
                                u.reg_write(UC_ARM64_REG_X0,new);u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                                u.emu_start(0x4038d510,0xa07000000,count=1000)
                                self.assertEqual(u.reg_read(UC_ARM64_REG_X0),int(enabled and not other and old!=new))
                                self.assertEqual(struct.unpack('<I',u.mem_read(DIRECTION,4))[0],new)
                                u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                                u.emu_start(0x4038d59c,0xa07000000,count=1000)
                                self.assertEqual(u.reg_read(UC_ARM64_REG_X0),new if enabled and not other else 0)
                                u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                                u.emu_start(0x4038d600,0xa07000000,count=1000)
                                self.assertEqual(u.reg_read(UC_ARM64_REG_X0),int(not enabled or bool(other)))
                                u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                                u.emu_start(0x4038d658,0xa07000000,count=1000)
                                self.assertEqual(u.reg_read(UC_ARM64_REG_X0),int(enabled and not (menu|other) and new in (1,2)))
                                u.reg_write(UC_ARM64_REG_X0,0x20);u.reg_write(UC_ARM64_REG_X1,1)
                                u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                                u.emu_start(0x4038e3e4,0xa07000000,count=1000)
                                self.assertEqual(struct.unpack('<I',u.mem_read(MASK,4))[0],other|0x20)


if __name__=='__main__':unittest.main()
