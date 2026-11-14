# SPDX-License-Identifier: GPL-2.0-or-later
"""Portrait video histogram: skip the landscape-only FW 2.10 position preset when
the effective display direction (0x4038d658) is portrait. Rewrites 0x4041b248..0x4041b2c4
in place (same 31 instructions, compacted address builds); landscape path unchanged."""
import hashlib
from keystone import Ks,KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN
B=0x40160000;START=0x4041b248;END=0x4041b2c4
ORIG_SHA=None
ASM=[
 'cbz w11, #0x4041b2d8',
 'bl #0x4038d658',
 'cbnz w0, #0x4041b2d8',
 'movz x9, #0xfc50','movk x9, #0x32c, lsl #16','movk x9, #0xa, lsl #32',
 'ldrb w2, [x9, #0x13e]',
 'movz w0, #0x701c','movk w0, #0x42ae, lsl #16',
 'mov w1, #4','mov w3, wzr',
 'bl #0x403eb4a8',
 'mov w21, w0',
 'movz x8, #0x669','movk x8, #0x32d, lsl #16','movk x8, #0xa, lsl #32',
 'ldrb w8, [x8]',
 'cmp w8, #2',
 'b.ne #0x4041b2bc',
 'movz w0, #0x1dce','movk w0, #0x200, lsl #16',
 'bl #0x403ec398',
 'movz w8, #0xde0','movk w8, #0x42af, lsl #16',
 'movz w9, #0xdd0','movk w9, #0x42af, lsl #16',
 'cmp w0, #1',
 'csel x8, x9, x8, eq',
 'b #0x4041b2c4',
 'movz w8, #0xdc0','movk w8, #0x42af, lsl #16',
]
def code():
    ks=Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN);out=b''
    for i,ins in enumerate(ASM):
        enc,_=ks.asm(ins,addr=START+4*i);out+=bytes(enc)
    assert len(out)==END-START,(len(out),END-START)
    return out
def apply(img,stock):
    o=START-B
    assert bytes(img[o:END-B])==stock[o:END-B],'histogram preset block differs from stock'
    img[o:END-B]=code()
