# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent F0 Huffman reader plus malformed/capacity encoder cases."""
import ctypes
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest

def decode_f0(blob,width,height,seeds):
    # Public Nikon F0 canonical table; reader uses bit-prefix matching, no
    # shared C implementation, packing routine or guest completion logic.
    counts=[0,1,4,2,2,3,1,2];cats=[7,6,8,5,9,4,10,3,11,12,2,0,1,13,14]
    table={};code=0;index=0
    for bits,n in enumerate(counts,1):
        for _ in range(n):table[format(code,f'0{bits}b')]=cats[index];code+=1;index+=1
        code<<=1
    stream=''.join(format(v,'08b') for v in blob);at=0;vertical=[list(seeds[:2]),list(seeds[2:])];rows=[]
    for y in range(height):
        row=[]
        for x in range(width):
            prefix=''
            while prefix not in table:
                prefix+=stream[at];at+=1
                if len(prefix)>16:raise ValueError('Invalid Huffman prefix')
            n=table[prefix];value=int(stream[at:at+n],2) if n else 0;at+=n
            diff=value if not n or value&(1<<(n-1)) else value-((1<<n)-1)
            previous=vertical[y%2][x] if x<2 else row[x-2]
            sample=previous+diff;row.append(sample)
            if x<2:vertical[y%2][x]=sample
        rows.append(row)
    return rows

class LosslessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name)
        src=root/'wrapper.c'
        header=Path(__file__).resolve().parents[1]/'qemu/nikon-expeed-lossless.h'
        src.write_text('#include "'+str(header)+'"\n'
            'bool encode(const uint8_t *p,size_t bytes,unsigned w,unsigned h,unsigned pitch,const uint16_t *seeds,uint8_t *out,size_t cap,size_t *n) { return nikon_z8_lossless14_encode(p,bytes,w,h,pitch,seeds,out,cap,n); }\n')
        lib=root/'encoder.so'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',str(src),'-o',str(lib)],check=True,capture_output=True)
        cls.library=ctypes.CDLL(str(lib));cls.encode=cls.library.encode
        cls.encode.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
        cls.encode.restype=ctypes.c_bool
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def call(self,rows,cap=8192,seeds=(2048,)*4,**changes):
        width=len(rows[0]);height=len(rows);pitch=((width*7//4+31)//32)*32
        raw=b''.join(sum(v<<(14*x) for x,v in enumerate(row)).to_bytes(width*7//4,'little').ljust(pitch,b'\xa5') for row in rows)
        source=ctypes.create_string_buffer(raw);out=ctypes.create_string_buffer(cap)
        written=ctypes.c_size_t(999);seed=(ctypes.c_uint16*4)(*seeds)
        args=dict(bytes=len(raw),w=width,h=height,pitch=pitch);args.update(changes)
        ok=self.encode(source,args['bytes'],args['w'],args['h'],args['pitch'],seed,out,cap,ctypes.byref(written))
        return ok,out.raw[:written.value],written.value
    def test_every_14bit_value_predictors_and_padding(self):
        randomizer=random.Random(41)
        for rows in ([[2048]*64 for _ in range(4)],[[0,16383,8192,1]*16 for _ in range(4)],
                     [list(range(i,i+64)) for i in range(0,16384,64)],
                     [[randomizer.randrange(16384) for _ in range(64)] for _ in range(8)]):
            ok,blob,n=self.call(rows,cap=65536)
            self.assertTrue(ok);self.assertGreater(n,4)
            self.assertEqual(decode_f0(blob,len(rows[0]),len(rows),(2048,)*4),rows)
    def test_capacity_and_bad_geometry_never_publish_partial(self):
        rows=[[0,16383]*32 for _ in range(4)]
        for options in (dict(cap=1),dict(cap=4),dict(w=3),dict(h=0),dict(pitch=1),dict(bytes=1),dict(seeds=(16384,)*4)):
            with self.subTest(options=options):
                ok,_,n=self.call(rows,**options);self.assertFalse(ok);self.assertEqual(n,0)

if __name__=='__main__':unittest.main()
