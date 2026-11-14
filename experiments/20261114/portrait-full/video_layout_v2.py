# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-canvas portrait coordinates for the video live view (Z8 C3.11).

Video widgets follow the native photo-mode portrait layout: a video widget
with a photo twin takes the twin's native portrait rectangle (plus the row
offset the video-only status rows need); video-only widgets are placed in
the same 480x720 canvas. Nothing is confined to the 16:9 picture.
"""
import struct
TABLE_START=0x43a21a70;TABLE_END=0x43a2c53c;IMAGE_BASE=0x40160000
TOP=0    # v2: photo rows keep the photo y; video status goes into free photo slots

def R(a,b):return lambda i:a<=i<=b
def S(*ids):return lambda i:i in ids
# (predicate, dx, dy) applied to the landscape rectangle
RULES=[
 # v2 status: REC + elapsed timer in the free left part of photo row 2,
 # remaining time in the photo remaining-shots slot, format fields in row 3
 (lambda i:i in(0x1d9e,0x1d9f) or 0x1dad<=i<=0x1db1,90,181),
 (S(0x1ed9,0x1eda,0x1f7f),-99,179),
 (lambda i:0x1dbc<=i<=0x1dc2 or i in(0x1f7d,0x1f7e),-336,626),
 (S(0x1e95,0x1e96,0x1e97,0x1e9c,0x1e9d),134,93),
 (S(0x2772,0x2773),132,93),
 (S(0x1f33,0x1f34),-146,181),(S(0x2177,0x2178),-150,181),
 # photo row 1 twins (photo y6 -> portrait y6), video adds TOP
 (R(0x1f08,0x1f0b),0,-38+TOP),          # mode
 (S(0x1f5c,0x1f5d),-7,-38+TOP),
 (S(0x1ef4,0x1ef5),-14,-38+TOP),
 (S(0x1f46,0x1f47,0x1f48,0x1f49,0x277c,0x277d),-227,-38+TOP),
 (S(0x1f0c,0x1f0d),-234,-38+TOP),
 (R(0x1f3b,0x1f3e),-240,-37+TOP),
 # photo row 2 (portrait y50)
 (R(0x1ef6,0x1ef9),-261,6+TOP),
 (S(0x291b,0x291c),-321,6+TOP),
 (S(0x2782,0x2783,0x2784),-198,48),
 (S(0x1dc3,0x1dc4),-224,-40+TOP),
 # photo row 3 (portrait y94/95)
 (S(0x1f78,0x1f79),0,4+TOP),
 (R(0x1efa,0x1f00),0,4+TOP),(R(0x1f01,0x1f07),0,44+TOP),
 (S(0x1f31,0x1f32,0x1f35,0x1f36,0x1cfa,0x1cfb),0,5+TOP),
 (R(0x1f37,0x1f3a),0,45+TOP),
 (S(0x1f3f,0x1f40),-240,4+TOP),
 # row 4 and the left column
 (S(0x1f7b,0x1f7c,0x1f6c,0x1f6d),0,4+TOP),
 (S(0x1e88,),0,48+TOP),
 (S(0x1f76,0x1f77),-240,4+TOP),
 (S(0x1f6a,0x1f6b),0,4+TOP),
 (S(0x1ee5,0x1ee6,0x1e2a,0x1e2b,0x1e2c),0,122),
 (R(0x1de7,0x1e08),0,126),               # side audio meter
 (S(0x1f50,0x1f51),0,170),(S(0x1f4a,0x1f4b),0,162),
 (R(0x1f56,0x1f5b),0,170),
 (S(0x1d1f,0x1d20),0,172),               # touch control
 (R(0x1f0e,0x1f11),0,168),
 (R(0x1f5e,0x1f63),0,192),               # photo twin 1eaf..1eb4
 (S(0x1f12,0x1f13),0,193),(S(0x1f6e,0x1f6f),-62,167),
 # audio meter + microphone: own row above the exposure row
 (lambda i:0x1dc5<=i<=0x1de6 or 0x1da4<=i<=0x1da6 or i in(0x1ee7,0x1ee8),0,186),
 # exposure row = photo twins
 (lambda i:0x1f14<=i<=0x1f1e or i in(0x1f42,0x1f43),-182,240),
 (lambda i:0x1f1f<=i<=0x1f22 or i in(0x1f44,0x1f45),-179,240),
 (R(0x1f23,0x1f2b),-176,240),
 # right column = photo twins
 (R(0x1f64,0x1f69),-240,240),               # photo remaining-shots twin
 (S(0x1f2c,),-240,138),                  # battery
 (R(0x1f4c,0x1f4f),-140,156),(R(0x1f52,0x1f55),-140,156),
 (S(0x1f70,0x1f71),-140,154),(S(0x1f72,0x1f73),-140,156),
 (S(0x1f74,0x1f75),-120,244),
 (S(0x1d23,0x1d24),-240,156),            # i button
 (S(0x1f41,),-256,194),                  # histogram -> photo 1e6f portrait
 (S(0x2780,),-120,128),
 (S(0x1f7a,),-120,120),
 (lambda i:0x1ecb<=i<=0x1ed8 or i in(0x278c,0x278d,0x278e),-120,240),
]

def portrait_rect(ident,rect):
    l,t,r,b=rect
    for pred,dx,dy in RULES:
        if pred(ident):
            n=(l+dx,t+dy,r+dx,b+dy)
            if not(0<=n[0]<n[2]<=480 and 0<=n[1]<n[3]<=720):
                raise ValueError(f'{ident:#x} leaves the canvas: {n}')
            return n
    raise KeyError(f'no rule for {ident:#x} {rect}')

def rows(source):
    for address in range(TABLE_START,TABLE_END,68):
        off=address-IMAGE_BASE;row=source[off:off+68]
        f=struct.unpack('<17I',row)
        if not f[1]:break
        yield address,f,struct.unpack_from('<4h',row,24),struct.unpack_from('<4h',row,32)
