# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute original C3.11 mux code on synthetic objects, not camera files."""
import hashlib,io,os,struct,unittest
from pathlib import Path
import test_z8_orientation_policy as policy
import test_video_track_orientation as atoms
from video_track_orientation import inspect_stream
from z8_resources import MAIN_SHA

VIDEO=0x40cef3d0
MOVIE=0x40cef168
AUDIO=0x40cf0880
TIMECODE=0x40cefe80
CONFIGURE=0x40cf3a24
CTX=0xa07001000
PARAM=0xa07008000
STOP=0xa07000000
STACK=0xa0700fff0
POINTERS=(0x208,0x228,0x1c0,0x170,0x730,0x700,0x760,0x6b0,0x410,0x430,0x3d0,0x380)
IDENTITY=struct.pack('>9i',*atoms.IDENTITY)


class Fixture:
    def __init__(self,source,kind=0):
        from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1
        helper=policy.NativeRotationPolicy();helper.source=source
        self.u,_=helper.fixture()
        self.u.mem_map(0xa07100000,0x100000)
        self.u.mem_map(0xa0497d000,4096)
        # Original41186810 classifies the fixture as coherent memory.
        # No cache callbacks, atom writers or original return values are stubbed.
        self.u.mem_write(0xa0497d030,struct.pack('<QQQ',0,1,0xffffffffffffffff))
        self.u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20)
        self.u.mem_write(CTX,struct.pack('<I',kind))
        self.u.mem_write(CTX+0x55c,struct.pack('<I',1))
        self.u.mem_write(CTX+0x590,struct.pack('<I',1))
        for n,offset in enumerate(POINTERS):
            self.u.mem_write(CTX+offset,struct.pack('<Q',0xa07100000+n*4096))
        data=bytearray(0x68)
        struct.pack_into('<4I',data,0,1920,1080,1000,60000)
        self.u.mem_write(PARAM,bytes(data))
    def call(self,pc,*args):
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_LR,UC_ARM64_REG_SP,UC_ARM64_REG_PC
        for r,v in zip((UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2),args):self.u.reg_write(r,v)
        self.u.reg_write(UC_ARM64_REG_LR,STOP);self.u.reg_write(UC_ARM64_REG_SP,STACK)
        self.u.emu_start(pc,STOP,count=1000000)
        if self.u.reg_read(UC_ARM64_REG_PC)!=STOP or self.u.reg_read(UC_ARM64_REG_SP)!=STACK:
            raise AssertionError('Native function did not return with balanced stack')
        return self.u.reg_read(UC_ARM64_REG_X0)
    def read(self,offset,size):return bytes(self.u.mem_read(CTX+offset,size))


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned original MAIN required')
class NativeMux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['Z8_MAIN']).read_bytes()
        if hashlib.sha256(cls.source).hexdigest()!=MAIN_SHA:raise ValueError('Wrong MAIN')

    def test_original_video_header_writes_identity_independent_of_ui_pose(self):
        for kind in (0,1,2):
            f=Fixture(self.source,kind)
            self.assertEqual(f.call(CONFIGURE,CTX,PARAM),0)
            for direction in (0,1,2):
                f.u.mem_write(policy.DIRECTION,struct.pack('<I',direction))
                f.u.mem_write(CTX+0x128,struct.pack('>9i',*atoms.ROT90))
                self.assertEqual(f.call(VIDEO,CTX),0)
                self.assertEqual(f.read(0x128,36),IDENTITY)
                self.assertEqual(f.read(0xf8,8),struct.pack('>I4s',92,b'tkhd'))
                self.assertEqual(f.read(0x14c,8),struct.pack('>2I',1920<<16,1080<<16))

    def test_movie_audio_timecode_and_video_own_separate_matrices(self):
        f=Fixture(self.source)
        self.assertEqual(f.call(CONFIGURE,CTX,PARAM),0)
        self.assertEqual(f.call(VIDEO,CTX),0)
        video=f.read(0xf8,92)
        for pc,offset in ((MOVIE,0xb0),(AUDIO,0x338),(TIMECODE,0x63c)):
            self.assertEqual(f.call(pc,CTX,0),0)
            self.assertEqual(f.read(offset,36),IDENTITY)
            self.assertEqual(f.read(0xf8,92),video)
        # Synthetic container wraps exact bytes emitted by the native
        # video and movie constructors; no encoded samples or real file.
        native_movie=f.read(0x84,108)
        raw=atoms.box(b'moov',native_movie+atoms.box(b'trak',video+atoms.box(b'mdia',atoms.box(b'hdlr',bytes(8)+b'vide'+bytes(12)))))
        parsed=inspect_stream(io.BytesIO(raw),len(raw))
        self.assertEqual(parsed['tracks'][0]['matrix']['canonical_rotation_degrees'],0)
        self.assertEqual(parsed['movie_matrices'][0]['canonical_rotation_degrees'],0)

    def test_configure_success_and_failure_leave_matrix_slot_untouched(self):
        f=Fixture(self.source);sentinel=bytes([0xa5])*36
        f.u.mem_write(CTX+0x128,sentinel)
        self.assertEqual(f.call(CONFIGURE,CTX,PARAM),0)
        self.assertEqual(f.read(0x128,36),sentinel)
        f.u.mem_write(PARAM+0x20,struct.pack('<I',3))
        self.assertEqual(f.call(CONFIGURE,CTX,PARAM),3)
        self.assertEqual(f.read(0x128,36),sentinel)
        self.assertEqual(f.call(CONFIGURE,0,PARAM),3)
        self.assertEqual(f.call(CONFIGURE,CTX,0),3)


if __name__=='__main__':unittest.main()
