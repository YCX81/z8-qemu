# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-mux matrix locking against original A64 configuration and serializers."""
import hashlib,io,os,struct,unittest
from pathlib import Path
import test_z8_video_mux as mux
import test_z8_orientation_policy as policy
from z8_resources import MAIN_SHA
from z8_video_file_rotation import build_plan,POSE_MATRICES
from video_track_orientation import inspect_stream
import test_video_track_orientation as atoms


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class FileRotation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
        if hashlib.sha256(cls.source).hexdigest()!=MAIN_SHA:raise ValueError('Wrong MAIN')
        cls.plan=build_plan(cls.source)

    def test_only_two_original_function_extents_change(self):
        restored=bytearray(self.plan['image'])
        for site in self.plan['patch_sites']:
            off=site['address']-0x40160000;n=site['bytes']
            restored[off:off+n]=self.source[off:off+n]
        self.assertEqual(bytes(restored),self.source)
        self.assertEqual(len(self.plan['image']),len(self.source))
        self.assertFalse(self.plan['new_executable_mapping'])
        self.assertEqual(self.plan['additional_stack_bytes'],0)
        with self.assertRaises(ValueError):build_plan(self.plan['image'])

    def test_configure_preserves_native_fields_across_rates_modes_and_errors(self):
        original=mux.Fixture(self.source);patched=mux.Fixture(self.plan['image'])
        seed=original.read(0,0x1000)
        for fps in (24000,25000,30000,50000,60000,100000,120000,29970,0xffffffff):
            for kind in (0,1,2):
                for large,aux in ((0,0),(0,1),(1,0),(1,1)):
                    for f in (original,patched):
                        f.u.mem_write(mux.CTX,seed)
                        f.u.mem_write(mux.CTX,struct.pack('<I',kind))
                        f.u.mem_write(mux.CTX+4,bytes([large]))
                        f.u.mem_write(mux.CTX+0x128,bytes([0xa5])*36)
                        f.u.mem_write(mux.PARAM+12,struct.pack('<I',fps))
                        f.u.mem_write(mux.PARAM+0x54,struct.pack('<I',aux))
                        f.u.mem_write(policy.DIRECTION,struct.pack('<I',1))
                    self.assertEqual(original.call(mux.CONFIGURE,mux.CTX,mux.PARAM),patched.call(mux.CONFIGURE,mux.CTX,mux.PARAM))
                    a=original.read(0,0x1000);b=patched.read(0,0x1000)
                    self.assertEqual(a[:0x128]+a[0x14c:],b[:0x128]+b[0x14c:])
                    self.assertEqual(b[0x128:0x14c],POSE_MATRICES[1])
        for f in (original,patched):
            f.u.mem_write(mux.PARAM+0x20,struct.pack('<I',3))
            f.u.mem_write(mux.CTX+0x128,bytes([0x5a])*36)
            self.assertEqual(f.call(mux.CONFIGURE,mux.CTX,mux.PARAM),3)
            self.assertEqual(f.read(0x128,36),bytes([0x5a])*36)
            self.assertEqual(f.call(mux.CONFIGURE,0,mux.PARAM),3)
            self.assertEqual(f.call(mux.CONFIGURE,mux.CTX,0),3)

    def test_header_keeps_start_pose_while_movie_audio_and_timecode_stay_native(self):
        for kind in (0,1,2):
            for pose in (0,1,2,3,4,255):
                a=mux.Fixture(self.source,kind);b=mux.Fixture(self.plan['image'],kind)
                for f in (a,b):
                    f.u.mem_write(policy.DIRECTION,struct.pack('<I',pose))
                    self.assertEqual(f.call(mux.CONFIGURE,mux.CTX,mux.PARAM),0)
                expected=POSE_MATRICES[pose] if pose<3 else POSE_MATRICES[0]
                self.assertEqual(b.read(0x128,36),expected)
                for direction in (2,0,1):
                    for f in (a,b):
                        f.u.mem_write(policy.DIRECTION,struct.pack('<I',direction))
                        self.assertEqual(f.call(mux.VIDEO,mux.CTX),0)
                    self.assertEqual(b.read(0x128,36),expected)
                    aa=a.read(0,0x1000);bb=b.read(0,0x1000)
                    self.assertEqual(aa[:0x128]+aa[0x14c:],bb[:0x128]+bb[0x14c:])
                for pc,off in ((mux.MOVIE,0xb0),(mux.AUDIO,0x338),(mux.TIMECODE,0x63c)):
                    for f in (a,b):self.assertEqual(f.call(pc,mux.CTX,0),0)
                    self.assertEqual(a.read(off,36),b.read(off,36))
                self.assertEqual(b.call(mux.VIDEO,0),3)

    def test_objects_keep_independent_snapshots_and_new_configuration_resamples(self):
        import unicorn.arm64_const as regs
        f=mux.Fixture(self.plan['image']);other=mux.CTX+0x3000
        f.u.mem_write(other,f.read(0,0x1000))
        registers=[getattr(regs,f'UC_ARM64_REG_X{n}') for n in range(19,30)]
        registers.extend(getattr(regs,f'UC_ARM64_REG_D{n}') for n in range(8,16))
        saved={r:0x100000000+r for r in registers}
        for r,v in saved.items():f.u.reg_write(r,v)
        # Raw pose is used even when a UI/menu inhibit mask is set.
        f.u.mem_write(policy.MASK,struct.pack('<I',0xffffffff))
        for obj,pose in ((mux.CTX,1),(other,2)):
            f.u.mem_write(policy.DIRECTION,struct.pack('<I',pose))
            self.assertEqual(f.call(mux.CONFIGURE,obj,mux.PARAM),0)
        f.u.mem_write(policy.DIRECTION,bytes(4))
        for obj,pose,degrees in ((mux.CTX,1,90),(other,2,270)):
            self.assertEqual(f.call(mux.VIDEO,obj),0)
            self.assertEqual(bytes(f.u.mem_read(obj+0x128,36)),POSE_MATRICES[pose])
            header=bytes(f.u.mem_read(obj+0xf8,92))
            raw=atoms.box(b'moov',atoms.box(b'trak',header+atoms.box(b'mdia',atoms.box(b'hdlr',bytes(8)+b'vide'+bytes(12)))))
            parsed=inspect_stream(io.BytesIO(raw),len(raw))
            self.assertEqual(parsed['tracks'][0]['matrix']['canonical_rotation_degrees'],degrees)
        self.assertEqual(f.call(mux.CONFIGURE,other,mux.PARAM),0)
        self.assertEqual(bytes(f.u.mem_read(other+0x128,36)),POSE_MATRICES[0])
        self.assertEqual(f.read(0x128,36),POSE_MATRICES[1])
        for r,v in saved.items():self.assertEqual(f.u.reg_read(r),v)

    def test_composition_is_explicit_and_preserves_ui_bytes(self):
        from z8_orientation_unlock import build_plan as ui_plan
        from z8_video_file_rotation import patch_matrix_lifetime
        ui=ui_plan(self.source,video=True,video_portrait=True)
        combined=ui_plan(self.source,video=True,video_portrait=True,video_file_rotation=True)
        self.assertFalse(ui['video_file_rotation']);self.assertTrue(combined['video_file_rotation'])
        restored=bytearray(combined['image'])
        for site in self.plan['patch_sites']:
            at=site['address']-0x40160000;n=site['bytes']
            restored[at:at+n]=self.source[at:at+n]
        self.assertEqual(bytes(restored),ui['image'])
        for flags in ({'video':True,'video_file_rotation':1},
                      {'menu':True,'video_file_rotation':True}):
            with self.assertRaises(ValueError):ui_plan(self.source,**flags)
        with self.assertRaises(ValueError):patch_matrix_lifetime(self.source,bytearray(combined['image']))
        with self.assertRaises(ValueError):patch_matrix_lifetime(self.source,bytes(self.source))
        with self.assertRaises(ValueError):patch_matrix_lifetime(b'',bytearray())


if __name__=='__main__':unittest.main()
