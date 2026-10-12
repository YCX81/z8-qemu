# SPDX-License-Identifier: GPL-2.0-or-later
"""Simulation-only C32/C64 bindings for the original C3.11 GUI list.

This retains the original nine-item constructor, adds the configured bounded native rows,
and relocates its shared arrays. Native C-10 label resources are a separate
required adapter; no host overlay or borrowed C-9 label is a valid substitute.
"""
import struct

from z8_picture_control import BASE, MAIN_SHA256, _check_source, _assemble, _constant
from z8_slots_layout import Layout
from z8_slots_core import decode_asm, record_asm

# Audited explicit MOVZ/MOVK references in the pinned AArch64 text view.
# Every sequence is reconstructed and checked before a replacement is emitted.
LITERALS = {
    0xA03417898: (0x404415A0, 0x40443224, 0x40453364, 0x40453454, 0x404537B4,
                  0x4045A0FC, 0x4045A234, 0x4048EB90, 0x4048EE08, 0x4048F144,
                  0x4049A450, 0x4049E230),
    0xA032F8534: (0x4043F99C, 0x404534F8, 0x40453674, 0x4048EEA0, 0x4048F040,
                  0x4049C8A8, 0x4049F1C0, 0x404A7080, 0x404E50D4, 0x404E522C),
    0xA032E5EB8: (0x40451D68, 0x404523B8, 0x40452424, 0x40452470, 0x40452634,
                  0x40452680, 0x40452E70, 0x40452EE8, 0x404531C0, 0x404533A8,
                  0x40453800, 0x40453940, 0x40454544, 0x404546D0, 0x4045501C,
                  0x404551D8, 0x40455E10, 0x404563A4, 0x4045667C, 0x4045684C,
                  0x40456A64, 0x40456C08, 0x40456DC0, 0x40456F5C, 0x40457158,
                  0x40457474, 0x40457CF4, 0x40458058, 0x40458718, 0x40458AE8,
                  0x4045905C, 0x40459158, 0x40459900, 0x40459A4C, 0x4045A140,
                  0x4045A288, 0x4045AEAC, 0x4045B074, 0x4045BEC0, 0x4045C3EC,
                  0x4045C8B0, 0x4045C9E4, 0x4045D0A4, 0x4045D65C),
    0x43A832E0: (0x4043FD60, 0x40441558, 0x404431E8, 0x4049A444, 0x4049C8D0,
                 0x4049D76C, 0x4049E224, 0x404E5034, 0x404E5048),
    0x43A83420: (0x4043FDD4, 0x40441580, 0x404431EC, 0x404E5060),
    0xA032F4F90: (0x4048D890, 0x4048DF58, 0x4048DFC4, 0x4048E010, 0x4048E1E8,
                  0x4048E234, 0x4048EA30, 0x4048EBC8, 0x4048F198, 0x40490020,
                  0x404901D0, 0x40490D88, 0x40490F5C, 0x40491634, 0x40491CDC,
                  0x4049201C, 0x404921D4, 0x404923D8, 0x40492624, 0x40492800,
                  0x40492984, 0x40492B70, 0x40492EB0, 0x404937D4, 0x40494340,
                  0x40494744, 0x40494950),
}


def build_menu_plan(source, layout=Layout()):
    source = _check_source(source)
    L=layout; COUNT=L.slots; TOTAL=31+COUNT
    MENU_CODE, MENU_DATA = L.imenu_code, L.address('imenu')
    LABELS, SELECTORS, CELLS = MENU_DATA, MENU_DATA+0x200, MENU_DATA+0x400
    CELLS_ALT=MENU_DATA+0x1000
    PAIR0, PAIR1 = MENU_DATA+0x2000, MENU_DATA+0x2400
    CLICK_PAIR0, CLICK_PAIR1 = MENU_DATA+0x2800, MENU_DATA+0x2C00
    TEMP, STATE, LABEL_IDS, GUARD = (MENU_DATA+x for x in (0x3000,0x3180,0x3200,0x3500))
    C10_LABEL_IDS=tuple(L.label_id(s,v) for s in range(10,COUNT+1) for v in range(5))
    CONSTRUCT, ORIGINAL_CONSTRUCT = MENU_CODE, MENU_CODE+0x380
    RECORD_ENTRY, VALID_ENTRY, GET_ENTRY = (MENU_CODE+x for x in (0x400,0x500,0x600))
    ROW_ENTRY, ROW_SELECTOR_ENTRY = MENU_CODE+0x800, MENU_CODE+0x900
    CONSTRUCT_ALT, ORIGINAL_ALT = MENU_CODE+0xA00, MENU_CODE+0xD80
    ENCODE = L.core_code+0x4400
    patches = []
    code, data = bytearray(0x8000), bytearray(0x8000)

    def original(address, size):
        offset = address - BASE
        if not 0 <= offset <= len(source) - size:
            raise ValueError('Menu source range outside pinned MAIN')
        return source[offset:offset + size]

    def change(address, replacement, name):
        patches.append(dict(address=address, expected=original(address, len(replacement)),
                            replacement=replacement, name=name))

    def instruction(address, text, name):
        raw = _assemble(text, address)
        if len(raw) != 4:
            raise ValueError('Menu patch must replace one original instruction')
        change(address, raw, name)

    def emit(address, text, capacity):
        raw = _assemble(text, address)
        if len(raw) > capacity:
            raise ValueError('Menu guest function exceeds its owned capacity')
        code[address - MENU_CODE:address - MENU_CODE + len(raw)] = raw

    def relocate(start, old, new):
        first = int.from_bytes(original(start, 4), 'little')
        if first & 0xFFE00000 != 0xD2800000:
            raise ValueError('Expected an audited 64-bit MOVZ literal')
        register = first & 31
        value = (first >> 5) & 0xFFFF
        positions = [(start, 0)]
        # The audited keyboard-table pair is interleaved over 72 bytes.
        for at in range(start + 4, start + 96, 4):
            word = int.from_bytes(original(at, 4), 'little')
            if word & 0xFF80001F == 0xF2800000 | register:
                shift = ((word >> 21) & 3) * 16
                value = value & ~(0xFFFF << shift) | ((word >> 5) & 0xFFFF) << shift
                positions.append((at, shift))
            if value == old and {shift for _, shift in positions} >= {0, 16, 32}:
                break
        else:
            raise ValueError(f'Unconfirmed native menu literal at {start:#x}')
        for at, shift in positions:
            op = 'movz' if shift == 0 else 'movk'
            suffix = '' if shift == 0 else f', lsl #{shift}'
            instruction(at, f'{op} x{register}, #{new >> shift & 0xffff}{suffix}',
                        'Relocate bounded original GUI array')

    for old, new in ((0xA03417898, LABELS), (0xA032F8534, SELECTORS), (0xA032E5EB8, CELLS),
                     (0x43A832E0, CLICK_PAIR0), (0x43A83420, CLICK_PAIR1),
                     (0xA032F4F90, CELLS_ALT)):
        for start in LITERALS[old]:
            relocate(start, old, new)
    # Owned cells use the original initialized 24-byte template. The native
    # constructor may still refresh its original 40 cells without overrunning.
    template = original(0x43A83948, 24)
    data[CELLS - MENU_DATA:CELLS - MENU_DATA + TOTAL * 24] = template * TOTAL
    data[CELLS_ALT - MENU_DATA:CELLS_ALT - MENU_DATA + TOTAL * 24] = template * TOTAL
    data[PAIR0 - MENU_DATA:PAIR0 - MENU_DATA + 320] = original(0x42AD8AC8, 320)
    data[PAIR1 - MENU_DATA:PAIR1 - MENU_DATA + 320] = original(0x42AD8C08, 320)
    for slot in range(10,COUNT+1):
        offset=(31+slot-1)*8
        for bank,table in ((0,PAIR0),(1,PAIR1)):
            struct.pack_into('<II',data,table-MENU_DATA+offset,L.selector(slot,bank),L.code(slot))
    for address, old, bank in ((CLICK_PAIR0,0x43A832E0,0),(CLICK_PAIR1,0x43A83420,1)):
        data[address-MENU_DATA:address-MENU_DATA+320]=original(old,320)
        for slot in range(10,COUNT+1):
            struct.pack_into('<II',data,address-MENU_DATA+(31+slot-1)*8,L.selector(slot,bank),L.code(slot))
    instruction(0x404E508C,f'mov w25,#{TOTAL}','Bound keyboard search to owned table')
    instruction(0x404E50CC,f'cmp w8,#{COUNT-1}','Configured compact custom list')
    instruction(0x404E50E0,f'mov w1,#{COUNT}','Configured compact custom search')
    instruction(0x404E5224,f'cmp w9,#{COUNT-1}','Configured compact custom destination')
    for start, old, new, count_at in ((0x403F9618, 0x42AD8AC8, PAIR0, 0x403F9628),
                                      (0x403F9660, 0x42AD8C08, PAIR1, 0x403F9670)):
        relocate(start, old, new)
        instruction(count_at, f'mov w1,#{TOTAL}', 'Include C10 in original selector/code lookup')
    struct.pack_into('<'+'H'*len(C10_LABEL_IDS), data, LABEL_IDS-MENU_DATA,*C10_LABEL_IDS)
    data[GUARD - MENU_DATA:GUARD - MENU_DATA + 16] = b'C10-MENU-GUARD!!'
    c = _constant

    # The original constructor remains nine items. Its resource tables are
    # deliberately NOT widened. This wrapper appends only an actual C10 record.
    instruction(0x4045341C, f'b #{CONSTRUCT}', 'Append C10 after original GUI construction')
    emit(ORIGINAL_CONSTRUCT,
         f'.word {int.from_bytes(original(0x4045341C, 4), "little")}; b #0x40453420;', 0x20)
    construct_text = f'''
        stp x19,x20,[sp,#-80]!; stp x21,x22,[sp,#16]; stp x23,x24,[sp,#32];
        stp x25,x26,[sp,#48]; stp x27,x30,[sp,#64];
        mov w19,w0; mov x20,x1; cbz x20,construct_bad;
        bl #{ORIGINAL_CONSTRUCT}; mov w21,w0; ldr w22,[x20];
        cmp w22,#9; b.hi construct_bad;
        tst w19,#1; cset w23,eq; mov w27,#10;
        {c('x25',STATE)} ldr w26,[x25,#4]; str w23,[x25];
        construct_loop: mov w0,w27; mov w1,w23; bl #{ENCODE}; mov w24,w0;
        bl #{RECORD_ENTRY}; cbz x0,construct_next;
        {c('x1',TEMP)} mov w0,w24; bl #0x403f9724; cbz w0,construct_next;
        mov w0,w24; bl #0x403f9e64; mov w19,w0;
        {c('x8',TEMP)} ldrh w0,[x8,#0x110]; cmp w0,#0x800; b.hi construct_alt;
        tst w19,#1; mov w19,#0; mov w8,#3; csel w19,w19,w8,ne; b construct_type;
        construct_alt: tst w19,#1; mov w19,#2; mov w8,#4; csel w19,w19,w8,ne;
        construct_type: bl #0x403f9880; cmp w0,#0x20; mov w8,#1; csel w19,w8,w19,eq;
        sub w9,w27,#10; mov w8,#5; madd w9,w9,w8,w19; mov w8,#0x7e00; add w9,w9,w8;
        {c('x8',LABELS)} add w10,w22,#31; ldrh w11,[x8,x10,lsl #1];
        cmp w9,w11; cset w11,ne; orr w21,w21,w11; strh w9,[x8,x10,lsl #1];
        {c('x8',SELECTORS)} ldr w9,[x8,x22,lsl #2]; cmp w9,w24; cset w9,ne;
        orr w21,w21,w9; str w24,[x8,x22,lsl #2]; add w22,w22,#1;
        construct_next: add w27,w27,#1; cmp w27,#{COUNT}; b.ls construct_loop;
        construct_finish: cmp w22,w26; cset w8,ne; orr w21,w21,w8;
        str w22,[x20]; str w22,[x25,#4]; dmb ish; mov w0,w21; b construct_done;
        construct_bad: mov w0,#0;
        construct_done: ldp x27,x30,[sp,#64]; ldp x25,x26,[sp,#48]; ldp x23,x24,[sp,#32];
        ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#80; ret;
    '''
    emit(CONSTRUCT, construct_text, 0x380)
    # The second photo-page constructor takes only x0=count_pointer and has
    # separate cell metadata. Execute that original body, then the same append.
    instruction(0x4048EDD4, f'b #{CONSTRUCT_ALT}', 'Append C10 in the second native photo page')
    emit(ORIGINAL_ALT,
         f'.word {int.from_bytes(original(0x4048EDD4, 4), "little")}; b #0x4048edd8;', 0x20)
    emit(CONSTRUCT_ALT, construct_text.replace('mov w19,w0; mov x20,x1;',
                                               'mov w19,#1; mov x20,x0;')
         .replace(f'bl #{ORIGINAL_CONSTRUCT}', f'bl #{ORIGINAL_ALT}'), 0x380)

    emit(RECORD_ENTRY,decode_asm(L,'w0','record_custom','record_bad','record')+f'''
        record_custom: {record_asm(L,'x0')} ldr w8,[x0]; cmp w8,#1; b.ne record_bad; ret;
        record_bad: mov x0,#0; ret;''',0x100)
    instruction(0x403F95D8,f'b #{VALID_ENTRY}','Owned custom validity with original fallback')
    emit(VALID_ENTRY,decode_asm(L,'w0','custom','old','valid')+f'''
        custom: str x30,[sp,#-16]!; bl #{RECORD_ENTRY}; cmp x0,#0; cset w0,ne;
        ldr x30,[sp],#16; ret; old: b #0x410db078;''',0x100)
    instruction(0x403F9724,f'b #{GET_ENTRY}','Native conversion of owned custom parameters')
    emit(GET_ENTRY,decode_asm(L,'w0','custom','old','get')+f'''
        custom: cbz x1,bad; stp x19,x30,[sp,#-16]!; mov x19,x1;
        bl #{RECORD_ENTRY}; cbz x0,empty; add x1,x0,#0x10; mov x0,x19;
        mov w2,#0; bl #0x403f97b8; mov w0,#1; b done;
        empty: mov w0,#0; done: ldp x19,x30,[sp],#16; ret;
        bad: mov w0,#0; ret;
        old: .word {int.from_bytes(original(0x403F9724,4),'little')}; b #0x403f9728;''',0x200)
    instruction(0x404A7040, f'b #{ROW_ENTRY}', 'C10 code resolves to its actual compact GUI row')
    emit(ROW_ENTRY, f'''
        sub w8,w0,#40; cmp w8,#{COUNT-10}; b.hi row_original;
        tst w1,#1; mov w9,#1; mov w10,#2; csel w9,w9,w10,ne;
        lsl w9,w9,#12; add w8,w8,#9; add w8,w8,w9; {c('x9',STATE)} ldr w10,[x9,#4];
        cmp w10,#{COUNT}; b.hi row_bad; {c('x9',SELECTORS)} mov x11,#0;
        row_loop: cmp w11,w10; b.hs row_bad; ldr w12,[x9,x11,lsl #2];
        cmp w12,w8; b.eq row_found; add x11,x11,#1; b row_loop;
        row_found: add w0,w11,#31; ret;
        row_bad: mov w0,#0; ret;
        row_original: .word {int.from_bytes(original(0x404A7040,4), 'little')}; b #0x404a7044;
    ''', 0x100)
    emit(ROW_SELECTOR_ENTRY, f'''
        sub w8,w0,#31; {c('x9',STATE)} ldr w10,[x9,#4];
        cmp w8,w10; b.hs rs_bad; cmp w8,#{COUNT}; b.hs rs_bad;
        {c('x9',SELECTORS)} ldr w0,[x9,x8,lsl #2]; ret;
        rs_bad: mov w0,#0; ret;
    ''', 0x100)
    spans = sorted((p['address'], p['address'] + len(p['expected'])) for p in patches)
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('Overlapping menu patches')
    return dict(schema=3, source_sha256=MAIN_SHA256, simulation_only=True,
                patches=patches, regions=[dict(address=MENU_CODE, data=bytes(code), permissions='rx'),
                                         dict(address=MENU_DATA, data=bytes(data), permissions='rw')],
                entries=dict(construct=CONSTRUCT, construct_photo=CONSTRUCT_ALT,
                             row_to_selector=ROW_SELECTOR_ENTRY),
                arrays=dict(labels=LABELS, selectors=SELECTORS, cells=CELLS, cells_photo=CELLS_ALT, state=STATE,
                            keyboard_mode0=CLICK_PAIR0, keyboard_mode1=CLICK_PAIR1),
                required_label_ids=list(C10_LABEL_IDS),
                limitations=['Requires native per-slot resource resolver before GUI pixel acceptance',
                             'Explicit simulation memory; not a physical camera patch'])
