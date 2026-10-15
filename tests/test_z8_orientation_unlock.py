# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded private-image construction and actual A64 mask-filter semantics."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_orientation_unlock import build_plan,write_experiment,PATCH_ADDRESS,PATCH_BYTES
from z8_orientation_unlock_run import UnlockedGuest
from z8_orientation_acceptance import OrientationGuest


class Guards(unittest.TestCase):
    def test_unidentified_input_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'new'
            with self.assertRaises(ValueError):write_experiment(bytes(4096),destination,menu=True)
            self.assertFalse(destination.exists())

    def test_loader_requires_explicit_boolean_scope_before_owner_allocation(self):
        for kwargs in ({},{'unlock_menu':1},{'unlock_video':'yes'},
                       {'unlock_menu':True,'menu_layout':1},{'unlock_video':True,'menu_layout':True},
                       {'unlock_video':True,'video_layout':1},
                       {'unlock_video':True,'video_portrait':1},
                       {'unlock_video':True,'video_file_rotation':1},
                       {'unlock_menu':True,'video_file_rotation':True},
                       {'unlock_menu':True,'unlock_video':True,'video_portrait':True},
                       {'unlock_video':True,'video_layout':True,'video_portrait':True},
                       {'unlock_menu':True,'unlock_video':True,'video_layout':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):UnlockedGuest(**kwargs)

    def test_loader_records_actual_kernel_and_verifies_loaded_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.bin';original.write_bytes(b'original')
            for mode in ('good','wrong-loaded','wrong-geometry','already-replaced','build-failed'):
                with self.subTest(mode=mode):
                    guest=object.__new__(UnlockedGuest);guest.out=root;guest.firmware=original
                    guest.unlock_menu=True;guest.unlock_video=True;guest.menu_layout=False;guest.video_layout=False;guest.video_portrait=False;guest.video_file_rotation=False;guest.data={}
                    guest.command=['qemu','-kernel',str(original) if mode!='already-replaced' else 'different']
                    guest.read_bytes=lambda at,*_:b'wrong' if mode=='wrong-loaded' or mode=='wrong-geometry' and at!=PATCH_ADDRESS else b'leaf'
                    metadata=dict(image_sha256='derived-identity',replacement_sha256=hashlib.sha256(b'leaf').hexdigest())
                    if mode=='wrong-geometry':
                        metadata['patch_sites']=[dict(address=0x40340be0,bytes=4,replacement_sha256=hashlib.sha256(b'good').hexdigest())]
                    with patch('z8_orientation_unlock_run.write_experiment',return_value=metadata,
                               side_effect=ValueError('guard') if mode=='build-failed' else None),\
                         patch.object(OrientationGuest,'__enter__',return_value=guest),\
                         patch.object(UnlockedGuest,'__exit__') as cleanup:
                        if mode!='good':
                            with self.assertRaises((ValueError,RuntimeError)):guest.__enter__()
                            cleanup.assert_called_once()
                        else:
                            self.assertIs(guest.__enter__(),guest)
                            self.assertEqual(guest.command[2],str(root/'rotation-image/derived-main.bin'))
                            self.assertEqual(guest.data['executed_kernel_sha256'],'derived-identity')
                            self.assertTrue(guest.data['rotation_unlock']['loaded_leaf_verified'])
                            self.assertTrue(guest.data['rotation_unlock']['loaded_patch_sites_verified'])
                            self.assertTrue(guest.data['program_modified_in_simulation'])
                            cleanup.assert_not_called()
                    self.assertEqual(original.read_bytes(),b'original')


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned local MAIN required')
class NativeUnlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path(os.environ['Z8_MAIN']).read_bytes()

    def test_scopes_are_explicit_and_boolean(self):
        for kwargs in ({},{'menu':1},{'video':'yes'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):build_plan(self.source,**kwargs)

    def test_only_the_guarded_native_leaf_is_changed(self):
        plan=build_plan(self.source,menu=True,video=True)
        at=PATCH_ADDRESS-0x40160000
        self.assertEqual(len(plan['image']),len(self.source))
        self.assertEqual(plan['image'][:at],self.source[:at])
        self.assertEqual(plan['image'][at+PATCH_BYTES:],self.source[at+PATCH_BYTES:])
        self.assertEqual(plan['source_sha256'],hashlib.sha256(self.source).hexdigest())
        self.assertEqual(plan['suppressed_mask'],0x21)
        with self.assertRaises(ValueError):build_plan(plan['image'],menu=True)

    def test_original_set_clear_semantics_preserve_every_other_bit_and_abi(self):
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X10,UC_ARM64_REG_LR,UC_ARM64_REG_SP
        address=0xa03228308
        for menu,video in ((True,False),(False,True),(True,True)):
            plan=build_plan(self.source,menu=menu,video=video)
            u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);base=PATCH_ADDRESS&~4095
            u.mem_map(base,4096);u.mem_write(PATCH_ADDRESS,plan['replacement'])
            u.mem_map(address&~4095,4096);u.mem_map(0x50000000,4096)
            for previous in (0,1,0x20,0x23,0x8000003f,0xffffffff):
                for requested in (1,2,4,8,0x10,0x20,0x80000000,0xffffffff):
                    for enabled in (0,1):
                        with self.subTest(menu=menu,video=video,previous=previous,requested=requested,enabled=enabled):
                            u.mem_write(address,struct.pack('<I',previous))
                            u.reg_write(UC_ARM64_REG_X0,requested);u.reg_write(UC_ARM64_REG_X1,enabled)
                            u.reg_write(UC_ARM64_REG_X10,0x123456789abcdef0)
                            u.reg_write(UC_ARM64_REG_SP,0x50000ff0);u.reg_write(UC_ARM64_REG_LR,0x50000000)
                            u.emu_start(PATCH_ADDRESS,0x50000000,count=100)
                            expected=(previous|requested) if enabled else (previous&~requested)
                            expected&=~plan['suppressed_mask']&0xffffffff
                            self.assertEqual(struct.unpack('<I',u.mem_read(address,4))[0],expected)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_X0),requested)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_X1),enabled)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_X10),0x123456789abcdef0)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_SP),0x50000ff0)

    def test_output_is_new_and_contains_bound_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'experiment'
            result=write_experiment(self.source,destination,menu=True)
            self.assertTrue((destination/'derived-main.bin').is_file())
            self.assertEqual(result['suppressed_mask'],0x20)
            self.assertFalse(result['physical_camera_verified'])
            with self.assertRaises(FileExistsError):write_experiment(self.source,destination,video=True)

    def test_native_video_and_menu_callers_preserve_auto_switch_and_layout_lock(self):
        from test_z8_orientation_policy import NativeRotationPolicy,MASK
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_LR
        helper=NativeRotationPolicy();helper.source=self.source
        plan=build_plan(self.source,menu=True,video=True)
        for enabled in (0,1):
            for video in (0,1):
                for layout in (0,1):
                    for direction in (1,2):
                        with self.subTest(enabled=enabled,video=video,layout=layout,direction=direction):
                            u,_=helper.fixture(enabled=enabled,video=video,layout=layout,direction=direction)
                            u.mem_write(PATCH_ADDRESS,plan['replacement'])
                            u.emu_start(0x40507144,0x40507150,count=1000)
                            u.emu_start(0x4046dcf0,0x4046dd04,count=2000)
                            self.assertEqual(struct.unpack('<I',u.mem_read(MASK,4))[0],2 if layout else 0)
                            u.reg_write(UC_ARM64_REG_LR,0xa07000000)
                            u.emu_start(0x4038d59c,0xa07000000,count=1000)
                            self.assertEqual(u.reg_read(UC_ARM64_REG_X0),direction if enabled and not layout else 0)


if __name__=='__main__':unittest.main()
