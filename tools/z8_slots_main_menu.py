# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded simulation Picture Control registry, PTP metadata and LCD pages.

The original publisher rejects 41 candidates when its separate registry has
capacity 40. PTP metadata and the actual LCD definitions have separate limits.
The LCD extension preserves native widget allocation, navigation and selection;
visible labels/keyboard behavior still require whole-guest proof.
"""
import struct

from z8_picture_control import BASE, MAIN_SHA256, _assemble, _check_source, _constant
from z8_slots_layout import Layout
from z8_slots_core import build_plan, decode_asm, record_asm

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


def build_main_menu_plan(source, layout=Layout(), *, core_plan=None, extend_copy_page=False):
    """Return native patches and a guarded overlay in the reserved core gap.

    The caller must apply core_data_overlay to a COPY of the core RW region,
    verifying every expected zero byte. This avoids overlapping loader regions.
    It must not modify the supplied core plan or bypass native bounds checks.
    """
    source = _check_source(source)
    L=layout; COUNT=L.slots; TOTAL=31+COUNT
    MAIN_CODE,MAIN_CODE_BYTES=L.main_code,0x8000
    MAIN_DATA,MAIN_DATA_BYTES=L.address('main_menu'),L.regions['main_menu'][1]
    ORIGINAL_TABLE,TABLE=0x440F0430,MAIN_DATA
    CACHE0,CACHE1=MAIN_DATA+0x1000,MAIN_DATA+0x1400
    REGISTRY0,REGISTRY1=0xA02948688,0xA029486B0
    C10_ITEM_ID=0xD3
    LCD_LABEL_CHOOSE=MAIN_CODE+0x800
    LCD_LABEL_ROW=MAIN_CODE+0x900
    LCD_LABEL_CURRENT=MAIN_CODE+0xB00
    LCD_LABEL_MOVIE_CURRENT=MAIN_CODE+0xD00
    if core_plan is None:
        core_plan = build_plan(source,L)
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
    for slot in range(10,COUNT+1):
        struct.pack_into('<7I',data,len(table)+(slot-10)*28,C10_ITEM_ID+slot-10,L.code(slot),
            L.keys(slot,0)[0],L.keys(slot,1)[0],L.selector(slot,0),L.selector(slot,1),0xFFFF)
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
            instruction(address, 'mov w1,#40', f'mov w1,#{TOTAL}')
    for registry, cache, old_pointer, key in ((REGISTRY0, CACHE0, 0xA03D42D48, 0x07000633),
                                             (REGISTRY1, CACHE1, 0xA03D42DE8, 0x0700122B)):
        _at, record = original(registry, 0x28)
        if (struct.unpack_from('<I', record)[0] != key or
                struct.unpack_from('<Q', record, 0x10)[0] != old_pointer or
                struct.unpack_from('<I', record, 0x18)[0] != 40):
            raise ValueError('Original Picture Control registry layout differs')
        change(registry + 0x10, struct.pack('<Q', cache), 'Relocate main-menu published candidate storage')
        change(registry + 0x18, struct.pack('<I',TOTAL), 'Give original publisher verified 41-item capacity')
        data[cache - MAIN_DATA + 0x3E0:cache - MAIN_DATA + 0x3F0] = b'MAIN-CACHE-GUARD'
    # The name/parameter serializer validates selectors through this original
    # entry, separate from the i-menu's 403f95d8 wrapper. Only custom selectors
    # use the restored owned flags; all builtin/file selectors retain original.
    native_valid = 0x410DB078
    instruction(native_valid, 'cmp w0,#39', f'b #{MAIN_CODE}')
    valid = _assemble(decode_asm(L,'w0','custom','old','valid')+f'''
        custom: {record_asm(L)} ldr w8,[x10]; cmp w8,#1; cset w0,eq; ret;
        old: cmp w0,#39; b #{native_valid+4};''',MAIN_CODE)
    if len(valid)>0x200:raise ValueError('Validity adapter exceeds reservation')
    code[:len(valid)]=valid
    # 407f1c04 is another original PC-list caller with a fixed stack buffer.
    # Widening only its length would overwrite adjacent metadata. Move the
    # metadata by8 and saved registers by16, keeping the complete frame aligned.
    delta=((TOTAL*4-160)+15)&~15
    instruction(0x407F1C04,'sub sp,sp,#0x100',f'sub sp,sp,#{0x100+delta}')
    instruction(0x407F1D34,'add sp,sp,#0x100',f'add sp,sp,#{0x100+delta}')
    for address,opcode,registers,offset in (
        (0x407F1C08,'str','x24',0xC0),(0x407F1C0C,'stp','x23,x22',0xD0),
        (0x407F1C10,'stp','x21,x20',0xE0),(0x407F1C14,'stp','x19,x30',0xF0),
        (0x407F1C60,'stp','xzr,xzr',0xA8),(0x407F1C80,'str','x24',0xB8),
        (0x407F1CA8,'ldr','w8',0xB0),(0x407F1CBC,'ldr','x8',0xB8),
        (0x407F1CD4,'ldrsw','x8',0xB0),(0x407F1D10,'ldr','x24',0xC0),
        (0x407F1D28,'ldp','x19,x30',0xF0),(0x407F1D2C,'ldp','x21,x20',0xE0),
        (0x407F1D30,'ldp','x23,x22',0xD0)):
        instruction(address,f'{opcode} {registers},[sp,#{offset}]',f'{opcode} {registers},[sp,#{offset+delta}]')
    for address in (0x407F1C5C,0x407F1C8C,0x407F1C98):
        instruction(address,'mov w2,#0xa0',f'mov w2,#{TOTAL*4}')
    for address in (0x407F1C90,0x407F1C9C):
        instruction(address,'add x1,sp,#0xa8',f'add x1,sp,#{0xA8+delta}')

    # Actual LCD pages are independent of the PTP metadata above. The native
    # allocator consumes these initialized definitions before building widgets.
    # Keep every existing row, separator, predicate and navigation event intact.
    cursor = 0x1800
    emitted = [(MAIN_CODE, MAIN_CODE + len(valid))]

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
        if any(address < end and start < address + len(raw) for start, end in emitted):
            raise ValueError('LCD executable functions overlap')
        emitted.append((address, address + len(raw)))
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

    pages=[]
    for bank,definition,old_rows,old_events,count,old_pairs in (
        (0,0xA028CD6F0,0x43DE18B4,0x43DE1FC0,41,0x42B2B4C0),
        (1,0xA028C20C0,0x43C13924,0x43C14060,42,0x42B2B600)):
        descriptor=original(definition,0x22)[1]
        if struct.unpack_from('<QQH',descriptor,0x10)!=(old_rows,old_events,count):
            raise ValueError('Original LCD definition changed')
        pairs=original(old_pairs,320)[1];rows=original(old_rows,count*44)[1]
        keys={struct.unpack_from('<I',rows,i*44+0x18)[0] for i in range(count)}
        events=original(old_events,count*8)[1]
        template=original(struct.unpack_from('<Q',events,len(events)-8)[0],0x200)[1]
        added_rows=bytearray();added_pairs=bytearray();added_events=bytearray();extensions=[]
        for slot in range(10,COUNT+1):
            key=L.row_key(slot,bank)
            if key in keys:raise ValueError('LCD key collision')
            selector=L.selector(slot,bank)
            row=bytearray(rows[-44:]);struct.pack_into('<II',row,0x14,L.label_id(slot),key)
            added_rows+=row;added_pairs+=struct.pack('<II',selector,key)
            handler=MAIN_CODE+0x4000+(bank*55+slot-10)*0x40
            callbacks=bytearray(template)
            for offset,event in ((0xC0,0x28B),(0x160,0x2AE)):
                if struct.unpack_from('<I',callbacks,offset)[0]!=event or struct.unpack_from('<Q',callbacks,offset+8)[0]!=(0x405E6DA0 if bank==0 else 0x4056ECD8):
                    raise ValueError('Original row event changed')
                struct.pack_into('<Q',callbacks,offset+8,handler)
            event_list=allocate(callbacks);added_events+=struct.pack('<Q',event_list)
            if bank==0:emit(handler,f'mov w0,#{selector}; b #0x403f96a8;',0x40)
            else:emit(handler,f'''str x30,[sp,#-16]!; mov w0,#0x122c; movk w0,#0x0300,lsl #16;
                mov w1,#0; bl #0x403ec39c; mov w0,#{selector}; ldr x30,[sp],#16; b #0x403f96a8;''',0x40)
            extensions.append(dict(slot=slot,selector=selector,key=key,handler=handler,events=event_list))
        new_pairs=allocate(pairs+added_pairs);new_rows=allocate(rows+added_rows);new_events=allocate(events+added_events)
        change(definition+0x10,struct.pack('<QQH',new_rows,new_events,count+COUNT-9),'Configured independent LCD rows and native events')
        pages.append(dict(definition=definition,rows=new_rows,events=new_events,pairs=new_pairs,
            old_pairs=old_pairs,count=count+COUNT-9,extensions=extensions))
    photo_refs = ((0x405FE664, 20), (0x405FECA0, 21), (0x405FF698, 22),
                  (0x40600D98, 19), (0x40600EC4, 0), (0x40601144, 21),
                  (0x40601398, 21), (0x40601548, 20), (0x40601688, 11))
    movie_refs = ((0x405FE680, 20), (0x405FEF3C, 21), (0x405FF768, 22),
                  (0x40601680, 8), (0x4060178C, 0))
    for page, references in zip(pages, (photo_refs, movie_refs)):
        for start, register in references:
            relocate_literal(start, register, page['old_pairs'], page['pairs'])
    for address in (0x40600ED4, 0x4060179C):
        instruction(address, 'mov w9,#40', f'mov w9,#{TOTAL}')

    # Liveview Picture Control indicator (OSD item 0x24, drawer 4041a7dc). The
    # drawer indexes 58-entry icon/text tables by display setting 0x251d; the
    # original producer has no index for configured slots and publishes 0
    # (Auto). The original C1..C9 entries are single composite bitmaps
    # ("FC C-1" is 0x1824), so no original glyph can carry a new number, and
    # "FC C-N" as text in the only style verified on this layer (0x21f) is
    # about twice the field width. Only when that index is 0 and the original
    # photo selector getter (403f9608) returns a configured slot does the hook
    # set both widgets to the owned menu label "C-N" (variant 1 for a Flexible
    # Color record, type 0x20, else variant 0).
    LV_INDICATOR = MAIN_CODE + 0x2000
    ICONS, TEXTS = 0x42AE751C, 0x42AE7604
    icons = original(ICONS, 58 * 4)[1]; texts = original(TEXTS, 58 * 4)[1]
    if (struct.unpack_from('<HH', icons, 0) != (0x7C5, 0x7C7) or struct.unpack_from('<H', texts, 0)[0] != 0x7C6 or
            struct.unpack_from('<HH', icons, 49 * 4) != (0x1824, 0x1824) or struct.unpack_from('<H', icons, 31 * 4)[0] == 0):
        raise ValueError('Original liveview Picture Control tables changed')
    tail = original(0x4041A988, 0x24)[1]
    if tail != _assemble('''ldrh w3,[x9]; mov w0,#0x24; ldrh w4,[x8]; orr w1,wzr,#1; mov w2,wzr;
            ldp x19,x30,[sp,#0x20]; ldp x21,x20,[sp,#0x10]; ldr x22,[sp],#0x30; b #0x4041ecb4''', 0x4041A988):
        raise ValueError('Original liveview indicator tail changed')
    instruction(0x4041A86C, 'cmp w19,#0x39', f'b #{LV_INDICATOR}')
    emit(LV_INDICATOR, f'''cbnz w19,old;
        bl #0x403f9608;
        {decode_asm(L,'w0','custom','old','lvind',extended_only=True)}
        custom: cbnz w9,old; mov w15,w8; {record_asm(L)}
        ldrh w11,[x10,#0x120]; sub w15,w15,#9; cmp w11,#0x20; cset w14,eq;
        mov w13,#5; mov w4,#0x7e00; madd w4,w15,w13,w4; add w4,w4,w14;
        mov w3,w4; mov w0,#0x24; mov w1,#1; mov w2,wzr;
        ldp x19,x30,[sp,#0x20]; ldp x21,x20,[sp,#0x10]; ldr x22,[sp],#0x30; b #0x4041ecb4;
        old: cmp w19,#0x39; b #0x4041a870;''', 0x200)
    # Manage Picture Control "copy to camera" destination page (LCD id 0xd1).
    # Its rows keep the page's own native event list; the original row event
    # 405e81c8 maps the row key to a selector through the 40601708 pair getter
    # and then calls the original card load/write chain. Only the getter's
    # table address/count and the page definition change; the count is never
    # 40, so 403eb788 keeps its original bounds.
    # NOT applied by default: it rewrites the original page row count (9) and
    # the getter count. Held for coordinator approval (IND-1, 2026-10-21).
    copy_page = None
    if extend_copy_page:
        copy_definition, copy_pairs_old, copy_count_old = 0xA028CFDB0, 0x42B2BE48, 9
        descriptor = original(copy_definition, 0x22)[1]
        copy_rows_old, copy_events_old, count = struct.unpack_from('<QQH', descriptor, 0x10)
        if count != copy_count_old:
            raise ValueError('Original copy-destination page changed')
        copy_rows = original(copy_rows_old, count * 44)[1]
        copy_events = original(copy_events_old, count * 8)[1]
        copy_pairs = original(copy_pairs_old, count * 8)[1]
        if [struct.unpack_from('<I', copy_pairs, i * 8)[0] for i in range(count)] != [L.selector(s, 0) for s in range(1, 10)]:
            raise ValueError('Original copy-destination selectors changed')
        if len(set(copy_events[i:i + 8] for i in range(0, len(copy_events), 8))) != 1:
            raise ValueError('Copy-destination rows no longer share one native event list')
        copy_keys = {struct.unpack_from('<I', copy_rows, i * 44 + 0x18)[0] for i in range(count)}
        added_rows, added_pairs, copy_extensions = bytearray(), bytearray(), []
        for slot in range(10, COUNT + 1):
            key = 0x7A00 + slot
            if key in copy_keys:
                raise ValueError('Copy-destination key collision')
            row = bytearray(copy_rows[-44:]); struct.pack_into('<II', row, 0x14, L.label_id(slot), key)
            added_rows += row; added_pairs += struct.pack('<II', L.selector(slot, 0), key)
            copy_extensions.append(dict(slot=slot, selector=L.selector(slot, 0), key=key))
        copy_pairs_new = allocate(copy_pairs + added_pairs)
        copy_rows_new = allocate(copy_rows + added_rows)
        copy_events_new = allocate(copy_events + copy_events[-8:] * (COUNT - 9))
        change(copy_definition + 0x10, struct.pack('<QQH', copy_rows_new, copy_events_new, COUNT),
               'Copy-destination page: configured rows with the original row event')
        getter = 0x4060170C
        for shift in (0, 16, 32, 48):
            op = 'movz' if shift == 0 else 'movk'; suffix = '' if shift == 0 else f',lsl #{shift}'
            at = getter + shift // 4
            old = f'{op} x0,#{copy_pairs_old >> shift & 0xFFFF}{suffix}'
            new = f'{op} x0,#{copy_pairs_new >> shift & 0xFFFF}{suffix}'
            _at, expected = original(at, 4)
            if expected != _assemble(old, at):
                raise ValueError('Unexpected copy-destination pair getter')
            change(at, _assemble(new, at), 'Copy-destination pair getter uses the configured table')
        instruction(0x4060171C, f'mov w9,#{copy_count_old}', f'mov w9,#{COUNT}')
        copy_page = dict(definition=copy_definition, rows=copy_rows_new, events=copy_events_new,
                         pairs=copy_pairs_new, old_pairs=copy_pairs_old, count=COUNT,
                         getter=0x40601708, extensions=copy_extensions)

    # Generic lookup grows only when given one of these owned pair tables.
    owned=[p['pairs'] for p in pages]+[L.address('imenu')+o for o in (0x2000,0x2400,0x2800,0x2C00)]
    for entry,fallback,target in ((0x403EB6A8,0x403EB6D8,MAIN_CODE+0x400),
                                  (0x403EB788,0x403EB7BC,MAIN_CODE+0x600)):
        instruction(entry,f'cbz x0,#{fallback}',f'b #{target}')
        comparisons=''.join(_constant('x8',p)+'cmp x0,x8; b.eq owned;' for p in owned)
        emit(target,f'''cbnz x0,nonnull; b #{fallback}; nonnull: cmp w1,#40; b.ne original;
            {comparisons} b original;
            owned: mov w1,#{TOTAL}; cmp w3,#40; b.ne original; mov w3,#{TOTAL};
            original: b #{entry+4};''',0x200)

    # Explicit selector membership replaces the 64-bit position bitmap.
    classify=MAIN_CODE+0x1400
    instruction(0x405FE734,'sub w8,w0,#0x1af',f'b #{classify}')
    emit(classify,decode_asm(L,'w0','custom','old','classify',extended_only=True)+
        'custom: b #0x405fe75c; old: sub w8,w0,#0x1af; b #0x405fe738;',0x100)
    membership=[]
    for index,(entry,reg,yes) in enumerate(((0x405FEA70,'w0',0x405FEA88),
        (0x406011B4,'w23',0x406011D8),(0x40601408,'w23',0x4060142C),(0x406015B8,'w22',0x406015DC))):
        target=MAIN_CODE+0x1600+index*0x200
        instruction(entry,f'sub w8,{reg},#31',f'b #{target}')
        emit(target,decode_asm(L,reg,'custom','old',f'member{index}')+
            f'custom: b #{yes}; old: sub w8,{reg},#31; b #{entry+4};',0x200)
        membership.append(dict(entry=entry,reg=reg,success=yes))

    # w3 is the slot, independent of sparse selector numbering and source bank.
    emit(LCD_LABEL_CHOOSE,'''mov w4,#0; tbnz w2,#0,enabled; mov w4,#3;
        enabled: cbz w1,finish; cmp w0,#0x20; b.eq flexible;
        sub w8,w0,#0x801; cmp w8,#0x13; b.hi finish;
        mov w4,#2; tbnz w2,#0,finish; mov w4,#4; b finish;
        flexible: mov w4,#1;
        finish: sub w3,w3,#10; mov w5,#5; madd w0,w3,w5,w4; mov w4,#0x7e00; add w0,w0,w4; ret;''',0x100)
    instruction(0x405FF058,'sub w8,w0,#0x801',f'b #{LCD_LABEL_ROW}')
    emit(LCD_LABEL_ROW,decode_asm(L,'w27','custom','old','row',extended_only=True)+f'''
        custom: add w3,w8,#1; ldrb w1,[sp,#0x4c]; mov w2,w25; bl #{LCD_LABEL_CHOOSE};
        mov w1,w0; b #0x405ff0f4;
        old: sub w8,w0,#0x801; b #0x405ff05c;''',0x180)
    instruction(0x40600DDC,'sub w8,w0,#0x801',f'b #{LCD_LABEL_CURRENT}')
    emit(LCD_LABEL_CURRENT,decode_asm(L,'w20','custom','old','current',extended_only=True)+f'''
        custom: mov w19,w0; mov w0,w20; bl #0x403f9e64; mov w2,w0; mov w0,w19;
        {decode_asm(L,'w20','slot','impossible','current2',extended_only=True)}
        slot: add w3,w8,#1; ldrb w1,[sp,#0x1c]; bl #{LCD_LABEL_CHOOSE};
        ldp x19,x30,[sp,#0x170]; ldp x28,x20,[sp,#0x160]; add sp,sp,#0x180; ret;
        impossible: brk #0x322;
        old: sub w8,w0,#0x801; b #0x40600de0;''',0x200)
    instruction(0x40573224,'sub w8,w0,#0x801',f'b #{LCD_LABEL_MOVIE_CURRENT}')
    emit(LCD_LABEL_MOVIE_CURRENT,decode_asm(L,'w22','custom','old','movie_current',extended_only=True)+f'''
        custom: stp x0,x30,[sp,#-16]!; mov w0,w22; bl #0x403f9e64; mov w2,w0;
        {decode_asm(L,'w22','slot','impossible','movie2',extended_only=True)}
        slot: add w3,w8,#1; ldr x0,[sp]; ldrb w1,[sp,#0x34]; bl #{LCD_LABEL_CHOOSE};
        mov w1,w0; ldp x0,x30,[sp],#16; b #0x405732f8;
        impossible: brk #0x323;
        old: sub w8,w0,#0x801; b #0x40573228;''',0x200)

    # Independent markers for every added native row; no indexing after C9.
    marker=allocate(bytes(2*55*16));cache_read,cache_write=MAIN_CODE+0xF00,MAIN_CODE+0x1100
    for entry,target,old,operation,resume in ((0x405FE8F0,cache_read,'ldrh w8,[x8,w27,uxtw #1]','ldrh w8,[x9,#8]',0x405FE8F4),
        (0x405FE908,cache_write,'strh w0,[x8,x27,lsl #1]','strh w0,[x9,#8]',0x405FE90C)):
        instruction(entry,old,f'b #{target}')
        emit(target,f'''stp x9,x10,[sp,#-32]!; str x11,[sp,#16]; sub w10,w27,#29;
            cmp w10,#{COUNT-10}; b.hi original;
            {_constant('x9',pages[0]['pairs'])} cmp x20,x9; b.eq photo;
            {_constant('x9',pages[1]['pairs'])} cmp x20,x9; b.ne original;
            {_constant('x9',marker+55*16)} b owned;
            photo: {_constant('x9',marker)}
            owned: add x9,x9,x10,lsl #4; ldr x11,[x19,#0x48]; ldr x8,[x9]; cmp x8,x11; b.eq ready;
            str x11,[x9]; strh wzr,[x9,#8];
            ready: {operation}; ldr x11,[sp,#16]; ldp x9,x10,[sp],#32; b #{resume};
            original: ldr x11,[sp,#16]; ldp x9,x10,[sp],#32; {old}; b #{resume};''',0x180)
    deferred_photo,deferred_movie=MAIN_CODE+0x1300,MAIN_CODE+0x1380
    instruction(0x410AB09C,'cmp w0,#39',f'b #{deferred_photo}')
    emit(deferred_photo,f'''sub w8,w0,#40; cmp w8,#{COUNT-10}; b.hi old;
        add w8,w8,#9; add w0,w8,#0x1000; ret; old: cmp w0,#39; b #0x410ab0a0;''',0x80)
    instruction(0x410A8BF4,'cmp w19,#40',f'b #{deferred_movie}')
    emit(deferred_movie,f'''sub w8,w19,#40; cmp w8,#{COUNT-10}; b.hi old;
        add w8,w8,#9; add w0,w8,#0x2000; b #0x410a8bd8;
        old: cmp w19,#40; b #0x410a8bf8;''',0x80)
    return dict(source_sha256=MAIN_SHA256, simulation_only=True, patches=patches,
                regions=[dict(address=MAIN_CODE,data=bytes(code),permissions='rx'),dict(address=MAIN_DATA,data=bytes(data),permissions='rw')],

                item_id=C10_ITEM_ID, table=dict(address=TABLE, rows=TOTAL, stride=28),
                registry_entries=[REGISTRY0, REGISTRY1], original_failure_id=0xD2,
                lcd_pages=pages,copy_page=copy_page,label_choose=LCD_LABEL_CHOOSE,membership=membership,
                lcd_cache_read=cache_read, lcd_cache_write=cache_write,
                limitations=['Native LCD definition/event contracts require whole-QEMU MCU and pixel acceptance',
                             'Only Set Picture Control photo/movie pages are extended; management/edit pages are separate'])
