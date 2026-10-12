# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent C-10 guest text resources for the original Nikon GUI renderer.

The original row setter accepts bitmap IDs. Only the five extension IDs are
converted to the original ASCII-text kind (3). Original font/layout/rendering
code remains responsible for pixels; there is no host-rendered label overlay.
"""
import struct

from z8_picture_control import BASE, DATA_BASE, MAIN_SHA256, _assemble, _check_source, _constant

LABEL_CODE, LABEL_DATA = 0x45007000, DATA_BASE + 0x37000
C10_LABEL_IDS = tuple(range(0x7FF0, 0x7FF5))
SETTER = 0x403F645C
RESOLVER = 0x4032E70C
TABLE = LABEL_DATA + 0x100
DEFAULT_STYLE_ID = 0x21F  # Original main LCD custom-name style.
MAX_STYLE_ID = 0x652  # Original 40695e3c style lookup, not 8-bit font face.


def build_label_plan(source, style_ids=None):
    """Create simulation-only hooks and five separately owned text records.

    None uses the original main LCD custom-name style. It does not depend on
    a dynamic string slot being initialized by some other page. Explicit style
    IDs must fit the original style-record table; they are not byte-sized font
    face IDs or Picture Control bitmap IDs.
    """
    source = _check_source(source)
    bound = 0x40695E44
    if source[bound - BASE:bound - BASE + 4] != _assemble(f'cmp w0,#{MAX_STYLE_ID}', bound):
        raise ValueError('Original native style-table boundary differs')
    if style_ids is not None and (len(style_ids) != 5 or
                                 any(type(s) is not int or not 0 <= s <= MAX_STYLE_ID for s in style_ids)):
        raise ValueError('Five validated native font-style IDs required')
    styles = (DEFAULT_STYLE_ID,) * 5 if style_ids is None else tuple(style_ids)
    code, data = bytearray(0x1000), bytearray(0x1000)
    for index, style in enumerate(styles):
        pointer = LABEL_DATA + index * 16
        data[index * 16:index * 16 + 5] = b'C-10\0'
        struct.pack_into('<QIII', data, 0x100 + index * 32, pointer, 4, style, 0)

    def emit(address, text, capacity):
        raw = _assemble(text, address)
        if len(raw) > capacity:
            raise ValueError('Label function exceeds owned code slot')
        code[address - LABEL_CODE:address - LABEL_CODE + len(raw)] = raw

    original_setter = source[SETTER - BASE:SETTER - BASE + 4]
    original_resolver = source[RESOLVER - BASE:RESOLVER - BASE + 4]
    # The two displaced instructions do not use PC-relative addressing. Their
    # exact bytes remain in the guarded source; all old IDs resume stock code.
    emit(LABEL_CODE, f'''
        mov w9,#0x7ff0; sub w8,w1,w9; cmp w8,#4; b.ls new_label;
        ldr w8,[x0,#8]; sub w8,w8,w9; cmp w8,#4; b.hi old_label;
        ldr w8,[x0,#12]; cmp w8,#3; b.ne old_label;
        stp x19,x20,[sp,#-32]!; str x30,[sp,#16]; mov x19,x0; mov w20,w1;
        bl #0x4032b9ec; mov w8,#1; str w8,[x19,#12];
        mov x0,x19; mov w1,#1; mov w2,w20; bl #0x40325d48; b setter_done;
        new_label: cbz x0,label_bad; ldr w8,[x0,#12];
        cmp w8,#1; b.eq allowed; cmp w8,#3; b.ne label_bad;
        allowed: stp x19,x20,[sp,#-32]!; str x30,[sp,#16]; mov x19,x0; mov w20,w1;
        mov w8,#3; str w8,[x19,#12]; mov w1,#3; mov w2,w20;
        bl #0x40325d48;
        setter_done: ldr x30,[sp,#16]; ldp x19,x20,[sp],#32; ret;
        old_label: .word {int.from_bytes(original_setter,'little')}; b #{SETTER+4};
        label_bad: mov w0,#0; ret;
    ''', 0x300)
    emit(LABEL_CODE + 0x400, f'''
        ldr w8,[x0]; mov w9,#0x7ff0; sub w8,w8,w9; cmp w8,#4; b.hi original_resource;
        {_constant('x9',TABLE)} add x9,x9,x8,lsl #5;
        ldr w10,[x9,#12]; cmp w10,#{MAX_STYLE_ID}; b.hi not_ready;
        ldr x11,[x9]; ldr w12,[x9,#8]; str x11,[x1]; str w12,[x2]; str w10,[x3]; ret;
        not_ready: str xzr,[x1]; str wzr,[x2]; str wzr,[x3]; ret;
        original_resource: .word {int.from_bytes(original_resolver,'little')}; b #{RESOLVER+4};
    ''', 0x300)
    patches = [dict(address=SETTER, expected=original_setter,
                    replacement=_assemble(f'b #{LABEL_CODE}', SETTER),
                    name='Five C10 IDs use native ASCII text kind; old IDs retain bitmap kind'),
               dict(address=RESOLVER, expected=original_resolver,
                    replacement=_assemble(f'b #{LABEL_CODE+0x400}', RESOLVER),
                    name='Resolve independent C-10 strings into native text rendering')]
    return dict(schema=1, source_sha256=MAIN_SHA256, simulation_only=True,
                patches=patches,
                regions=[dict(address=LABEL_CODE, data=bytes(code), permissions='rx'),
                         dict(address=LABEL_DATA, data=bytes(data), permissions='rw')],
                labels=[dict(resource_id=key, string_address=LABEL_DATA + i * 16,
                             descriptor=TABLE + i * 32, content='C-10',
                             font_style=styles[i]) for i, key in enumerate(C10_LABEL_IDS)],
                native_chain=[0x404A7A30, SETTER, 0x40325D48, 0x4032B648, RESOLVER],
                limitations=['Native glyph rendering is required; no host drawing is provided',
                             'Default uses verified LCD style0x21f, with original style-table bound0x652',
                             'Pixel appearance and clipping require full-QEMU GUI acceptance'])
