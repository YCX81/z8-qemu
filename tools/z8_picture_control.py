# SPDX-License-Identifier: GPL-2.0-or-later
"""Simulation-only C3.11 Picture Control adapter and original A64 fixture.

No update container is produced. Native bytes are obtained from the user's
identity-checked MAIN. The private mappings are explicit simulation inputs,
NOT evidence of free or executable memory on a physical Z8.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

from z8_c10_memory import DATA_BASE

BASE = 0x40160000
MAIN_BYTES = 81807709
MAIN_SHA256 = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
CODE_BASE, DATA_BYTES = 0x45000000, 0x32000
GROUPS, ITEMS = DATA_BASE, DATA_BASE + 0x2000
RECORDS, RECORD_BYTES, LOAD_BUFFER = DATA_BASE + 0x20000, 0x6000, DATA_BASE + 0x26000
PARAMS, INPUT, PARSED, SCRATCH = DATA_BASE + 0x2C000, DATA_BASE + 0x2D000, DATA_BASE + 0x2E000, DATA_BASE + 0x30000
PRIMARY = DATA_BASE + 0x2E200
LIST0, LIST1, CODES = DATA_BASE + 0x31000, DATA_BASE + 0x31200, DATA_BASE + 0x31400
C10_CACHE0, C10_CACHE1 = DATA_BASE + 0x31B00, DATA_BASE + 0x31D48
CACHE_UPDATE, CACHE_GET, CACHE_GENERATION, CACHE_INVALIDATE = (CODE_BASE + value for value in (0x2200, 0x2400, 0x2600, 0x2700))
IMPORT_ENTRY, SELECT_ENTRY, LIST_ENTRY = CODE_BASE, CODE_BASE + 0x400, CODE_BASE + 0x600
SELECT_FULL_ENTRY = CODE_BASE + 0x2A00
SYNC_CURRENT_ENTRY, DECODE_CURRENT_ENTRY = CODE_BASE + 0x2E00, CODE_BASE + 0x3000
FILE_ENTRY, SAVE_ENTRY, LOAD_ENTRY = CODE_BASE + 0x800, CODE_BASE + 0x1000, CODE_BASE + 0x1080
VALIDATE_ENTRY = CODE_BASE + 0xD00
SAVE_CALLBACK, LOAD_CALLBACK = CODE_BASE + 0x1800, CODE_BASE + 0x1840
HEADER = (b'Z8C10SIM' + struct.pack('<II', 2, RECORD_BYTES) + bytes.fromhex(MAIN_SHA256)
          + struct.pack('<4I', 0, 0, 0, 1))
VENDOR = Path(__file__).resolve().parents[1] / '.local/analysis/c10-20261011/picture-control/vendor'


def _dependencies():
    import sys
    if VENDOR.is_dir() and str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))


def make_np3(name, seed=0):
    """Authored basic-version NCP/NP3 fixture; not a Flexible Color export."""
    if not isinstance(name, str) or not 1 <= len(name) <= 19 or any(not 32 <= ord(c) <= 126 for c in name):
        raise ValueError('NP3 fixture name requires 1..19 printable ASCII characters')
    if type(seed) is not int or not 0 <= seed <= 255:
        raise ValueError('Fixture seed must be a byte')
    fields = {0x100: b'0300', 0x200: name.encode().ljust(20, b'\0'), 0x300: b'\0\1', 0x400: b'\0\0'}
    modes = {5: 1, **{i: 4 for i in range(6, 12)}, 12: 0, 13: 0, 14: 4,
             **{i: 1 for i in range(15, 21)}, 21: 10, 22: 4}
    for i, mode in modes.items():
        fields[i * 0x100] = bytes((255 if i == 13 else 128, mode))
    # Type2 is a distinct auxiliary block. Its byte order is transformed by
    # the ORIGINAL loader, rather than manufacturing the parsed guest output.
    fields[2] = bytes((seed + i) & 255 for i in range(0x242))
    return b'NCP\0' + b''.join(struct.pack('>II', key, len(value)) + value for key, value in fields.items())


def make_flexible_np3(name, seed=19, *, auxiliary_enabled=True):
    """Authored 0310 Flexible Color fixture from native handlers, not a cloud export.

    410aea68 parses tags 1900..2000; original 410af260 constructs the 44-byte
    extra object. Controlled nonzero values exercise its native byte order.
    """
    blob = make_np3(name, seed)
    if type(auxiliary_enabled) is not bool:
        raise ValueError('Auxiliary enabled must be Boolean')
    blob = blob.replace(struct.pack('>II', 0x100, 4) + b'0300', struct.pack('>II', 0x100, 4) + b'0310')
    blob = blob.replace(struct.pack('>II', 0x300, 2) + b'\0\1', struct.pack('>II', 0x300, 2) + b'\0\x20')
    values = ([1] * 5 if auxiliary_enabled else [128 + seed % 16] * 5) + [144 + seed % 16]
    fields = {0x1900 + index * 0x100: bytes((value, 1)) for index, value in enumerate(values)}
    fields[0x1F00] = bytes(96 + seed % 16 + index for index in range(24)) + b'\1\1\1\0'
    grades = b''.join(struct.pack('>HBB', 0x8000 + seed + index * 17, 128 + seed % 16 + index, 112 + index)
                      for index in range(3))
    fields[0x2000] = grades + b'\1\1\1\0' + bytes((128 + seed % 16, 1, 112 + seed % 16, 1))
    return blob + b''.join(struct.pack('>II', key, len(value)) + value for key, value in fields.items())


def _check_source(source):
    source = bytes(source)
    if len(source) != MAIN_BYTES or hashlib.sha256(source).hexdigest() != MAIN_SHA256:
        raise ValueError('Requires the pinned original Z8 C3.11 MAIN')
    return source


def _assemble(text, address):
    _dependencies()
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN
    return bytes(Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN).asm(text, addr=address)[0])


def _constant(reg, value):
    return ';'.join([f'movz {reg}, #{value & 65535}'] +
                    [f'movk {reg}, #{(value >> shift) & 65535}, lsl #{shift}' for shift in (16, 32, 48)]) + ';'


def _record(slot, mode=0):
    if type(slot) is not int or not 1 <= slot <= 10 or mode not in (0, 1):
        raise ValueError('Requires slot 1..10 and mode 0/1')
    return RECORDS + 64 + (mode * 10 + slot - 1) * 0x400


def _keys(slot, mode):
    _record(slot, mode)
    if slot == 10:
        return 0x01007C00 + mode * 2, 0x7C01 + mode * 2
    return (0x01000D08 if mode == 0 else 0x01001408) + slot - 1, 0xF00 + mode * 9 + slot - 1


def _extra_key(slot, mode):
    _record(slot, mode)
    if slot == 10:
        return 0x01007C04 + mode
    return (0x01000D14 if mode == 0 else 0x01001414) + slot - 1


@dataclass(frozen=True)
class ParsedControl:
    main: bytes
    auxiliary: bytes
    extra: bytes = b''


def build_plan(source):
    """Return guarded simulation patches, owned regions and fixed guest ABI.

    Entries take import(x0=NP3 pointer,x1=length,w2=slot,w3=mode),
    select(w0=slot,w1=mode)->record pointer or 0, and list(w0=mode)->list
    descriptor. Save/load callbacks receive (x0=buffer,x1=0x6000) and return
    transferred byte count. Root integration must supply real guest media I/O.
    select_full(w0=slot,w1=source_bank,w2=target_bank) additionally supports
    photo custom parameters as movie current state. Schema2 persists source
    banks at header+56/+60, separate from target selected slots at +48/+52.
    """
    source = _check_source(source)
    data = bytearray(DATA_BYTES)
    code = bytearray(0x4000)
    patches = []

    def original(address, size):
        return source[address - BASE:address - BASE + size]

    def change(address, replacement, name):
        old = original(address, len(replacement))
        if len(old) != len(replacement):
            raise ValueError('Patch outside pinned source')
        patches.append(dict(address=address, expected=old, replacement=replacement, name=name))

    def initialized(address, size):
        offset = 0x4B13000 + address - 0xA02808000
        if not 0x4B13000 <= offset <= 0x4DC0000 - size:
            raise ValueError('Outside pinned initialized-data copy')
        return source[offset:offset + size]

    def instruction(address, text, name):
        raw = _assemble(text, address)
        if len(raw) != 4:
            raise ValueError('Expected one A64 instruction')
        change(address, raw, name)

    def relocate(addresses, reg, target, label):
        for shift, address in zip((0, 16, 32, 48), addresses):
            op = f'movz {reg}, #{target & 65535}' if not shift else f'movk {reg}, #{(target >> shift) & 65535}, lsl #{shift}'
            instruction(address, op, label)

    def emit(address, text, capacity):
        raw = _assemble(text, address)
        if len(raw) > capacity:
            raise ValueError('Owned A64 function capacity exceeded')
        offset = address - CODE_BASE
        code[offset:offset + len(raw)] = raw

    groups = original(0x440244C8, 124 * 48)
    data[:len(groups)] = groups
    count = max(sum(struct.unpack_from('<HH', groups, i * 48 + 24)) for i in range(124))
    item_bytes = original(0x4400A5C0, count * 24)
    if 0x2000 + len(item_bytes) + 144 >= 0x20000:
        raise ValueError('Native descriptor table exceeds owned region')
    data[0x2000:0x2000 + len(item_bytes)] = item_bytes
    # An explicitly new simulation group keeps all existing setting IDs.
    struct.pack_into('<I', data, 124 * 48, 1)
    struct.pack_into('<HH', data, 124 * 48 + 24, count, 6)
    for mode in (0, 1):
        for slot in range(1, 11):
            for part, key in enumerate((*_keys(slot, mode), _extra_key(slot, mode))):
                group, index = (key >> 8) & 255, key & 255
                first = struct.unpack_from('<H', data, group * 48 + 24)[0]
                record = _record(slot, mode) + (0x10, 0x124, 0x370)[part]
                size = (0x114, 0x242, 0x2C)[part]
                descriptor = 0x2000 + (first + index) * 24
                default_source = (struct.unpack_from('<Q', data, descriptor + 16)[0]
                                  if slot < 10 else DATA_BASE + 0x31800)
                struct.pack_into('<IIQQ', data, 0x2000 + (first + index) * 24,
                                 10, size, record, default_source)
    data[RECORDS - DATA_BASE:RECORDS - DATA_BASE + len(HEADER)] = HEADER
    struct.pack_into('<10I', data, CODES - DATA_BASE, *range(28, 37), 40)
    for address, old_base, descriptor in ((LIST0, 0xA02983D04, 0xA02983CB0),
                                         (LIST1, 0xA02983DA4, 0xA02983CC8)):
        # Scatter publishes count=40 before native list rebuilding. Preserve
        # all 31 builtins and 9 original custom codes during that interval.
        initial_count = struct.unpack('<I', initialized(descriptor + 8, 4))[0]
        if initial_count != 40:
            raise ValueError('Unexpected pinned native menu initial count')
        data[address - DATA_BASE:address - DATA_BASE + initial_count * 4] = initialized(old_base, initial_count * 4)
        patches.append(dict(address=descriptor + 16, expected=initialized(descriptor + 16, 8),
                            replacement=struct.pack('<Q', address), name='Publish owned native menu list',
                            offset_space='initialized_guest_ram',
                            source_offset=0x4B13000 + descriptor + 16 - 0xA02808000))
    for addr in (LIST0, LIST1):
        # Owned extra room and canary. Original nine entries are not adjacent.
        data[addr - DATA_BASE + 0x180:addr - DATA_BASE + 0x190] = b'C10-LIST-GUARD!!'

    for at in (0x41015B34, 0x41015EC4, 0x4101561C):
        instruction(at, 'cmp w8, #0x7c', 'Allow explicitly described simulation group 0x7c')
    relocate((0x41015B40, 0x41015B44, 0x41015B4C, 0x41015B50), 'x9', GROUPS, 'Native getter group table')
    relocate((0x41015B80, 0x41015B84, 0x41015B88, 0x41015B90), 'x9', ITEMS, 'Native getter item table')
    relocate((0x41015ED0, 0x41015ED4, 0x41015EDC, 0x41015EE0), 'x9', GROUPS, 'Native setter group table')
    relocate((0x41015F10, 0x41015F14, 0x41015F18, 0x41015F20), 'x9', ITEMS, 'Native setter item table')
    relocate((0x41015628, 0x4101562C, 0x41015634, 0x41015638), 'x9', GROUPS, 'Native block getter group table')
    relocate((0x41015668, 0x4101566C, 0x41015670, 0x41015678), 'x9', ITEMS, 'Native block getter item table')
    # Keep original list instructions, changing capacity and owned references.
    for at in (0x410AAD14, 0x410AAD80):
        instruction(at, 'cmp x9, #10', 'Ten independent selectable records')
    relocate((0x410AACCC, 0x410AACD4, 0x410AACDC, 0x410AACEC), 'x10', CODES, 'Mode1 ten-entry codes')
    relocate((0x410AAD38, 0x410AAD40, 0x410AAD48, 0x410AAD58), 'x10', CODES, 'Mode0 ten-entry codes')
    relocate((0x410AACD0, 0x410AACD8, 0x410AACE0, 0x410AACF0), 'x11', LIST1, 'Mode1 owned list')
    relocate((0x410AAD3C, 0x410AAD44, 0x410AAD4C, 0x410AAD5C), 'x11', LIST0, 'Mode0 owned list')
    # The original list final notification descriptor remains native; the root
    # must integrate its observable menu binding rather than draw a fake list.
    for at in (0x410ADCA8, 0x410AC834):
        instruction(at, f'bl #{FILE_ENTRY}', 'Explicit simulation NP3 input service')
    instruction(0x410DAC58, f'bl #{CODE_BASE+0xA00}', 'Borrow original parsed file object for native conversion')
    for at, target in ((0x410AC7EC, CODE_BASE + 0x900),
                       (0x410AC7FC, CODE_BASE + 0x940),
                       (0x410AC924, CODE_BASE + 0x980)):
        instruction(at, f'bl #{target}', 'Owned virtual NP3 file-buffer service')

    c = _constant
    # Import uses ORIGINAL parser validation. This routine is newly authored
    # guest A64; host code does not manufacture the accepted parsed object.
    emit(IMPORT_ENTRY, f'''
        stp x19,x20,[sp,#-48]!; stp x21,x22,[sp,#16]; str x30,[sp,#32];
        mov x19,x0; mov x20,x1; mov w21,w2; mov w22,w3;
        sub w8,w21,#1; cmp w8,#9; b.hi import_bad; cmp w22,#1; b.hi import_bad;
        cmp x20,#4; b.lo import_bad; cmp x20,#1,lsl #12; b.hi import_bad;
        {c('x8',PARAMS)} stp x19,x20,[x8];
        {c('x0',0xA047180D0)} mov x1,#0; mov x2,#0x100; bl #0x4127ab40;
        {c('x0',0xA0471074A)} strb wzr,[x0];
        mov x0,#0; {c('x1',PARSED)} {c('x2',SCRATCH)} bl #0x410adc64;
        cbz w0,import_bad; mov w0,#200; {c('x1',PRIMARY)} bl #0x410dac30; cbz w0,import_bad;
        mov w0,#200; bl #0x410ac6d0; cbz x0,import_bad;
        mov x19,x0;
        mov w0,w21; mov w1,w22; bl #{CODE_BASE+0xC00}; cbz x0,import_bad;
        mov x20,x0;
        ldp w0,w8,[x20,#4]; {c('x2',PRIMARY)} mov w1,#0x114; bl #0x41015ec0; cbz w0,import_bad;
        ldr w0,[x20,#8]; mov w1,#0x242; mov x2,x19; bl #0x41015ec0; cbz w0,import_bad;
        mov w8,#50; madd w0,w22,w8,w21; add w0,w0,#30; add x1,sp,#40; bl #0x410a9e90; cbz w0,import_bad;
        ldr w0,[sp,#40]; mov w1,#0x2c; {c('x2',PARSED+0x12C)} bl #0x41015ec0; cbz w0,import_bad;
        {c('x0',0xA047027C0)} bl #0x40d2c674;
        mov w8,#50; madd w0,w22,w8,w21; add w0,w0,#30; mov x1,x19; bl #0x410a93e0;
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w8,#1; str w8,[x20]; mov w0,#1; b import_done;
        import_bad: mov w0,#0;
        import_done: ldr x30,[sp,#32]; ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#48; ret;
    ''', 0x400)
    emit(SELECT_ENTRY, f'mov w2,w1; b #{SELECT_FULL_ENTRY};', 0x200)
    emit(SELECT_FULL_ENTRY, f'''
        stp x19,x20,[sp,#-64]!; stp x21,x22,[sp,#16]; stp x23,x30,[sp,#32];
        mov w20,w0; mov w21,w1; mov w22,w2;
        cmp w22,#1; b.hi select_bad; cbnz w22,select_source_ok; cbnz w21,select_bad;
        select_source_ok:
        bl #{CODE_BASE+0xC00}; cbz x0,select_done;
        mov x19,x0; ldr w8,[x19]; cbz w8,select_bad;
        ldr w0,[x19,#4]; add x1,sp,#48; bl #0x41015b30; cbz w0,select_bad;
        ldr x8,[sp,#48]; add x9,x19,#0x10; cmp x8,x9; b.ne select_bad;
        ldr w0,[x19,#8]; add x1,sp,#48; bl #0x41015b30; cbz w0,select_bad;
        ldr x8,[sp,#48]; add x9,x19,#0x124; cmp x8,x9; b.ne select_bad;
        // Follow the original selector mutex and shared current-object builder.
        // The latter fills baseline pointer, conditional aux cache, generation,
        // and class-specific tail; copying only 0x114 bytes is not sufficient.
        {c('x0',0xA047027C0)} bl #0x40d2c674;
        {c('x8',0xA02983CF8)} add x8,x8,x22,lsl #2;
        mov w9,#50; madd w9,w21,w9,w20; add w9,w9,#30; str w9,[x8];
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w9,#50; madd w0,w21,w9,w20; add w0,w0,#30; mov w1,w22; bl #0x410a7c44;
        {c('x8',RECORDS+48)} str w20,[x8,x22,lsl #2];
        {c('x8',RECORDS+56)} str w21,[x8,x22,lsl #2];
        mov x0,x19; b select_done; select_bad: mov x0,#0;
        select_done: ldp x23,x30,[sp,#32]; ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#64; ret;
    ''', 0x400)
    emit(LIST_ENTRY, f'''
        stp x19,x30,[sp,#-32]!; cmp w0,#1; b.hi list_bad; mov w19,w0;
        {c('x8',RECORDS+64)} mov x9,#0x2800; madd x8,x19,x9,x8;
        mov x9,#0; list_scan: ldr w10,[x8]; cmp w10,#1; cset w10,eq;
        add x11,sp,#16; strb w10,[x11,x9]; add x8,x8,#0x400;
        add x9,x9,#1; cmp x9,#10; b.ne list_scan;
        add x0,sp,#16; mov w1,w19; bl #0x410aac98;
        {c('x0',LIST0)} mov x9,#0x200; madd x0,x19,x9,x0; b list_done;
        list_bad: mov x0,#0; list_done: ldp x19,x30,[sp],#32; ret;
    ''', 0x200)
    emit(FILE_ENTRY, f'''
        {c('x8',PARAMS)} ldp x9,x10,[x8]; cbz x9,file_bad; cmp x10,#1,lsl #12; b.hi file_bad;
        str x9,[x1]; str w10,[x1,#8]; str wzr,[x1,#12]; mov w0,#1; ret;
        file_bad: mov w0,#0; ret;
    ''', 0x100)
    emit(CODE_BASE + 0x900, f'''
        {c('x8',PARAMS)} ldp x9,x10,[x8]; cmp x9,#0; cset w9,ne;
        cmp x10,#4; cset w10,hs; and w0,w9,w10; ret;
    ''', 0x40)
    emit(CODE_BASE + 0x940, f'''
        cmp x0,#1,lsl #12; b.hi alloc_bad; {c('x0',DATA_BASE+0x2F000)} ret;
        alloc_bad: mov x0,#0; ret;
    ''', 0x40)
    emit(CODE_BASE + 0x980, 'mov x0,#0; ret;', 0x40)
    emit(CODE_BASE + 0xA00, f'''
        cmp w0,#200; b.ne parsed_bad;
        str x30,[sp,#-16]!; mov x0,x1; {c('x1',PARSED)} mov x2,#0x158;
        bl #0x4127a900; mov w0,#1; ldr x30,[sp],#16; ret;
        parsed_bad: mov w0,#0; ret;
    ''', 0x100)
    # Shared checked record address; its key fields are initialized in the
    # owned data image, never supplied by an untrusted NP3.
    emit(CODE_BASE + 0xC00, f'''
        sub w8,w0,#1; cmp w8,#9; b.hi record_bad; cmp w1,#1; b.hi record_bad;
        mov w9,#10; madd w8,w1,w9,w8; {c('x0',RECORDS+64)}
        add x0,x0,x8,lsl #10; ret; record_bad: mov x0,#0; ret;
    ''', 0x100)
    for mode in (0, 1):
        for slot in range(1, 11):
            struct.pack_into('<II', data, _record(slot, mode) - DATA_BASE + 4, *_keys(slot, mode))
    expected_keys = DATA_BASE + 0x31500
    for mode in (0, 1):
        for slot in range(1, 11):
            struct.pack_into('<II', data, expected_keys - DATA_BASE + (mode * 10 + slot - 1) * 8, *_keys(slot, mode))
    expected_header = DATA_BASE + 0x31600
    data[expected_header - DATA_BASE:expected_header - DATA_BASE + 48] = HEADER[:48]
    emit(VALIDATE_ENTRY, f'''
        {c('x8',expected_header)} mov x9,#0;
        validate_header: ldr x10,[x0,x9]; ldr x11,[x8,x9]; cmp x10,x11; b.ne validate_bad;
        add x9,x9,#8; cmp x9,#48; b.ne validate_header;
        ldp w8,w9,[x0,#48]; cmp w8,#10; b.hi validate_bad; cmp w9,#10; b.hi validate_bad;
        ldp w12,w13,[x0,#56]; cbnz w12,validate_bad; cmp w13,#1; b.hi validate_bad;
        add x8,x0,#64; {c('x9',expected_keys)} mov w10,#20;
        validate_record: ldr w11,[x8]; cmp w11,#1; b.hi validate_bad;
        ldr x11,[x8,#4]; ldr x12,[x9],#8; cmp x11,x12; b.ne validate_bad;
        add x8,x8,#0x400; subs w10,w10,#1; b.ne validate_record;
        ldp w8,w9,[x0,#48]; cbz w8,validate_second;
        sub w8,w8,#1; add x10,x0,#64; add x10,x10,x8,lsl #10;
        ldr w11,[x10]; cmp w11,#1; b.ne validate_bad;
        validate_second: cbz w9,validate_good;
        ldr w12,[x0,#60]; mov w13,#10; madd w9,w12,w13,w9; sub w9,w9,#1;
        add x10,x0,#64; add x10,x10,x9,lsl #10;
        ldr w11,[x10]; cmp w11,#1; b.ne validate_bad;
        validate_good:
        mov w0,#1; ret; validate_bad: mov w0,#0; ret;
    ''', 0x300)
    emit(SAVE_ENTRY, f'''
        str x30,[sp,#-16]!; bl #{SYNC_CURRENT_ENTRY}; cbz w0,save_bad;
        {c('x0',RECORDS)} mov x1,#0x6000; bl #{SAVE_CALLBACK};
        mov x8,#0x6000; cmp x0,x8; cset w0,eq; b save_done;
        save_bad: mov w0,#0; save_done: ldr x30,[sp],#16; ret;
    ''', 0x80)
    emit(LOAD_ENTRY, f'''
        str x30,[sp,#-16]!; {c('x0',LOAD_BUFFER)} mov x1,#0x6000; bl #{LOAD_CALLBACK};
        mov x8,#0x6000; cmp x0,x8; b.ne load_bad;
        {c('x0',LOAD_BUFFER)} bl #{VALIDATE_ENTRY}; cbz w0,load_bad;
        {c('x0',RECORDS)} {c('x1',LOAD_BUFFER)} mov x2,#0x6000; bl #0x4127a900;
        bl #{CACHE_INVALIDATE};
        {c('x8',RECORDS+48)} ldr w0,[x8]; cbz w0,load_mode1; ldr w1,[x8,#8]; mov w2,#0; bl #{SELECT_FULL_ENTRY}; cbz x0,load_bad;
        load_mode1: {c('x8',RECORDS+52)} ldr w0,[x8]; cbz w0,load_good; ldr w1,[x8,#8]; mov w2,#1; bl #{SELECT_FULL_ENTRY}; cbz x0,load_bad;
        load_good: mov w0,#1; b load_done;
        load_bad: mov w0,#0; load_done: ldr x30,[sp],#16; ret;
    ''', 0x400)
    for entry in (SAVE_CALLBACK, LOAD_CALLBACK):
        emit(entry, 'mov x0,#0; ret;', 0x40)
    # Preserve all original selector cases. Only two newly authored values
    # enter new descriptors; existing native code continues after the one
    # displaced, non-PC-relative instruction.
    main_map, aux_map, main_read = CODE_BASE + 0x1C00, CODE_BASE + 0x1D00, CODE_BASE + 0x1E00
    instruction(0x410A95E8, f'b #{main_map}', 'C10 main selector mapping with original fallback')
    emit(main_map, f'''
        cmp w0,#40; b.eq map_zero; cmp w0,#90; b.eq map_one;
        mov w8,wzr; b #0x410a95ec;
        map_zero: mov w9,#0; b map_new; map_one: mov w9,#1;
        map_new: cbz x1,map_bad; cbz x2,map_bad; cbz x3,map_bad;
        mov w8,#0x7c00; movk w8,#0x100,lsl #16; add w8,w8,w9,lsl #1;
        str w8,[x1]; mov w8,#40; str w8,[x2]; str w9,[x3]; mov w0,#1; ret;
        map_bad: mov w0,#0; ret;
    ''', 0x100)
    instruction(0x410AA9F4, f'b #{aux_map}', 'C10 auxiliary selector mapping with original fallback')
    emit(aux_map, '''
        cmp w0,#40; b.eq aux_zero; cmp w0,#90; b.eq aux_one;
        sub w8,w0,#0x51; b #0x410aa9f8;
        aux_zero: mov w8,#0x7c01; b aux_new; aux_one: mov w8,#0x7c03;
        aux_new: cbz x1,aux_bad; str w8,[x1]; mov w0,#1; ret;
        aux_bad: mov w0,#0; ret;
    ''', 0x100)
    instruction(0x410A7E70, f'b #{main_read}', 'C10 primary getter with original fallback')
    emit(main_read, f'''
        cmp w0,#40; b.eq read_zero; cmp w0,#90; b.eq read_one;
        .word {int.from_bytes(original(0x410A7E70,4), 'little')}; b #0x410a7e74;
        read_zero: mov w0,#0x7c00; b read_new; read_one: mov w0,#0x7c02;
        read_new: movk w0,#0x100,lsl #16; mov x2,x1; mov w1,#0x114; b #0x41015618;
    ''', 0x100)
    menu_map = CODE_BASE + 0x1F00
    instruction(0x410AABC0, f'b #{menu_map}', 'Unique C10 menu code, preserve builtin codes 37..39')
    emit(menu_map, '''
        cmp w0,#40; b.ne menu_original; cbz w1,menu_zero;
        cmp w1,#1; b.ne menu_original; mov w0,#90; ret;
        menu_zero: mov w0,#40; ret;
        menu_original: cmp w1,#4; b #0x410aabc4;
    ''', 0x100)
    for mode, native, selector in ((0, 0x410A7BCC, 40), (1, 0x410A7D84, 90)):
        entry = CODE_BASE + 0x2000 + mode * 0x100
        instruction(native, f'b #{entry}', 'C10-only native selection entry adapter')
        mirror = 'cmp w0,#40; b.eq select_photo_tenth;' if mode == 1 else ''
        mirror_entry = f'''
            select_photo_tenth: str x30,[sp,#-16]!; mov w0,#10; mov w1,#0; mov w2,#1;
            bl #{SELECT_FULL_ENTRY}; cmp x0,#0; cset w0,ne; ldr x30,[sp],#16; ret;
        ''' if mode == 1 else ''
        emit(entry, f'''
            {mirror}
            cmp w0,#{selector}; b.eq select_tenth;
            .word {int.from_bytes(original(native,4), 'little')}; b #{native+4};
            select_tenth: str x30,[sp,#-16]!; mov w0,#10; mov w1,#{mode}; bl #{SELECT_ENTRY};
            cmp x0,#0; cset w0,ne; ldr x30,[sp],#16; ret;
            {mirror_entry}
        ''', 0x100)
    instruction(0x410A7C84, 'cmp w8,#10', 'Native shared current builder accepts movie C10')
    instruction(0x410A7C8C, 'cmp w20,#41', 'Native shared current builder accepts photo C10')
    instruction(0x410A8560, 'cmp w19,#41', 'Native base-parameter lookup classifies photo C10 correctly')
    instruction(0x410A93E0, f'b #{CACHE_UPDATE}', 'Independent C10 auxiliary cache with original legacy fallback')
    emit(CACHE_UPDATE, f'''
        cmp w0,#40; b.eq cache_zero; cmp w0,#90; b.eq cache_one;
        .word {int.from_bytes(original(0x410A93E0,4), 'little')}; b #0x410a93e4;
        cache_zero: {c('x8',C10_CACHE0)} b cache_copy;
        cache_one: {c('x8',C10_CACHE1)}
        cache_copy: stp x19,x30,[sp,#-16]!; mov x19,x8;
        mov x0,x19; mov w2,#0x242; bl #0x4127a900;
        {c('x10',0xA04705390)} ldr w8,[x10]; add w8,w8,#1; str w8,[x10]; str w8,[x19,#0x244];
        {c('x9',0xA047027D0)} ldr x10,[x9,#0x120]; cmp x10,x19; b.ne cache_movie;
        str w8,[x9,#0x128]; cache_movie:
        {c('x9',0xA04702928)} ldr x10,[x9,#0x120]; cmp x10,x19; b.ne cache_done;
        str w8,[x9,#0x128]; cache_done: mov w0,#1; ldp x19,x30,[sp],#16; ret;
    ''', 0x200)
    instruction(0x410A9FBC, f'b #{CACHE_GET}', 'C10 cache lookup retains original auxiliary-enable predicates')
    emit(CACHE_GET, f'''
        cmp w0,#40; b.eq tenth_cache; cmp w0,#90; b.eq tenth_cache;
        .word {int.from_bytes(original(0x410A9FBC,4), 'little')}; b #0x410a9fc0;
        tenth_cache: cbz x2,ordinary_flags;
        ldrb w8,[x2]; cmp w8,#1; b.ne no_cache;
        ldrb w8,[x2,#1]; cmp w8,#1; b.ne no_cache;
        ldrb w8,[x2,#2]; cmp w8,#1; b.ne no_cache;
        ldrb w8,[x2,#3]; cmp w8,#1; b.ne no_cache;
        ldrb w8,[x2,#4]; b last_flag;
        ordinary_flags: ldr w8,[x1,#0x10]; cmp w8,#1; b.ne no_cache;
        ldr w8,[x1,#0x14]; last_flag: cmp w8,#1; b.ne no_cache;
        stp x19,x20,[sp,#-48]!; str x30,[sp,#16]; mov w20,w0;
        {c('x19',C10_CACHE0)} cmp w0,#40; b.eq cache_ready; add x19,x19,#0x248;
        cache_ready: ldr w8,[x19,#0x244]; cbnz w8,have_cache;
        mov w0,#0x7c01; cmp w20,#40; b.eq get_key; add w0,w0,#2;
        get_key: add x1,sp,#32; bl #0x41015b30; cbz w0,lookup_failed;
        ldr x1,[sp,#32]; mov w0,w20; bl #0x410a93e0;
        have_cache: mov x0,x19; b lookup_done;
        lookup_failed: mov x0,#0;
        lookup_done: ldr x30,[sp,#16]; ldp x19,x20,[sp],#48; ret;
        no_cache: mov x0,#0; ret;
    ''', 0x200)
    instruction(0x410A85C4, f'b #{CACHE_GENERATION}', 'C10 current generation reads owned cache, never adjacent C1 or counter')
    emit(CACHE_GENERATION, f'''
        cmp w19,#40; b.eq generation_zero; cmp w19,#90; b.eq generation_one;
        ldr w8,[x8,#0x244]; b generation_done;
        generation_zero: {c('x8',C10_CACHE0)} b generation_read;
        generation_one: {c('x8',C10_CACHE1)}
        generation_read: ldr w8,[x8,#0x244];
        generation_done: cmp x22,#0; b #0x410a85c8;
    ''', 0x100)
    emit(CACHE_INVALIDATE, f'''
        {c('x8',0xA04702A80)} mov w9,#18;
        invalidate: str wzr,[x8,#0x244]; add x8,x8,#0x248; subs w9,w9,#1; b.ne invalidate;
        {c('x8',C10_CACHE0)} str wzr,[x8,#0x244]; str wzr,[x8,#0x48c]; ret;
    ''', 0x100)
    emit(SYNC_CURRENT_ENTRY, f'''
        stp x19,x20,[sp,#-64]!; stp x21,x30,[sp,#16];
        {c('x0',0xA047027C0)} bl #0x40d2c674; cbz w0,sync_bad;
        {c('x8',0xA02983CF8)} ldp w19,w20,[x8];
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w0,w19; mov w1,#0; bl #{DECODE_CURRENT_ENTRY}; cbz w2,sync_bad;
        str w0,[sp,#32]; str w1,[sp,#40];
        mov w0,w20; mov w1,#1; bl #{DECODE_CURRENT_ENTRY}; cbz w2,sync_bad;
        str w0,[sp,#36]; str w1,[sp,#44];
        // Stage the complete image so invalid source/key/class metadata cannot
        // partially replace the active header or reach the media callback.
        {c('x0',LOAD_BUFFER)} {c('x1',RECORDS)} mov x2,#0x6000; bl #0x4127a900;
        ldp x8,x9,[sp,#32]; {c('x10',LOAD_BUFFER+48)} stp x8,x9,[x10];
        {c('x0',LOAD_BUFFER)} bl #{VALIDATE_ENTRY}; cbz w0,sync_bad;
        ldp x8,x9,[sp,#32]; {c('x10',RECORDS+48)} stp x8,x9,[x10];
        mov w0,#1; b sync_done;
        sync_bad: mov w0,#0;
        sync_done: ldp x21,x30,[sp,#16]; ldp x19,x20,[sp],#64; ret;
    ''', 0x200)
    emit(DECODE_CURRENT_ENTRY, f'''
        sub w8,w0,#31; cmp w8,#9; b.ls decode_photo;
        sub w8,w0,#81; cmp w8,#9; b.ls decode_movie;
        // Builtin/special controls are owned by the original system. Clear the
        // extension slot instead of resurrecting a stale custom selection.
        mov w0,#0; mov w2,#1; ret;
        decode_movie: cbz w1,decode_bad; mov w1,#1; b decode_custom;
        decode_photo: mov w1,#0;
        decode_custom: add w0,w8,#1; mov w9,#10; madd w9,w1,w9,w8;
        {c('x8',RECORDS+64)} add x8,x8,x9,lsl #10;
        ldr w9,[x8]; cmp w9,#1; cset w2,eq; ret;
        decode_bad: mov w2,#0; ret;
    ''', 0x100)
    extra_map = CODE_BASE + 0x3200
    instruction(0x410A9E90, f'b #{extra_map}', 'C10 Flexible Color extra setting mapping with original fallback')
    emit(extra_map, f'''
        cmp w0,#40; b.eq extra_zero; cmp w0,#90; b.eq extra_one;
        .word {int.from_bytes(original(0x410A9E90,4), 'little')}; b #0x410a9e94;
        extra_zero: mov w8,#0x7c04; b extra_new;
        extra_one: mov w8,#0x7c05;
        extra_new: cbz x1,extra_bad; movk w8,#0x100,lsl #16; str w8,[x1]; mov w0,#1; ret;
        extra_bad: mov w0,#0; ret;
    ''', 0x100)
    # Invalid modifications cannot silently overlap other patches.
    ends = sorted((p['address'], p['address'] + len(p['expected'])) for p in patches)
    if any(a[1] > b[0] for a, b in zip(ends, ends[1:])):
        raise ValueError('Overlapping C10 patches')
    return dict(schema=1, source_sha256=MAIN_SHA256, simulation_only=True,
                patches=patches, regions=[dict(address=CODE_BASE, data=bytes(code), permissions='rx'),
                                         dict(address=DATA_BASE, data=bytes(data), permissions='rw')],
                entries=dict(import_np3=IMPORT_ENTRY, select=SELECT_ENTRY, select_full=SELECT_FULL_ENTRY, list=LIST_ENTRY,
                             save=SAVE_ENTRY, load=LOAD_ENTRY, sync_current=SYNC_CURRENT_ENTRY,
                             save_callback=SAVE_CALLBACK, load_callback=LOAD_CALLBACK),
                record_image=dict(address=RECORDS, bytes=RECORD_BYTES, staging=LOAD_BUFFER,
                                  schema=2, selected_slots_offset=48, source_banks_offset=56,
                                  flexible_extra_offset=0x370, flexible_extra_bytes=0x2C),
                limitations=['Synthetic reserved mappings; not physical free-space evidence',
                             'Native menu binding and actual media callbacks require separate integration',
                             'Flexible Color coverage uses authored 0310 fixtures; real exports and online cloud delivery need separate acceptance'])


class PictureControl:
    """Bounded original-instruction fixture; runtime/file services are explicit."""
    def __init__(self, source):
        self.source = _check_source(source)
        _dependencies()
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import arm64_const as r
        self.r = r
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.plan = build_plan(source)
        self.executed, self.services = set(), []
        self.uc.mem_map(BASE, (len(source) + 4095) & ~4095)
        self.uc.mem_write(BASE, source)
        self.uc.mem_map(0xA02800000, 0x900000)
        self.uc.mem_write(0xA02808000, source[0x4B13000:0x4DC0000])
        self.uc.mem_map(0xA03B00000, 0x1400000)
        self.uc.mem_map(0x46000000, 0x100000)
        for region in self.plan['regions']:
            self.uc.mem_map(region['address'], (len(region['data']) + 4095) & ~4095)
            self.uc.mem_write(region['address'], region['data'])
        for patch in self.plan['patches']:
            if bytes(self.uc.mem_read(patch['address'], len(patch['expected']))) != patch['expected']:
                raise ValueError('Guest original byte guard failed')
            self.uc.mem_write(patch['address'], patch['replacement'])
        self.uc.reg_write(r.UC_ARM64_REG_CPACR_EL1, 3 << 20)
        self._hook = self.uc.hook_add(UC_HOOK_CODE, self._observe)
        self.last_trace = []

    def close(self):
        if self.uc is not None:
            self.uc.hook_del(self._hook)
            self.uc = None

    def _observe(self, uc, address, size, _):
        r = self.r
        self.executed.add(address)
        self.last_trace.append(address)
        if len(self.last_trace) > 64:
            self.last_trace.pop(0)
        # Only environmental services are replaced, never parser results,
        # record lookup, type/length guards, copies or list-generation logic.
        fixture = {0x4086871C: 0, 0x40874284: 1, 0x408743E0: 0,
                   0x40D2C674: 1, 0x40D2C660: 1, 0x41014B0C: 0,
                   # libc formatter needs initialized per-thread locale;
                   # the declared buffer-file service does not use its path.
                   0x4127D850: 0}
        if address in fixture:
            self.services.append(hex(address))
            uc.reg_write(r.UC_ARM64_REG_X0, fixture[address])
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))

    def call(self, address, *args):
        r, u = self.r, self.uc
        for i, value in enumerate(args):
            u.reg_write(getattr(r, f'UC_ARM64_REG_X{i}'), value)
        preserved = tuple(0x12340000 + i for i in range(11))
        for i, value in enumerate(preserved, 19):
            u.reg_write(getattr(r, f'UC_ARM64_REG_X{i}'), value)
        u.reg_write(r.UC_ARM64_REG_SP, 0x460F0000)
        u.reg_write(r.UC_ARM64_REG_LR, 0x460FF000)
        self.last_trace = []
        try:
            u.emu_start(address, 0x460FF000, timeout=10_000_000, count=2_000_000)
        except Exception as exc:
            raise RuntimeError(f'A64 failed at {u.reg_read(r.UC_ARM64_REG_PC):#x}; trace {[hex(a) for a in self.last_trace[-12:]]}') from exc
        if u.reg_read(r.UC_ARM64_REG_PC) != 0x460FF000 or u.reg_read(r.UC_ARM64_REG_SP) != 0x460F0000:
            raise RuntimeError('Original function did not return within the bounded ABI contract')
        if tuple(u.reg_read(getattr(r, f'UC_ARM64_REG_X{i}')) for i in range(19, 30)) != preserved:
            raise RuntimeError('Callee-saved A64 register contract violated')
        return u.reg_read(r.UC_ARM64_REG_X0)

    def _input(self, blob):
        blob = bytes(blob)
        if not 4 <= len(blob) <= 4096:
            raise ValueError('NP3 input outside bounded native reader')
        # Bounds validation is an input preflight, not a replacement parser.
        offset = 4
        while offset < len(blob):
            if offset + 8 > len(blob):
                raise ValueError('Truncated NP3 TLV header')
            _, length = struct.unpack_from('>II', blob, offset)
            offset += 8 + length
            if offset > len(blob):
                raise ValueError('Truncated NP3 TLV body')
        self.uc.mem_write(INPUT, blob)
        self.uc.mem_write(PARAMS, struct.pack('<QQ', INPUT, len(blob)))
        return len(blob)

    def parse_np3(self, blob):
        self._input(blob)
        self.uc.mem_write(0xA047180D0, bytes(256))
        self.uc.mem_write(0xA0471074A, b'\0')
        if self.call(0x410ADC64, 0, PARSED, SCRATCH) != 1:
            raise ValueError('Original NP3 main parser rejected input')
        if self.call(0x410DAC30, 200, PRIMARY) != 1:
            raise ValueError('Original NP3 main conversion rejected input')
        pointer = self.call(0x410AC6D0, 200)
        if not pointer:
            raise ValueError('Original NP3 auxiliary parser rejected input')
        return ParsedControl(bytes(self.uc.mem_read(PRIMARY, 0x114)), bytes(self.uc.mem_read(pointer, 0x242)),
                             bytes(self.uc.mem_read(PARSED + 0x12C, 0x2C)))

    def import_np3(self, slot, blob, mode=0):
        _record(slot, mode)
        size = self._input(blob)
        if self.call(IMPORT_ENTRY, INPUT, size, slot, mode) != 1:
            raise ValueError('Original NP3 parser or guest record submission rejected input')
        return self.select(slot, mode)

    def select(self, slot, mode=0):
        _record(slot, mode)
        address = self.call(SELECT_ENTRY, slot, mode)
        if not address:
            raise ValueError('Guest slot is empty or invalid')
        return ParsedControl(bytes(self.uc.mem_read(address + 0x10, 0x114)),
                             bytes(self.uc.mem_read(address + 0x124, 0x242)),
                             bytes(self.uc.mem_read(address + 0x370, 0x2C)))

    def slots(self, mode=0):
        if mode not in (0, 1):
            raise ValueError('Invalid mode')
        self.call(LIST_ENTRY, mode)
        # Read original list OUTPUT, not a host-maintained slot catalog. The
        # original native notification descriptors store total count at +8.
        count_address = 0xA02983CB0 if mode == 0 else 0xA02983CC8
        total = struct.unpack('<I', self.uc.mem_read(count_address + 8, 4))[0]
        count = total - 31
        if not 0 <= count <= 10:
            raise ValueError('Original list returned invalid count')
        values = struct.unpack('<' + 'I' * count, self.uc.mem_read((LIST0 if mode == 0 else LIST1) + 0x7C, count * 4))
        return [10 if v == 40 else v - 27 for v in values]

    def current_selection(self, mode=0):
        if mode not in (0, 1):
            raise ValueError('Invalid mode')
        self.call(0x410A7B58 if mode == 0 else 0x410A7D10, SCRATCH, SCRATCH + 0x100)
        return (struct.unpack('<I', self.uc.mem_read(SCRATCH, 4))[0],
                bytes(self.uc.mem_read(SCRATCH + 0x100, 0x158)))

    def guards_intact(self):
        return all(bytes(self.uc.mem_read(addr + 0x180, 16)) == b'C10-LIST-GUARD!!' for addr in (LIST0, LIST1))

    def export_records(self):
        """Inspect guest bytes for offline transfer tests; not a media save."""
        raw = bytes(self.uc.mem_read(RECORDS, RECORD_BYTES))
        return raw + hashlib.sha256(raw).digest()

    def restore_records(self, image):
        """Offline context fixture ONLY; root uses LOAD_ENTRY/native media I/O."""
        image = bytes(image)
        if (len(image) != RECORD_BYTES + 32 or image[:48] != HEADER[:48]
                or hashlib.sha256(image[:-32]).digest() != image[-32:]):
            raise ValueError('Invalid offline guest-record image')
        self.uc.mem_write(LOAD_BUFFER, image[:-32])
        if self.call(VALIDATE_ENTRY, LOAD_BUFFER) != 1:
            raise ValueError('Invalid guest-record keys or selection')
        self.uc.mem_write(RECORDS, image[:-32])
        self.call(CACHE_INVALIDATE)
        sources = struct.unpack_from('<II', image, 56)
        for mode, slot in enumerate(struct.unpack_from('<II', image, 48)):
            if slot:
                if not self.call(SELECT_FULL_ENTRY, slot, sources[mode], mode):
                    raise ValueError('Offline guest current restoration failed')
