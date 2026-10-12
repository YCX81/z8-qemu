# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored A64 persistence for the C10 simulation-only extension.

Two explicitly reserved blocks on a new laboratory NAND image are used. They
are NOT claimed free on a real camera. The guest performs every register
transaction and CRC; the host never serializes/reinstates Picture Controls.
"""
from z8_picture_control import _assemble, _constant, DATA_BASE, LOAD_BUFFER, RECORD_BYTES

BASE = 0x45008000
SAVE, LOAD, SCAN, VALIDATE, IO, CRC = (BASE + n for n in (0, 0x400, 0x800, 0xa00, 0xc00, 0xe00))
BLOCKS = (2046 * 64, 2047 * 64)
HEADER_BUFFER = DATA_BASE + 0x38000
MAGIC = int.from_bytes(b'Z8C10AB1', 'little')
IO_ERROR = (1 << 64) - 1


def build_media_code():
    """Return one self-contained guest region; requires exclusive NAND use.

    save/load: x0=buffer, x1=0x6000; return x0=0x6000 or zero.
    A record becomes committed only after its header page is written last.
    On failed replacement, the previously validated block remains untouched.
    VALIDATE distinguishes an invalid record (0) from a read error (UINT64_MAX).
    A read error aborts SAVE before erase/program; that sentinel is never a
    permitted generation. SAVE input must not overlap the scan scratch buffers.
    SAVE/LOAD preserve DAIF and mask IRQ/FIQ only within the media operation.
    The surrounding native settings/mutex calls retain their task context.
    """
    code = bytearray(0x1000)
    c = _constant
    def emit(address, body, limit):
        data = _assemble(body, address)
        if len(data) > limit: raise ValueError('C10 media routine exceeds reserved code')
        code[address - BASE:address - BASE + len(data)] = data

    check = f'''
        mov x8,#0x6000; cmp x1,x8; b.ne bad;
        {c('x8',0xa00000000)} cmp x0,x8; b.lo bad;
        adds x9,x0,x1; b.cs bad; {c('x8',0xa20000000)} cmp x9,x8; b.hi bad;
    '''
    nonoverlap = ''
    for index, (start, size) in enumerate(((LOAD_BUFFER, RECORD_BYTES), (HEADER_BUFFER, 0x800))):
        nonoverlap += f'''
            {c('x8',start)} cmp x9,x8; b.ls separate_{index};
            {c('x8',start+size)} cmp x0,x8; b.lo bad;
            separate_{index}:
        '''
    emit(SAVE, f'''
        stp x19,x20,[sp,#-64]!; stp x21,x22,[sp,#16]; stp x23,x30,[sp,#32];
        mrs x9,daif; str x9,[sp,#48]; msr daifset,#3;
        {check} {nonoverlap} mov x19,x0; bl #{SCAN};
        cmn x0,#1; b.eq bad; cmn x1,#1; b.eq bad;
        cmp x0,x1; b.hi a_latest;
        mov x20,#{BLOCKS[0]}; mov x21,x1; b chosen;
        a_latest: mov x20,#{BLOCKS[1]}; mov x21,x0;
        chosen: adds x21,x21,#1; b.cs bad; cmn x21,#1; b.eq bad;
        mov x0,x20; mov x1,#1; mov x2,#0; mov x3,#2; bl #{IO}; cbz w0,bad;
        add x0,x20,#1; mov x1,#12; mov x2,x19; mov x3,#1; bl #{IO}; cbz w0,bad;
        mov x0,x19; mov x1,#0x6000; bl #{CRC}; mov w22,w0;
        {c('x23',HEADER_BUFFER)} mov x8,x23; mov x9,#128;
        clear_header: stp xzr,xzr,[x8],#16; subs x9,x9,#1; b.ne clear_header;
        {c('x8',MAGIC)} str x8,[x23]; mov w8,#1; str w8,[x23,#8];
        mov w8,#0x6000; str w8,[x23,#12]; str x21,[x23,#16]; str w22,[x23,#24];
        mov x0,x23; mov x1,#28; bl #{CRC}; str w0,[x23,#28];
        mov x0,x20; mov x1,#1; mov x2,x23; mov x3,#1; bl #{IO}; cbz w0,bad;
        mov x0,x20; bl #{VALIDATE}; cmp x0,x21; b.ne bad;
        mov x0,#0x6000; b done;
        bad: mov x0,#0;
        done: ldr x9,[sp,#48]; msr daif,x9;
        ldp x23,x30,[sp,#32]; ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#64; ret;
    ''', 0x400)
    emit(LOAD, f'''
        stp x19,x20,[sp,#-48]!; stp x21,x30,[sp,#16];
        mrs x9,daif; str x9,[sp,#32]; msr daifset,#3;
        {check} mov x19,x0; bl #{SCAN};
        cmn x0,#1; b.eq bad; cmn x1,#1; b.eq bad;
        orr x8,x0,x1; cbz x8,bad; cmp x0,x1; b.hs prefer_a;
        mov x20,#{BLOCKS[1]}; mov x21,#{BLOCKS[0]}; b read_choice;
        prefer_a: mov x20,#{BLOCKS[0]}; mov x21,#{BLOCKS[1]};
        read_choice: mov x0,x20; bl #{VALIDATE}; cmn x0,#1; b.eq bad; cbnz x0,copy_result;
        mov x0,x21; bl #{VALIDATE}; cmn x0,#1; b.eq bad; cbz x0,bad;
        copy_result: {c('x8',LOAD_BUFFER)} cmp x19,x8; b.eq good;
        mov x9,#0x600; copy: ldp x10,x11,[x8],#16; stp x10,x11,[x19],#16;
        subs x9,x9,#1; b.ne copy;
        good: mov x0,#0x6000; b done;
        bad: mov x0,#0;
        done: ldr x9,[sp,#32]; msr daif,x9;
        ldp x21,x30,[sp,#16]; ldp x19,x20,[sp],#48; ret;
    ''', 0x400)
    emit(SCAN, f'''
        stp x19,x30,[sp,#-16]!;
        mov x0,#{BLOCKS[0]}; bl #{VALIDATE}; mov x19,x0;
        mov x0,#{BLOCKS[1]}; bl #{VALIDATE}; mov x1,x0; mov x0,x19;
        ldp x19,x30,[sp],#16; ret;
    ''', 0x200)
    emit(VALIDATE, f'''
        stp x19,x20,[sp,#-48]!; stp x21,x30,[sp,#16];
        mov x19,x0; {c('x20',HEADER_BUFFER)}
        mov x1,#1; mov x2,x20; mov x3,#0; bl #{IO}; cbz w0,io_error;
        ldr x8,[x20]; {c('x9',MAGIC)} cmp x8,x9; b.ne bad;
        ldr w8,[x20,#8]; cmp w8,#1; b.ne bad;
        ldr w8,[x20,#12]; mov w9,#0x6000; cmp w8,w9; b.ne bad;
        ldr x21,[x20,#16]; cbz x21,bad;
        mov x0,x20; mov x1,#28; bl #{CRC}; ldr w8,[x20,#28]; cmp w0,w8; b.ne bad;
        add x0,x19,#1; mov x1,#12; {c('x2',LOAD_BUFFER)} mov x3,#0; bl #{IO}; cbz w0,io_error;
        {c('x0',LOAD_BUFFER)} mov x1,#0x6000; bl #{CRC};
        ldr w8,[x20,#24]; cmp w0,w8; b.ne bad;
        mov x0,x21; b done;
        bad: mov x0,#0; b done;
        io_error: mov x0,#-1;
        done: ldp x21,x30,[sp,#16]; ldp x19,x20,[sp],#48; ret;
    ''', 0x200)
    emit(IO, f'''
        {c('x8',0x17a0032c)} ldr w9,[x8]; cmp w9,#1; b.ne bad;
        {c('x8',0x17a00300)} ldr w9,[x8]; cmp w9,#7; b.ne bad;
        {c('x8',0x17800000)} mov w9,#-1; str w9,[x8,#0x138]; str w9,[x8,#0x128];
        str w0,[x8,#4]; cmp w3,#2; b.eq erase;
        sub x9,x1,#1; cmp x9,#255; b.hi bad;
        {c('x10',0xa00000000)} sub x2,x2,x10; mov x10,#0x40000000; add x2,x2,x10;
        str w2,[x8,#8]; str wzr,[x8,#12]; mov w10,#1; str w10,[x8,#0x400];
        mov w10,#0x800; movk w10,#0x800,lsl #16; str w10,[x8,#0x404];
        mov w10,#0x2200; movk w10,#0x4020,lsl #16; cbz w3,command;
        mov w10,#0x2100; movk w10,#0x4020,lsl #16;
        command: orr w10,w10,w9; b issue;
        erase: mov w10,#0x1000; movk w10,#0x4000,lsl #16;
        issue: str w10,[x8]; dsb sy; ldr w9,[x8,#0x14]; mov w10,#0x8000;
        cmp w9,w10; cset w0,eq; ret;
        bad: mov w0,#0; ret;
    ''', 0x200)
    emit(CRC, f'''
        mov w2,#-1; mov w5,#0x8320; movk w5,#0xedb8,lsl #16; cbz x1,done;
        byte: ldrb w3,[x0],#1; eor w2,w2,w3; mov w4,#8;
        bit: and w3,w2,#1; lsr w2,w2,#1; cbz w3,next; eor w2,w2,w5;
        next: subs w4,w4,#1; b.ne bit; subs x1,x1,#1; b.ne byte;
        done: mvn w0,w2; ret;
    ''', 0x100)
    return dict(address=BASE, data=bytes(code), permissions='rx',
                nand_blocks=BLOCKS, record_bytes=RECORD_BYTES,
                scope='Simulation-only reserved A/B blocks; not a physical Z8 layout')
