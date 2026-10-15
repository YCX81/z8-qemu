# SPDX-License-Identifier: GPL-2.0-or-later
"""Imported F0 encoder versus an independent Python bit reader; no camera claim."""
import ctypes as C
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

def unpack_f0(blob,width,height,seeds):
    # Serialized canonical 14-bit lossless tree from the documented F0 format.
    counts=(0,1,4,2,2,3,1,2,0,0,0,0,0,0,0,0)
    symbols=(7,6,8,5,9,4,10,3,11,12,2,0,1,13,14)
    lookup={};code=0;index=0
    for length,count in enumerate(counts,1):
        for _ in range(count):lookup[length,code]=symbols[index];code+=1;index+=1
        code*=2
    bits=iter((byte>>shift)&1 for byte in blob for shift in range(7,-1,-1))
    vertical=[list(seeds[:2]),list(seeds[2:])];result=[]
    for y in range(height):
        row=[];horizontal=[0,0]
        for x in range(width):
            code=0
            for length in range(1,17):
                code=code*2+next(bits)
                if (length,code) in lookup:break
            else:raise ValueError('Invalid Huffman prefix')
            category=lookup[length,code];difference=0
            for _ in range(category):difference=difference*2+next(bits)
            if category and difference < (1<<(category-1)):difference-=((1<<category)-1)
            previous=vertical[y%2][x] if x<2 else horizontal[x%2]
            value=previous+difference
            if not 0<=value<=16383:raise ValueError('Out of range decoded sample')
            if x<2:vertical[y%2][x]=value
            horizontal[x%2]=value;row.append(value)
        result.append(row)
    return result

def packed(rows,padding=0xa5):
    width=len(rows[0]);stride=((width*7//4+31)//32)*32
    body=bytearray()
    for row in rows:
        word=sum(value<<(14*i) for i,value in enumerate(row))
        line=word.to_bytes(width*7//4,'little')
        body+=line+bytes([padding])*(stride-len(line))
    return bytes(body),stride

class Lossless14(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='f0-lossless14-')
        cls.libs=[]
        for opt in ('-O0','-O2'):
            target=Path(cls.tmp.name)/('codec'+opt+'.so')
            subprocess.run(['cc','-shared','-fPIC','-std=c11','-Wall','-Wextra','-Werror',opt,
                '-I',str(ROOT/'qemu'),str(ROOT/'tests/lossless14_wrapper.c'),'-o',str(target)],check=True,capture_output=True)
            lib=C.CDLL(str(target));f=lib.lossless14_test_encode
            f.argtypes=[C.c_void_p,C.c_size_t,C.c_uint,C.c_uint,C.c_uint,C.c_void_p,C.c_void_p,C.c_size_t,C.c_void_p]
            f.restype=C.c_bool;cls.libs.append(f)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def encode(self,f,rows,seeds=(2048,)*4,capacity=None,padding=0xa5,overrides=None):
        body,stride=packed(rows,padding);capacity=len(body)*2+64 if capacity is None else capacity
        source=C.create_string_buffer(body);out=(C.c_ubyte*(capacity+32))(*([0x6d]*(capacity+32)))
        predictors=(C.c_uint16*4)(*seeds);written=C.c_size_t(12345)
        args=[source,len(body),len(rows[0]),len(rows),stride,predictors,out,capacity,C.byref(written)]
        for index,value in (overrides or {}).items():args[index]=value
        ok=f(*args)
        self.assertEqual(bytes(out[capacity:]),bytes([0x6d])*32)
        if not ok:self.assertEqual(written.value,0)
        return ok,bytes(out[:written.value])

    def test_every_14bit_value_and_both_vertical_predictors(self):
        values=list(range(16384));random.Random(814).shuffle(values)
        rows=[values[i:i+256] for i in range(0,len(values),256)]
        for f in self.libs:
            ok,blob=self.encode(f,rows);self.assertTrue(ok)
            self.assertEqual(unpack_f0(blob,256,64,(2048,)*4),rows)

    def test_flat_smooth_and_extreme_differences(self):
        for f in self.libs:
            for rows in ([[0]*64 for _ in range(16)], [[16383]*64 for _ in range(16)],
                         [[(x*3+y*17)&16383 for x in range(64)] for y in range(16)],
                         [[0,16383,16383,0]*16 for _ in range(16)]):
                ok,blob=self.encode(f,rows);self.assertTrue(ok)
                self.assertEqual(unpack_f0(blob,64,16,(2048,)*4),rows)
                self.assertNotEqual(len(blob),((64*7//4+15)//16*16)*16)

    def test_padding_not_encoded_and_nondefault_seeds(self):
        rows=[[x+y for x in range(12)] for y in range(8)];seeds=(0,1,2,3)
        for f in self.libs:
            ok,a=self.encode(f,rows,seeds,padding=0);ok2,b=self.encode(f,rows,seeds,padding=255)
            self.assertTrue(ok and ok2);self.assertEqual(a,b)
            self.assertEqual(unpack_f0(a,12,8,seeds),rows)

    def test_capacity_and_invalid_input_publish_no_length(self):
        rows=[[0,1,2,3]*8 for _ in range(4)]
        for f in self.libs:
            for cap in (0,1,4):self.assertFalse(self.encode(f,rows,capacity=cap)[0])
            for overrides in ({0:None},{1:1},{2:0},{2:3},{3:0},{4:1},{5:None},{6:None}):
                self.assertFalse(self.encode(f,rows,overrides=overrides)[0])
            self.assertFalse(self.encode(f,rows,seeds=(0,1,2,16384))[0])

if __name__=='__main__':unittest.main()
