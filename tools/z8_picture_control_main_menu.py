# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded simulation Picture Control registry, PTP metadata and LCD pages.

The original publisher rejects 41 candidates when its separate registry has
capacity 40. PTP metadata and the actual LCD definitions have separate limits.
The LCD extension preserves native widget allocation, navigation and selection;
visible labels/keyboard behavior still require whole-guest proof.
"""
import struct

from z8_picture_control import BASE, DATA_BASE, RECORDS, MAIN_SHA256, _assemble, _check_source, _constant, build_plan

MAIN_CODE, MAIN_CODE_BYTES = 0x45009000, 0x3000
MAIN_DATA, MAIN_DATA_BYTES = DATA_BASE + 0x1C000, 0x3000
ORIGINAL_TABLE, TABLE = 0x440F0430, MAIN_DATA
CACHE0, CACHE1 = MAIN_DATA + 0x800, MAIN_DATA + 0xA00
REGISTRY0, REGISTRY1 = 0xA02948688, 0xA029486B0
C10_ITEM_ID = 0xD3  # 0xd2 is an original lookup-failure sentinel, never C10.
LCD_PHOTO_KEY, LCD_MOVIE_KEY = 0x7FE0, 0x7FE1
LCD_LABEL_CHOOSE = MAIN_CODE + 0x400
LCD_LABEL_ROW = MAIN_CODE + 0x500
LCD_LABEL_CURRENT = MAIN_CODE + 0x600
LCD_LABEL_MOVIE_CURRENT = MAIN_CODE + 0xA00

# Verified references to the original 40x28-byte table. The second lookup in
# two functions reuses the already-loaded base but reloads the original count.
TABLE_REFERENCES = (
    (0x40811F58, 19, (0x40811F6C,)),
    (0x40812008, 19, (0x4081201C,)),
    (0x408120D0, 19, (0x408120E4,)),
    (0x40812198, 19, (0x408121AC,)),
    (0x40812200, 19, (0x40812214, 0x40812250)),
    (0x40812314, 20, (0x4081232C, 0x40812360)),
    (0x408123BC, 19, (0x408123D4,)),
    (0x408124F8, 19, (0x40812510,)),
    (0x40812560, 19, (0x40812574,)),
    (0x408125C8, 19, (0x408125E0,)),
    (0x40812634, 19, (0x4081264C,)),
)


def build_main_menu_plan(source, *, core_plan=None):
    """Return native patches and a guarded overlay in the reserved core gap.

    The caller must apply core_data_overlay to a COPY of the core RW region,
    verifying every expected zero byte. This avoids overlapping loader regions.
    It must not modify the supplied core plan or bypass native bounds checks.
    """
    source = _check_source(source)
    if core_plan is None:
        core_plan = build_plan(source)
    owners = [r for r in core_plan['regions'] if r['permissions'] == 'rw' and
              r['address'] <= MAIN_DATA and MAIN_DATA + MAIN_DATA_BYTES <= r['address'] + len(r['data'])]
    if len(owners) != 1:
        raise ValueError('Main-menu metadata requires the reserved core data gap')
    owner = owners[0]
    offset = MAIN_DATA - owner['address']
    if any(owner['data'][offset:offset + MAIN_DATA_BYTES]):
        raise ValueError('Main-menu data reservation is not zero in the core plan')
    patches, data, code = [], bytearray(MAIN_DATA_BYTES), bytearray(MAIN_CODE_BYTES)

    def original(address, size):
        if BASE <= address < BASE + len(source):
            at = address - BASE
        elif 0xA02808000 <= address and address + size <= 0xA02AB5000:
            at = 0x4B13000 + address - 0xA02808000
        else:
            raise ValueError('Main-menu patch outside verified source views')
        value = source[at:at + size]
        if len(value) != size:
            raise ValueError('Truncated source mapping')
        return at, value

    def change(address, replacement, name):
        at, expected = original(address, len(replacement))
        patches.append(dict(address=address, source_offset=at, expected=expected,
                            replacement=replacement, name=name))

    def instruction(address, old, new):
        _at, expected = original(address, 4)
        if expected != _assemble(old, address):
            raise ValueError(f'Unexpected main-menu literal/count at {address:#x}')
        replacement = _assemble(new, address)
        if len(replacement) != 4:
            raise ValueError('Main-menu patch must replace one instruction')
        change(address, replacement, 'Extend guarded original main-menu metadata lookup')

    _offset, table = original(ORIGINAL_TABLE, 40 * 28)
    if any(struct.unpack_from('<I', table, index * 28)[0] == C10_ITEM_ID for index in range(40)):
        raise ValueError('C10 main-menu item ID is already in the original table')
    data[:len(table)] = table
    struct.pack_into('<7I', data, len(table), C10_ITEM_ID, 40, 0x01007C00, 0x01007C02, 40, 90, 0xFFFF)
    for start, register, counts in TABLE_REFERENCES:
        position = start
        for shift in (0, 16, 32, 48):
            op = 'movz' if shift == 0 else 'movk'
            suffix = '' if shift == 0 else f',lsl #{shift}'
            old = f'{op} x{register},#{ORIGINAL_TABLE >> shift & 0xFFFF}{suffix}'
            if shift:
                matches = [at for at in range(position + 4, start + 28, 4)
                           if original(at, 4)[1] == _assemble(old, at)]
                if len(matches) != 1:
                    raise ValueError('Unverified interleaved main-menu literal')
                position = matches[0]
            instruction(position, old,
                        f'{op} x{register},#{TABLE >> shift & 0xFFFF}{suffix}')
        for address in counts:
            instruction(address, 'mov w1,#40', 'mov w1,#41')
    for registry, cache, old_pointer, key in ((REGISTRY0, CACHE0, 0xA03D42D48, 0x07000633),
                                             (REGISTRY1, CACHE1, 0xA03D42DE8, 0x0700122B)):
        _at, record = original(registry, 0x28)
        if (struct.unpack_from('<I', record)[0] != key or
                struct.unpack_from('<Q', record, 0x10)[0] != old_pointer or
                struct.unpack_from('<I', record, 0x18)[0] != 40):
            raise ValueError('Original Picture Control registry layout differs')
        change(registry + 0x10, struct.pack('<Q', cache), 'Relocate main-menu published candidate storage')
        change(registry + 0x18, struct.pack('<I', 41), 'Give original publisher verified 41-item capacity')
        data[cache - MAIN_DATA + 0x1E0:cache - MAIN_DATA + 0x1F0] = b'MAIN-CACHE-GUARD'
    # The name/parameter serializer validates selectors through this original
    # entry, separate from the i-menu's 403f95d8 wrapper. Only custom selectors
    # use the restored owned flags; all builtin/file selectors retain original.
    native_valid = 0x410DB078
    instruction(native_valid, 'cmp w0,#39', f'b #{MAIN_CODE}')
    valid = _assemble(f'''
        sub w8,w0,#31; cmp w8,#9; b.ls valid_photo;
        sub w8,w0,#81; cmp w8,#9; b.ls valid_movie;
        cmp w0,#39; b #{native_valid+4};
        valid_photo: {_constant('x9',RECORDS+64)} b valid_read;
        valid_movie: {_constant('x9',RECORDS+64+0x2800)}
        valid_read: add x9,x9,x8,lsl #10; ldr w8,[x9]; cmp w8,#1; cset w0,eq; ret;
    ''', MAIN_CODE)
    if len(valid) > 0x200:
        raise ValueError('Main-menu validity entry exceeds its reservation')
    code[:len(valid)] = valid
    # 407f1c04 is another original PC-list caller with a fixed stack buffer.
    # Widening only its length would overwrite adjacent metadata. Move the
    # metadata by8 and saved registers by16, keeping the complete frame aligned.
    stack_changes = (
        (0x407F1C04, 'sub sp,sp,#0x100', 'sub sp,sp,#0x110'),
        (0x407F1C08, 'str x24,[sp,#0xc0]', 'str x24,[sp,#0xd0]'),
        (0x407F1C0C, 'stp x23,x22,[sp,#0xd0]', 'stp x23,x22,[sp,#0xe0]'),
        (0x407F1C10, 'stp x21,x20,[sp,#0xe0]', 'stp x21,x20,[sp,#0xf0]'),
        (0x407F1C14, 'stp x19,x30,[sp,#0xf0]', 'stp x19,x30,[sp,#0x100]'),
        (0x407F1C5C, 'mov w2,#0xa0', 'mov w2,#0xa4'),
        (0x407F1C60, 'stp xzr,xzr,[sp,#0xa8]', 'stp xzr,xzr,[sp,#0xb0]'),
        (0x407F1C80, 'str x24,[sp,#0xb8]', 'str x24,[sp,#0xc0]'),
        (0x407F1C8C, 'mov w2,#0xa0', 'mov w2,#0xa4'),
        (0x407F1C90, 'add x1,sp,#0xa8', 'add x1,sp,#0xb0'),
        (0x407F1C98, 'mov w2,#0xa0', 'mov w2,#0xa4'),
        (0x407F1C9C, 'add x1,sp,#0xa8', 'add x1,sp,#0xb0'),
        (0x407F1CA8, 'ldr w8,[sp,#0xb0]', 'ldr w8,[sp,#0xb8]'),
        (0x407F1CBC, 'ldr x8,[sp,#0xb8]', 'ldr x8,[sp,#0xc0]'),
        (0x407F1CD4, 'ldrsw x8,[sp,#0xb0]', 'ldrsw x8,[sp,#0xb8]'),
        (0x407F1D10, 'ldr x24,[sp,#0xc0]', 'ldr x24,[sp,#0xd0]'),
        (0x407F1D28, 'ldp x19,x30,[sp,#0xf0]', 'ldp x19,x30,[sp,#0x100]'),
        (0x407F1D2C, 'ldp x21,x20,[sp,#0xe0]', 'ldp x21,x20,[sp,#0xf0]'),
        (0x407F1D30, 'ldp x23,x22,[sp,#0xd0]', 'ldp x23,x22,[sp,#0xe0]'),
        (0x407F1D34, 'add sp,sp,#0x100', 'add sp,sp,#0x110'),
    )
    for address, old, new in stack_changes:
        instruction(address, old, new)

    # Actual LCD pages are independent of the PTP metadata above. The native
    # allocator consumes these initialized definitions before building widgets.
    # Keep every existing row, separator, predicate and navigation event intact.
    cursor = 0xC00

    def allocate(raw):
        nonlocal cursor
        cursor = (cursor + 7) & ~7
        address = MAIN_DATA + cursor
        if cursor + len(raw) > MAIN_DATA_BYTES:
            raise ValueError('LCD data exceeds the reserved zero gap')
        data[cursor:cursor + len(raw)] = raw
        cursor += len(raw)
        return address

    def emit(address, assembly, capacity):
        raw = _assemble(assembly, address)
        if len(raw) > capacity or not MAIN_CODE <= address < address + len(raw) <= MAIN_CODE + MAIN_CODE_BYTES:
            raise ValueError('LCD code exceeds its reserved function slot')
        code[address - MAIN_CODE:address - MAIN_CODE + len(raw)] = raw

    def relocate_literal(start, register, old_value, new_value):
        position = start
        for shift in (0, 16, 32, 48):
            op = 'movz' if shift == 0 else 'movk'
            suffix = '' if shift == 0 else f',lsl #{shift}'
            old = f'{op} x{register},#{old_value >> shift & 0xFFFF}{suffix}'
            if shift:
                matches = [at for at in range(position + 4, start + 64, 4)
                           if original(at, 4)[1] == _assemble(old, at)]
                if not matches:
                    raise ValueError(f'Unverified LCD table address construction {start:#x} shift{shift}: {matches}')
                position = matches[0]
            instruction(position, old, f'{op} x{register},#{new_value >> shift & 0xFFFF}{suffix}')

    pages = []
    for bank, definition, old_rows, old_events, count, old_pairs, row_key, handler in (
        (0, 0xA028CD6F0, 0x43DE18B4, 0x43DE1FC0, 41, 0x42B2B4C0, LCD_PHOTO_KEY, MAIN_CODE + 0x200),
        (1, 0xA028C20C0, 0x43C13924, 0x43C14060, 42, 0x42B2B600, LCD_MOVIE_KEY, MAIN_CODE + 0x240),
    ):
        selector = 40 + 50 * bank
        descriptor = original(definition, 0x22)[1]
        if (struct.unpack_from('<QQ', descriptor, 0x10) != (old_rows, old_events) or
                struct.unpack_from('<H', descriptor, 0x20)[0] != count):
            raise ValueError('Unexpected original LCD definition layout')
        pairs = original(old_pairs, 320)[1]
        rows = original(old_rows, count * 44)[1]
        keys = [struct.unpack_from('<I', rows, i * 44 + 0x18)[0] for i in range(count)]
        if row_key in keys or struct.unpack_from('<II', pairs, 39 * 8) != (selector - 1, keys[-1]):
            raise ValueError('LCD owned row key collides or original C9 differs')
        new_pairs = allocate(pairs + struct.pack('<II', selector, row_key))
        extra_row = bytearray(rows[-44:])
        struct.pack_into('<I', extra_row, 0x14, 0x7FF0)
        struct.pack_into('<I', extra_row, 0x18, row_key)
        new_rows = allocate(rows + extra_row)
        events = original(old_events, count * 8)[1]
        c9_events = bytearray(original(struct.unpack_from('<Q', events, len(events) - 8)[0], 0x200)[1])
        old_handler = 0x405E6DA0 if bank == 0 else 0x4056ECD8
        for event_offset, event_id in ((0xC0, 0x28B), (0x160, 0x2AE)):
            if struct.unpack_from('<I', c9_events, event_offset)[0] != event_id or struct.unpack_from('<Q', c9_events, event_offset + 8)[0] != old_handler:
                raise ValueError('Original LCD C9 event callback differs')
            struct.pack_into('<Q', c9_events, event_offset + 8, handler)
        new_event_list = allocate(c9_events)
        new_events = allocate(events + struct.pack('<Q', new_event_list))
        change(definition + 0x10, struct.pack('<QQH', new_rows, new_events, count + 1), 'Append independent C10 LCD row and native event callbacks')
        if bank == 0:
            emit(handler, 'mov w0,#40; b #0x403f96a8;', 0x40)
        else:
            emit(handler, '''str x30,[sp,#-16]!; mov w0,#0x122c; movk w0,#0x0300,lsl #16;
                mov w1,#0; bl #0x403ec39c; mov w0,#90; ldr x30,[sp],#16; b #0x403f96a8;''', 0x40)
        pages.append(dict(definition=definition, old_rows=old_rows, old_events=old_events,
                          old_count=count, rows=new_rows, events=new_events, pairs=new_pairs,
                          old_pairs=old_pairs, row_key=row_key, handler=handler, selector=selector))
    photo_refs = ((0x405FE664, 20), (0x405FECA0, 21), (0x405FF698, 22),
                  (0x40600D98, 19), (0x40600EC4, 0), (0x40601144, 21),
                  (0x40601398, 21), (0x40601548, 20), (0x40601688, 11))
    movie_refs = ((0x405FE680, 20), (0x405FEF3C, 21), (0x405FF768, 22),
                  (0x40601680, 8), (0x4060178C, 0))
    for page, references in zip(pages, (photo_refs, movie_refs)):
        for start, register in references:
            relocate_literal(start, register, page['old_pairs'], page['pairs'])
    for address in (0x40600ED4, 0x4060179C):
        instruction(address, 'mov w9,#40', 'mov w9,#41')

    # Change search capacity only for these two owned tables AND the original
    # count40 ABI. Generic/file/edit contexts still search their original data.
    for entry, fallback, target in ((0x403EB6A8, 0x403EB6D8, MAIN_CODE + 0x300),
                                    (0x403EB788, 0x403EB7BC, MAIN_CODE + 0x380)):
        instruction(entry, f'cbz x0,#{fallback}', f'b #{target}')
        emit(target, f'''cbnz x0,search_nonnull; b #{fallback};
            search_nonnull: cmp w1,#40; b.ne search_original;
            {_constant('x8',pages[0]['pairs'])} cmp x0,x8; b.eq search_c10;
            {_constant('x8',pages[1]['pairs'])} cmp x0,x8; b.ne search_original;
            search_c10: mov w1,#41;
            search_original: b #{entry+4};''', 0x80)

    instruction(0x405FE74C, 'ccmp w8,#9,#0,hs', 'ccmp w8,#10,#0,hs')
    instruction(0x405FE754, 'ccmp w8,#8,#0,hs', 'ccmp w8,#9,#0,hs')
    for compare, low, high, register in ((0x405FEA74, 0x405FEA60, 0x405FEA68, 25),
                                         (0x406011B8, 0x406011C0, 0x406011C8, 10),
                                         (0x4060140C, 0x40601414, 0x4060141C, 10),
                                         (0x406015BC, 0x406015C4, 0x406015CC, 10)):
        instruction(compare, 'cmp w8,#0x3a', 'cmp w8,#0x3b')
        instruction(low, f'mov x{register},#0x1ff', f'mov x{register},#0x3ff')
        instruction(high, f'movk x{register},#0x7fc,lsl #48', f'movk x{register},#0xffc,lsl #48')

    # Five dedicated native text resources replace only C10's absent bitmaps.
    # Input: converted type, custom flag, native comparison state; output ID.
    emit(LCD_LABEL_CHOOSE, '''mov w3,#0x7ff0; tbnz w2,#0,label_enabled;
        mov w3,#0x7ff3; label_enabled: cbz w1,label_return;
        cmp w0,#0x20; b.eq label_flexible;
        sub w8,w0,#0x801; cmp w8,#0x13; b.hi label_return;
        mov w3,#0x7ff2; tbnz w2,#0,label_return; mov w3,#0x7ff4; b label_return;
        label_flexible: mov w3,#0x7ff1;
        label_return: mov w0,w3; ret;''', 0x100)
    instruction(0x405FF058, 'sub w8,w0,#0x801', f'b #{LCD_LABEL_ROW}')
    emit(LCD_LABEL_ROW, f'''cmp w27,#40; b.eq row_c10; cmp w27,#90; b.eq row_c10;
        sub w8,w0,#0x801; b #0x405ff05c;
        row_c10: ldrb w1,[sp,#0x4c]; mov w2,w25; bl #{LCD_LABEL_CHOOSE};
        mov w1,w0; b #0x405ff0f4;''', 0x100)
    instruction(0x40600DDC, 'sub w8,w0,#0x801', f'b #{LCD_LABEL_CURRENT}')
    emit(LCD_LABEL_CURRENT, f'''cmp w20,#40; b.eq current_c10;
        sub w8,w0,#0x801; b #0x40600de0;
        current_c10: mov w19,w0; mov w0,w20; bl #0x403f9e64;
        mov w2,w0; mov w0,w19; ldrb w1,[sp,#0x1c];
        cmp w0,#0x20; b.ne current_choose; tbnz w2,#0,current_choose;
        mov w0,#0x801;
        current_choose: bl #{LCD_LABEL_CHOOSE};
        ldp x19,x30,[sp,#0x170]; ldp x28,x20,[sp,#0x160]; add sp,sp,#0x180; ret;''', 0x100)

    # The stock creative-icon marker array has a proven domain0..28 only.
    # C10's index29 gets independent storage, tied to its native widget pool.
    # Existing markers, and every unrelated context, retain the original path.
    marker = allocate(bytes(32))
    cache_read, cache_write = MAIN_CODE + 0x700, MAIN_CODE + 0x800
    for entry, target, old_instruction, operation, resume in (
        (0x405FE8F0, cache_read, 'ldrh w8,[x8,w27,uxtw #1]', 'ldrh w8,[x9,#8]', 0x405FE8F4),
        (0x405FE908, cache_write, 'strh w0,[x8,x27,lsl #1]', 'strh w0,[x9,#8]', 0x405FE90C),
    ):
        instruction(entry, old_instruction, f'b #{target}')
        emit(target, f'''stp x9,x10,[sp,#-16]!; cmp w27,#29; b.ne cache_original;
            {_constant('x9',pages[0]['pairs'])} cmp x20,x9; b.eq cache_photo;
            {_constant('x9',pages[1]['pairs'])} cmp x20,x9; b.ne cache_original;
            {_constant('x9',marker+16)} b cache_owned;
            cache_photo: {_constant('x9',marker)}
            cache_owned: ldr x10,[x19,#0x48]; ldr x8,[x9]; cmp x8,x10; b.eq cache_ready;
            str x10,[x9]; strh wzr,[x9,#8];
            cache_ready: {operation}; ldp x9,x10,[sp],#16; b #{resume};
            cache_original: ldp x9,x10,[sp],#16; {old_instruction}; b #{resume};''', 0x100)

    # The deferred PictureControl service does not use the UI code mapper.
    # It reloads DSIDs and translates through a separate fixed table. Preserve
    # all original codes, including its failure fallback; only add code40.
    deferred_photo, deferred_movie = MAIN_CODE + 0x900, MAIN_CODE + 0x940
    instruction(0x410AB09C, 'cmp w0,#39', f'b #{deferred_photo}')
    emit(deferred_photo, '''cmp w0,#40; b.eq deferred_tenth;
        cmp w0,#39; b #0x410ab0a0;
        deferred_tenth: mov w0,#40; ret;''', 0x40)
    instruction(0x410A8BF4, 'cmp w19,#40', f'b #{deferred_movie}')
    emit(deferred_movie, '''cmp w19,#40; b.eq deferred_tenth;
        cmp w19,#40; b #0x410a8bf8;
        deferred_tenth: mov w0,#90; b #0x410a8bd8;''', 0x40)

    # Independent VIDEO overview uses a separate glyph-selection branch.
    # The inherit-photo branch already calls the photo helper above. Here the
    # original parameter getter has supplied type/flag for movie selector90.
    instruction(0x40573224, 'sub w8,w0,#0x801', f'b #{LCD_LABEL_MOVIE_CURRENT}')
    emit(LCD_LABEL_MOVIE_CURRENT, f'''cmp w22,#90; b.eq movie_current_c10;
        sub w8,w0,#0x801; b #0x40573228;
        movie_current_c10: stp x0,x30,[sp,#-16]!;
        mov w0,w22; bl #0x403f9e64; mov w2,w0; ldr x0,[sp];
        ldrb w1,[sp,#0x34]; bl #{LCD_LABEL_CHOOSE}; mov w1,w0;
        ldp x0,x30,[sp],#16; b #0x405732f8;''', 0x100)
    return dict(source_sha256=MAIN_SHA256, simulation_only=True, patches=patches,
                regions=[dict(address=MAIN_CODE, data=bytes(code), permissions='rx')],
                core_data_overlay=dict(address=MAIN_DATA, expected=bytes(MAIN_DATA_BYTES), data=bytes(data)),
                item_id=C10_ITEM_ID, table=dict(address=TABLE, rows=41, stride=28),
                registry_entries=[REGISTRY0, REGISTRY1], original_failure_id=0xD2,
                lcd_pages=pages,
                lcd_cache_read=cache_read, lcd_cache_write=cache_write,
                limitations=['Native LCD definition/event contracts require whole-QEMU MCU and pixel acceptance',
                             'Only Set Picture Control photo/movie pages are extended; management/edit pages are separate'])
