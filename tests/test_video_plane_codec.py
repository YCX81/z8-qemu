# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent zlib/CRC checks for the private functional plane envelope."""
import ctypes
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name!='nt' and shutil.which('cc'), 'Linux C compiler required')
class PlaneCodec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.libs=[]
        for option in ('-O0','-O2'):
            out=Path(cls.temp.name)/(option[1:]+'.so')
            subprocess.run(['cc','-std=c11',option,'-shared','-fPIC','-Wall','-Wextra','-Werror',
                            '-I'+str(ROOT/'qemu'),str(ROOT/'tests/video_plane_wrapper.c'),
                            '-o',str(out),'-lz'],check=True)
            lib=ctypes.CDLL(str(out))
            lib.encode_plane.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,
                ctypes.c_uint,ctypes.c_uint,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t]
            lib.encode_plane.restype=ctypes.c_size_t
            lib.decode_plane.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
            lib.decode_plane.restype=ctypes.c_int
            cls.libs.append(lib)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def make_packet(self,lib):
        raw=bytes((i*17+i//127)&255 for i in range(128*64))
        source=ctypes.create_string_buffer(raw)
        target=ctypes.create_string_buffer(16384)
        count=lib.encode_plane(source,128,64,128,0,7,target,len(target))
        self.assertGreater(count,80)
        return raw,bytearray(target.raw[:count])

    def test_full_independent_decode(self):
        for lib in self.libs:
            raw,packet=self.make_packet(lib)
            count=struct.unpack_from('<Q',packet,40)[0]
            self.assertEqual(zlib.decompress(packet[80:80+count]),raw)
            self.assertEqual(zlib.crc32(raw),struct.unpack_from('<I',packet,64)[0])
            decoded=ctypes.create_string_buffer(len(raw))
            self.assertEqual(lib.decode_plane(bytes(packet),len(packet),decoded,len(raw)),1)
            self.assertEqual(decoded.raw,raw)

    def test_corruption_bounds_and_padding(self):
        for lib in self.libs:
            raw,packet=self.make_packet(lib)
            mutations=[]
            for off in (0,16,48,64,76,80):
                changed=packet.copy();changed[off]^=1;mutations.append(changed)
            changed=packet.copy();changed[-1]=1;mutations.append(changed)
            changed=packet.copy();struct.pack_into('<I',changed,28,2)
            struct.pack_into('<I',changed,76,zlib.crc32(changed[:76]));mutations.append(changed)
            changed=packet.copy();struct.pack_into('<I',changed,64,0)
            struct.pack_into('<I',changed,76,zlib.crc32(changed[:76]));mutations.append(changed)
            mutations += [packet[:79],packet[:-1]]
            for bad in mutations:
                out=ctypes.create_string_buffer(len(raw))
                self.assertEqual(lib.decode_plane(bytes(bad),len(bad),out,len(raw)),0)
            out=ctypes.create_string_buffer(len(raw))
            self.assertEqual(lib.decode_plane(bytes(packet),len(packet),out,len(raw)-1),0)

    def test_encode_rejects_invalid_ownership_and_geometry(self):
        for lib in self.libs:
            raw=ctypes.create_string_buffer(bytes(8192));out=ctypes.create_string_buffer(16384)
            for width,height,stride,plane,epoch,capacity in (
                (0,64,128,0,1,16384),(127,64,128,0,1,16384),
                (128,64,64,0,1,16384),(128,64,128,2,1,16384),
                (128,64,128,0,0,16384),(128,64,128,0,1,80)):
                self.assertEqual(lib.encode_plane(raw,width,height,stride,plane,epoch,out,capacity),0)
            self.assertEqual(lib.encode_plane(out,128,64,128,0,1,out,16384),0)


if __name__=='__main__':unittest.main()
