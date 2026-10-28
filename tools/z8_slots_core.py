# SPDX-License-Identifier: GPL-2.0-or-later
"""C32/C64 guest core. Original A64 parsing, setting and current-object code.

The schema reserves 64 records per bank but exposes only the configured count.
Nothing in this module writes the original MAIN or accesses a physical camera.
"""
import hashlib
import struct
from z8_slots_layout import Layout
import z8_picture_control as legacy
from z8_picture_control import BASE, MAIN_SHA256, _check_source, _assemble, _constant


def decode_asm(layout, reg, yes, no, prefix, *, extended_only=False):
    """Keep input intact; return zero-based index in w8, bank in w9."""
    old = '' if extended_only else f'''
        sub w8,{reg},#31; cmp w8,#8; b.ls {prefix}_photo;
        sub w8,{reg},#81; cmp w8,#8; b.ls {prefix}_movie;
    '''
    return old + f'''
        sub w8,{reg},#0x1000; cmp w8,#{layout.slots-1}; b.hi {prefix}_try_movie;
        cmp w8,#9; b.hs {prefix}_photo; b {no};
        {prefix}_try_movie: sub w8,{reg},#0x2000;
        cmp w8,#{layout.slots-1}; b.hi {no}; cmp w8,#9; b.lo {no};
        {prefix}_movie: mov w9,#1; b {yes};
        {prefix}_photo: mov w9,#0; b {yes};
    '''


def record_asm(layout, output='x10'):
    return f'''add w8,w8,w9,lsl #6; {_constant(output,layout.address('records')+128)}
        add {output},{output},x8,lsl #10;'''


def cache_asm(layout, output='x10'):
    return f'''add w8,w8,w9,lsl #6; mov w9,#0x248;
        {_constant(output,layout.address('cache'))} umaddl {output},w8,w9,{output};'''


def build_plan(source, layout=Layout()):
    source=_check_source(source)
    L=layout; C=L.core_code; A=L.address; c=_constant
    code=bytearray(0x8000); data=bytearray(L.reserved_bytes); patches=[]; emitted=[]
    entries={name:C+offset for name,offset in dict(
        import_np3=0,select=0x780,select_full=0x800,list=0xC00,file=0xE00,
        record=0x1000,validate=0x1200,save=0x1600,load=0x1700,sync_current=0x1A00,
        decode_current=0x1D00,main_map=0x2000,aux_map=0x2200,main_read=0x2400,
        menu_map=0x2600,photo_select=0x2800,movie_select=0x2A00,
        cache_update=0x2C00,cache_get=0x3000,cache_invalidate=0x3400,
        generation=0x3600,current_gate=0x3800,current_base=0x3A00,extra_map=0x3E00,
        save_callback=0x4000,load_callback=0x4040,file_valid=0x4100,file_alloc=0x4140,
        file_free=0x4180,parsed_copy=0x4200,encode=0x4400,list_adapter=0x4600,
        list_trampoline=0x4900,main_write=0x4A00,main_write_trampoline=0x4C00).items()}
    E=entries

    def original(address,size):
        offset=address-BASE if BASE<=address<BASE+len(source) else 0x4B13000+address-0xA02808000
        if not 0<=offset<=len(source)-size: raise ValueError('Unverified source address')
        return source[offset:offset+size],offset

    def patch(address,replacement,name):
        old,offset=original(address,len(replacement))
        patches.append(dict(address=address,source_offset=offset,expected=old,replacement=replacement,name=name))

    def ins(address,text,name):
        raw=_assemble(text,address)
        if len(raw)!=4: raise ValueError('A source instruction must stay four bytes')
        patch(address,raw,name)

    def emit(name,text,capacity):
        address=E[name] if isinstance(name,str) else name
        raw=_assemble(text,address)
        if len(raw)>capacity or not C<=address<address+len(raw)<=C+len(code):
            raise ValueError(f'Guest function exceeds its owned slot: {name} {len(raw):#x}/{capacity:#x}')
        if any(address<end and start<address+len(raw) for start,end in emitted):
            raise ValueError('Owned code overlap')
        emitted.append((address,address+len(raw)))
        code[address-C:address-C+len(raw)]=raw

    def put(address,raw):
        at=address-L.data_base
        if not 0<=at<=len(data)-len(raw):raise ValueError('Data outside reservation')
        data[at:at+len(raw)]=raw

    def relocate(addresses,reg,target):
        for shift,address in zip((0,16,32,48),addresses):
            op='movz' if shift==0 else 'movk'; suffix='' if not shift else f',lsl #{shift}'
            ins(address,f'{op} {reg},#{target>>shift&65535}{suffix}','Relocate bounded slot descriptor/list')

    groups=original(0x440244C8,124*48)[0]
    count=max(sum(struct.unpack_from('<HH',groups,i*48+24)) for i in range(124))
    items=original(0x4400A5C0,count*24)[0]
    put(A('groups'),groups);put(A('items'),items)
    extra_count=(L.capacity-9)*6
    if len(items)+extra_count*24>L.regions['items'][1]:raise ValueError('Descriptor budget exceeded')
    for group,first,amount in ((124,count,256),(125,count+256,extra_count-256)):
        struct.pack_into('<I',data,group*48,1)
        struct.pack_into('<HH',data,group*48+24,first,amount)
    for bank in (0,1):
        for slot in range(1,L.capacity+1):
            address=L.record(slot,bank,reserved=True);keys=L.keys(slot,bank,reserved=True)
            put(address+4,struct.pack('<III',*keys))
            put(A('keys')+(bank*L.capacity+slot-1)*12,struct.pack('<III',*keys))
            for part,key in enumerate(keys):
                group,index=(key>>8)&255,key&255
                first=struct.unpack_from('<H',data,group*48+24)[0]
                descriptor=A('items')+(first+index)*24
                default=struct.unpack_from('<Q',data,descriptor-L.data_base+16)[0] if slot<=9 else A('defaults')
                put(descriptor,struct.pack('<IIQQ',10,(0x114,0x242,44)[part],
                    address+(0x10,0x124,0x370)[part],default))
    header=(b'Z8SLOTS3'+struct.pack('<II',3,L.record_bytes)+bytes.fromhex(MAIN_SHA256)+
            struct.pack('<4I',0,0,0,1)+struct.pack('<4I',L.slots,L.capacity,2,L.stride)+bytes(48))
    put(A('records'),header);put(A('header'),header)
    put(A('codes'),struct.pack('<'+'I'*L.slots,*(L.code(s) for s in range(1,L.slots+1))))
    for bank,old_list,descriptor in ((0,0xA02983D04,0xA02983CB0),(1,0xA02983DA4,0xA02983CC8)):
        put(A(f'list{bank}'),original(old_list,40*4)[0])
        put(A(f'list{bank}')+0x3F0,b'SLOT-LIST-GUARD!')
        patch(descriptor+16,struct.pack('<Q',A(f'list{bank}')),'Owned list capacity for C32/C64')
    for address in (0x41015B34,0x41015EC4,0x4101561C):ins(address,'cmp w8,#0x7d','Allow two explicitly allocated settings groups')
    for addresses,reg,target in (
        ((0x41015B40,0x41015B44,0x41015B4C,0x41015B50),'x9',A('groups')),
        ((0x41015B80,0x41015B84,0x41015B88,0x41015B90),'x9',A('items')),
        ((0x41015ED0,0x41015ED4,0x41015EDC,0x41015EE0),'x9',A('groups')),
        ((0x41015F10,0x41015F14,0x41015F18,0x41015F20),'x9',A('items')),
        ((0x41015628,0x4101562C,0x41015634,0x41015638),'x9',A('groups')),
        ((0x41015668,0x4101566C,0x41015670,0x41015678),'x9',A('items')),
        ((0x410AACCC,0x410AACD4,0x410AACDC,0x410AACEC),'x10',A('codes')),
        ((0x410AAD38,0x410AAD40,0x410AAD48,0x410AAD58),'x10',A('codes')),
        ((0x410AACD0,0x410AACD8,0x410AACE0,0x410AACF0),'x11',A('list1')),
        ((0x410AAD3C,0x410AAD44,0x410AAD4C,0x410AAD5C),'x11',A('list0'))):relocate(addresses,reg,target)
    for address in (0x410AAD14,0x410AAD80):ins(address,f'cmp x9,#{L.slots}','Configured custom list count')
    for address,callee in ((0x410ADCA8,0x410AE940),(0x410AC834,0x410AE940)):
        if original(address,4)[0]!=_assemble(f'bl #{callee}',address):raise ValueError('NP3 input call site changed')
        ins(address,f"bl #{E['file']}",'Explicit NP3 input service')
    for address,name,callee in ((0x410DAC58,'parsed_copy',0x410A924C),(0x410AC7EC,'file_valid',0x40863C4C),
                                (0x410AC7FC,'file_alloc',0x40D2C408),(0x410AC924,'file_free',0x40D2C448)):
        if original(address,4)[0]!=_assemble(f'bl #{callee}',address):raise ValueError('NP3 runtime call site changed')
        ins(address,f"bl #{E[name]}",'Bounded NP3 runtime service')

    emit('record',f'''sub w8,w0,#1; cmp w8,#{L.slots-1}; b.hi bad; cmp w1,#1; b.hi bad;
        add w8,w8,w1,lsl #6; {c('x0',A('records')+128)} add x0,x0,x8,lsl #10; ret;
        bad: mov x0,#0; ret;''',0x100)
    emit('encode',f'''cmp w0,#1; b.lo bad; cmp w0,#{L.slots}; b.hi bad; cmp w1,#1; b.hi bad;
        cmp w0,#9; b.hi extended; mov w8,#50; madd w0,w1,w8,w0; add w0,w0,#30; ret;
        extended: add w8,w1,#1; lsl w8,w8,#12; sub w0,w0,#1; add w0,w8,w0; ret;
        bad: mov w0,#-1; ret;''',0x100)
    emit('import_np3',f'''
        stp x19,x20,[sp,#-64]!; stp x21,x22,[sp,#16]; stp x23,x30,[sp,#32];
        mov x19,x0; mov x20,x1; mov w21,w2; mov w22,w3;
        sub w8,w21,#1; cmp w8,#{L.slots-1}; b.hi bad; cmp w22,#1; b.hi bad;
        cmp x20,#4; b.lo bad; cmp x20,#1,lsl #12; b.hi bad;
        {c('x8',A('params'))} stp x19,x20,[x8];
        {c('x0',0xA047180D0)} mov x1,#0; mov x2,#0x100; bl #0x4127ab40;
        {c('x0',0xA0471074A)} strb wzr,[x0];
        mov x0,#0; {c('x1',A('parsed'))} {c('x2',A('scratch'))} bl #0x410adc64; cbz w0,bad;
        mov w0,#200; {c('x1',A('parsed')+0x200)} bl #0x410dac30; cbz w0,bad;
        mov w0,#200; bl #0x410ac6d0; cbz x0,bad; mov x19,x0;
        mov w0,w21; mov w1,w22; bl #{E['record']}; cbz x0,bad; mov x20,x0;
        ldr w0,[x20,#4]; mov w1,#0x114; {c('x2',A('parsed')+0x200)} bl #0x41015ec0; cbz w0,bad;
        ldr w0,[x20,#8]; mov w1,#0x242; mov x2,x19; bl #0x41015ec0; cbz w0,bad;
        ldr w0,[x20,#12]; mov w1,#44; {c('x2',A('parsed')+0x12c)} bl #0x41015ec0; cbz w0,bad;
        {c('x0',0xA047027C0)} bl #0x40d2c674; cbz w0,bad;
        mov w0,w21; mov w1,w22; bl #{E['encode']}; mov x1,x19; bl #0x410a93e0;
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w8,#1; str w8,[x20]; mov w0,#1; b done;
        bad: mov w0,#0;
        done: {c('x8',A('params'))} stp xzr,xzr,[x8];
        ldp x23,x30,[sp,#32]; ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#64; ret;
    ''',0x600)
    emit('select',f"mov w2,w1; b #{E['select_full']};",0x80)
    emit('select_full',f'''
        stp x19,x20,[sp,#-64]!; stp x21,x22,[sp,#16]; stp x23,x30,[sp,#32];
        mov w20,w0; mov w21,w1; mov w22,w2;
        cmp w22,#1; b.hi bad; cbnz w22,source_ok; cbnz w21,bad;
        source_ok: bl #{E['record']}; cbz x0,done; mov x19,x0;
        ldr w8,[x19]; cmp w8,#1; b.ne bad;
        ldr w0,[x19,#4]; add x1,sp,#48; bl #0x41015b30; cbz w0,bad;
        ldr x8,[sp,#48]; add x9,x19,#0x10; cmp x8,x9; b.ne bad;
        mov w0,w20; mov w1,w21; bl #{E['encode']}; mov w23,w0;
        {c('x0',0xA047027C0)} bl #0x40d2c674; cbz w0,bad;
        {c('x8',0xA02983CF8)} str w23,[x8,x22,lsl #2];
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w0,w23; mov w1,w22; bl #0x410a7c44;
        {c('x8',A('records')+48)} str w20,[x8,x22,lsl #2];
        {c('x8',A('records')+56)} str w21,[x8,x22,lsl #2];
        mov x0,x19; b done;
        bad: mov x0,#0;
        done: ldp x23,x30,[sp,#32]; ldp x21,x22,[sp,#16]; ldp x19,x20,[sp],#64; ret;
    ''',0x400)
    emit('list',f'''stp x19,x30,[sp,#-16]!; cmp w0,#1; b.hi bad; mov w19,w0; mov w1,w0;
        mov x0,#0; bl #0x410aac98;
        {c('x0',A('list0'))} add x0,x0,x19,lsl #10; b done;
        bad: mov x0,#0; done: ldp x19,x30,[sp],#16; ret;''',0x200)
    frame=(L.slots+16+15)&~15
    emit('list_adapter',f'''cmp w1,#1; b.hi original;
        str x30,[sp,#-{frame}]!; {c('x8',A('records')+128)} add x8,x8,x1,lsl #16;
        mov x9,#0; add x11,sp,#16;
        scan: ldr w10,[x8]; cmp w10,#1; cset w10,eq; strb w10,[x11,x9];
        add x8,x8,#0x400; add x9,x9,#1; cmp x9,#{L.slots}; b.ne scan;
        mov x0,x11; bl #{E['list_trampoline']}; ldr x30,[sp],#{frame}; ret;
        original: b #{E['list_trampoline']};''',0x300)
    displaced=original(0x410AAC98,4)[0]
    emit('list_trampoline',f".word {int.from_bytes(displaced,'little')}; b #0x410aac9c;",0x40)
    ins(0x410AAC98,f"b #{E['list_adapter']}",'Never read beyond original nine-byte caller bitmap')
    # A successful original main-parameter write (card copy, manage/edit pages)
    # registers the configured custom record; the original writer runs unchanged.
    # Extended slots (C10+): the original photo writer gates the settings key
    # at 410d9b8c to the native C1..C9 range, so a key in the owned settings
    # groups 0x7c/0x7d is written by the original settings store 41015ec0 into
    # its Layout record and the record is registered. Every other selector or
    # key (C1..C9, built-ins, unknown) executes the original writer unchanged.
    # SIMULATION ONLY - review before any hardware build.
    displaced=original(0x410D9968,4)[0]
    if displaced!=_assemble('sub sp,sp,#0x1f0',0x410D9968):raise ValueError('Original main writer prologue changed')
    emit('main_write_trampoline',f".word {int.from_bytes(displaced,'little')}; b #0x410d996c;",0x40)
    emit('main_write',f'''stp x19,x20,[sp,#-32]!; str x30,[sp,#16]; mov w19,w0;
        cbz x1,original;
        {decode_asm(L,'w0','extended','original','mainext',extended_only=True)}
        extended: {record_asm(L)} ldr w11,[x10,#4]; ubfx w12,w11,#8,#8; sub w12,w12,#0x7c; cmp w12,#1; b.hi original;
        mov x20,x10; mov x2,x1; mov w0,w11; mov w1,#0x114; bl #0x41015ec0; cbz w0,failed;
        mov w8,#1; str w8,[x20]; mov w20,#1; b done;
        failed: mov w20,#0; b done;
        original: bl #{E['main_write_trampoline']}; mov w20,w0; tbz w0,#0,done;
        {decode_asm(L,'w19','custom','done','mainwrite')}
        custom: {record_asm(L)} mov w8,#1; str w8,[x10];
        done: mov w0,w20; ldr x30,[sp,#16]; ldp x19,x20,[sp],#32; ret;''',0x200)
    ins(0x410D9968,f"b #{E['main_write']}",'Original main writer registers configured custom record')

    # The experimental input is active only while import_np3 owns params (it
    # clears them on every exit). Otherwise every service resumes the original
    # callee with untouched arguments, so card import keeps its native path.
    emit('file',f'''{c('x8',A('params'))} ldp x9,x10,[x8]; cbz x9,original; cmp x10,#1,lsl #12; b.hi bad;
        str x9,[x1]; str w10,[x1,#8]; str wzr,[x1,#12]; mov w0,#1; ret;
        bad: mov w0,#0; ret; original: b #0x410ae940;''',0x100)
    emit('file_valid',f'''{c('x8',A('params'))} ldp x9,x10,[x8]; cbz x9,original;
        cmp x10,#4; cset w0,hs; ret; original: b #0x40863c4c;''',0x40)
    emit('file_alloc',f'''{c('x8',A('params'))} ldr x9,[x8]; cbz x9,original;
        cmp x0,#1,lsl #12; b.hi bad; {c('x0',A('file'))} ret;
        bad: mov x0,#0; ret; original: b #0x40d2c408;''',0x40)
    emit('file_free',f'''{c('x8',A('params'))} ldr x9,[x8]; cbz x9,original;
        mov x0,#0; ret; original: b #0x40d2c448;''',0x40)
    emit('parsed_copy',f'''{c('x8',A('params'))} ldr x9,[x8]; cbz x9,original;
        cmp w0,#200; b.ne bad; str x30,[sp,#-16]!;
        mov x0,x1; {c('x1',A('parsed'))} mov x2,#0x158; bl #0x4127a900;
        mov w0,#1; ldr x30,[sp],#16; ret; bad: mov w0,#0; ret;
        original: b #0x410a924c;''',0x100)

    for native,name in ((0x410A95E8,'main_map'),(0x410AA9F4,'aux_map'),(0x410A7E70,'main_read'),
        (0x410A9E90,'extra_map'),(0x410AABC0,'menu_map'),(0x410A7BCC,'photo_select'),
        (0x410A7D84,'movie_select'),(0x410A93E0,'cache_update'),(0x410A9FBC,'cache_get'),
        (0x410A85C4,'generation'),(0x410A7C68,'current_gate'),(0x410A8560,'current_base')):
        ins(native,f'b #{E[name]}','Scoped C32/C64 native adapter with original fallback')
    emit('main_map',decode_asm(L,'w0','custom','old','map')+f'''
        custom: cbz x1,bad; cbz x2,bad; cbz x3,bad;
        mov w11,w8; mov w12,w9; {record_asm(L)} ldr w8,[x10,#4]; str w8,[x1];
        add w11,w11,#1; cmp w11,#9; mov w8,#27; mov w9,#30; csel w8,w8,w9,ls;
        add w8,w8,w11; str w8,[x2]; str w12,[x3]; mov w0,#1; ret;
        bad: mov w0,#0; ret; old: mov w8,wzr; b #0x410a95ec;''',0x200)
    for name,native,part,old_instruction in (
        ('aux_map',0x410AA9F4,8,'sub w8,w0,#81'),
        ('extra_map',0x410A9E90,12,f".word {int.from_bytes(original(0x410A9E90,4)[0],'little')}")):
        emit(name,decode_asm(L,'w0','custom','old',name)+f'''
            custom: cbz x1,bad; {record_asm(L)} ldr w8,[x10,#{part}]; str w8,[x1]; mov w0,#1; ret;
            bad: mov w0,#0; ret; old: {old_instruction}; b #{native+4};''',0x200)
    emit('main_read',decode_asm(L,'w0','custom','old','read')+f'''
        custom: {record_asm(L)} ldr w0,[x10,#4]; mov x2,x1; mov w1,#0x114; b #0x41015618;
        old: .word {int.from_bytes(original(0x410A7E70,4)[0],'little')}; b #0x410a7e74;''',0x200)
    emit('menu_map',f'''sub w8,w0,#40; cmp w8,#{L.slots-10}; b.hi old; cmp w1,#1; b.hi old;
        add w0,w8,#10; b #{E['encode']}; old: cmp w1,#4; b #0x410aabc4;''',0x200)
    for name,native,target in (('photo_select',0x410A7BCC,0),('movie_select',0x410A7D84,1)):
        emit(name,decode_asm(L,'w0','custom','old',name)+f'''
            custom: {'cbnz w9,bad;' if target==0 else ''}
            str x30,[sp,#-16]!; add w0,w8,#1; mov w1,w9; mov w2,#{target};
            bl #{E['select_full']}; cmp x0,#0; cset w0,ne; ldr x30,[sp],#16; ret;
            bad: mov w0,#0; ret;
            old: .word {int.from_bytes(original(native,4)[0],'little')}; b #{native+4};''',0x200)
    emit('current_gate',decode_asm(L,'w20','custom','old','gate',extended_only=True)+'''
        custom: b #0x410a7ca0; old: sub w8,w20,#0x258; b #0x410a7c6c;''',0x200)
    emit('current_base',decode_asm(L,'w19','custom','old','base',extended_only=True)+f'''
        custom: str x22,[x20,#0x120]; cbnz w9,movie; bl #0x410aa0c4; b pointer;
        movie: bl #0x410aa460;
        pointer: str x0,[x20,#0x118]; b calculate;
        calculate: {decode_asm(L,'w19','cache','impossible','base2',extended_only=True)}
        cache: {cache_asm(L)} ldr w8,[x10,#0x244]; cmp x22,#0; csel w8,wzr,w8,eq;
        mov x0,x21; b #0x410a85cc;
        impossible: brk #0x321;
        old: cmp w19,#40; b #0x410a8564;''',0x300)
    emit('cache_update',decode_asm(L,'w0','custom','old','update')+f'''
        custom: {cache_asm(L)} stp x19,x30,[sp,#-16]!; mov x19,x10;
        mov x0,x19; mov w2,#0x242; bl #0x4127a900;
        {c('x10',0xA04705390)} ldr w8,[x10]; add w8,w8,#1; str w8,[x10]; str w8,[x19,#0x244];
        {c('x9',0xA047027D0)} ldr x10,[x9,#0x120]; cmp x10,x19; b.ne movie;
        str w8,[x9,#0x128]; movie: {c('x9',0xA04702928)}
        ldr x10,[x9,#0x120]; cmp x10,x19; b.ne done; str w8,[x9,#0x128];
        done: mov w0,#1; ldp x19,x30,[sp],#16; ret;
        old: .word {int.from_bytes(original(0x410A93E0,4)[0],'little')}; b #0x410a93e4;''',0x300)
    emit('cache_get',decode_asm(L,'w0','custom','old','cacheget')+f'''
        custom: cbz x2,ordinary;
        ldrb w10,[x2]; cmp w10,#1; b.ne none; ldrb w10,[x2,#1]; cmp w10,#1; b.ne none;
        ldrb w10,[x2,#2]; cmp w10,#1; b.ne none; ldrb w10,[x2,#3]; cmp w10,#1; b.ne none;
        ldrb w10,[x2,#4]; b last;
        ordinary: ldr w10,[x1,#0x10]; cmp w10,#1; b.ne none; ldr w10,[x1,#0x14];
        last: cmp w10,#1; b.ne none;
        stp x19,x20,[sp,#-48]!; str x30,[sp,#16]; mov w20,w0;
        {cache_asm(L,'x19')} ldr w8,[x19,#0x244]; cbnz w8,ready;
        mov w0,w20; add x1,sp,#32; bl #0x410aa9f4; cbz w0,failed;
        ldr w0,[sp,#32]; add x1,sp,#24; bl #0x41015b30; cbz w0,failed;
        ldr x1,[sp,#24]; mov w0,w20; bl #0x410a93e0;
        ready: mov x0,x19; b done; failed: mov x0,#0;
        done: ldr x30,[sp,#16]; ldp x19,x20,[sp],#48; ret;
        none: mov x0,#0; ret;
        old: .word {int.from_bytes(original(0x410A9FBC,4)[0],'little')}; b #0x410a9fc0;''',0x400)
    emit('generation','mov x16,x8;'+decode_asm(L,'w19','custom','old','generation')+f'''
        custom: {cache_asm(L)} ldr w8,[x10,#0x244]; b done;
        old: ldr w8,[x16,#0x244]; done: cmp x22,#0; b #0x410a85c8;''',0x200)
    emit('cache_invalidate',f'''{c('x8',A('cache'))} mov w9,#128;
        clear: str wzr,[x8,#0x244]; add x8,x8,#0x248; subs w9,w9,#1; b.ne clear; ret;''',0x100)

    emit('validate',f'''
        {c('x8',A('header'))} mov x9,#0;
        header: ldr x10,[x0,x9]; ldr x11,[x8,x9]; cmp x10,x11; b.ne bad;
        add x9,x9,#8; cmp x9,#48; b.ne header;
        ldr w12,[x0,#64]; cmp w12,#{L.slots}; b.ne bad;
        mov x9,#68;
        schema: ldr w10,[x0,x9]; ldr w11,[x8,x9]; cmp w10,w11; b.ne bad;
        add x9,x9,#4; cmp x9,#128; b.ne schema;
        ldp w8,w9,[x0,#48]; cmp w8,#{L.slots}; b.hi bad; cmp w9,#{L.slots}; b.hi bad;
        ldp w12,w13,[x0,#56]; cbnz w12,bad; cmp w13,#1; b.hi bad;
        add x8,x0,#128; {c('x9',A('keys'))} mov w10,#0;
        record: ldr w11,[x8]; cmp w11,#1; b.hi bad;
        and w12,w10,#63; cmp w12,#{L.slots}; b.lo active; cbnz w11,bad;
        active: ldr x11,[x8,#4]; ldr x12,[x9]; cmp x11,x12; b.ne bad;
        ldr w11,[x8,#12]; ldr w12,[x9,#8]; cmp w11,w12; b.ne bad;
        add x9,x9,#12; add x8,x8,#0x400; add w10,w10,#1; cmp w10,#128; b.ne record;
        ldp w8,w9,[x0,#48]; cbz w8,second;
        sub w8,w8,#1; add x10,x0,#128; add x10,x10,x8,lsl #10;
        ldr w11,[x10]; cmp w11,#1; b.ne bad;
        second: cbz w9,good; ldr w12,[x0,#60]; add w9,w9,w12,lsl #6; sub w9,w9,#1;
        add x10,x0,#128; add x10,x10,x9,lsl #10; ldr w11,[x10]; cmp w11,#1; b.ne bad;
        good: mov w0,#1; ret; bad: mov w0,#0; ret;''',0x400)
    emit('decode_current',decode_asm(L,'w0','custom','builtin','decode')+f'''
        custom: cbnz w1,source_ok; cbnz w9,bad;
        source_ok: add w0,w8,#1; mov w1,w9; {record_asm(L)} ldr w8,[x10]; cmp w8,#1; cset w2,eq; ret;
        builtin: mov w0,#0; mov w2,#1; ret; bad: mov w2,#0; ret;''',0x200)
    emit('sync_current',f'''
        stp x19,x20,[sp,#-64]!; stp x21,x30,[sp,#16];
        {c('x0',0xA047027C0)} bl #0x40d2c674; cbz w0,bad;
        {c('x8',0xA02983CF8)} ldp w19,w20,[x8];
        {c('x0',0xA047027C0)} bl #0x40d2c660;
        mov w0,w19; mov w1,#0; bl #{E['decode_current']}; cbz w2,bad;
        str w0,[sp,#32]; str w1,[sp,#40];
        mov w0,w20; mov w1,#1; bl #{E['decode_current']}; cbz w2,bad;
        str w0,[sp,#36]; str w1,[sp,#44];
        {c('x0',A('load'))} {c('x1',A('records'))} {c('x2',L.record_bytes)} bl #0x4127a900;
        ldp x8,x9,[sp,#32]; {c('x10',A('load')+48)} stp x8,x9,[x10];
        {c('x0',A('load'))} bl #{E['validate']}; cbz w0,bad;
        ldp x8,x9,[sp,#32]; {c('x10',A('records')+48)} stp x8,x9,[x10];
        mov w0,#1; b done; bad: mov w0,#0;
        done: ldp x21,x30,[sp,#16]; ldp x19,x20,[sp],#64; ret;''',0x300)
    emit('save',f'''str x30,[sp,#-16]!; bl #{E['sync_current']}; cbz w0,bad;
        {c('x0',A('records'))} {c('x1',L.record_bytes)} bl #{E['save_callback']};
        {c('x8',L.record_bytes)} cmp x0,x8; cset w0,eq; b done;
        bad: mov w0,#0; done: ldr x30,[sp],#16; ret;''',0x100)
    emit('load',f'''str x30,[sp,#-16]!; {c('x0',A('load'))} {c('x1',L.record_bytes)} bl #{E['load_callback']};
        {c('x8',L.record_bytes)} cmp x0,x8; b.ne bad;
        {c('x0',A('load'))} bl #{E['validate']}; cbz w0,bad;
        {c('x0',A('records'))} {c('x1',A('load'))} {c('x2',L.record_bytes)} bl #0x4127a900;
        bl #{E['cache_invalidate']};
        {c('x8',A('records')+48)} ldr w0,[x8]; cbz w0,movie; ldr w1,[x8,#8]; mov w2,#0; bl #{E['select_full']}; cbz x0,bad;
        movie: {c('x8',A('records')+52)} ldr w0,[x8]; cbz w0,good; ldr w1,[x8,#8]; mov w2,#1; bl #{E['select_full']}; cbz x0,bad;
        good: mov w0,#1; b done; bad: mov w0,#0; done: ldr x30,[sp],#16; ret;''',0x300)
    for name in ('save_callback','load_callback'):emit(name,'mov x0,#0; ret;',0x40)
    spans=sorted((p['address'],p['address']+len(p['expected'])) for p in patches)
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('Source patch overlap')
    return dict(layout=L,entries=E,patches=patches,source_sha256=MAIN_SHA256,
        regions=[dict(address=C,data=bytes(code),permissions='rx'),dict(address=L.data_base,data=bytes(data),permissions='rw')],
        header=header,simulation_only=True)


class NativeSlots(legacy.PictureControl):
    """Unicorn contract fixture; environmental locks/file runtime are declared."""
    def __init__(self,source,layout=Layout()):
        self.source=_check_source(source);self.layout=layout;legacy._dependencies()
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
        from unicorn import arm64_const as r
        self.r=r;self.uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM);self.plan=build_plan(source,layout);self.entries=self.plan['entries']
        self.executed,self.services,self.last_trace=set(),[],[]
        self.uc.mem_map(BASE,(len(source)+4095)&~4095);self.uc.mem_write(BASE,source)
        self.uc.mem_map(0xA02800000,0x900000);self.uc.mem_write(0xA02808000,source[0x4B13000:0x4DC0000])
        self.uc.mem_map(0xA03B00000,0x1400000);self.uc.mem_map(0x46000000,0x100000)
        for region in self.plan['regions']:
            self.uc.mem_map(region['address'],len(region['data']));self.uc.mem_write(region['address'],region['data'])
        for patch in self.plan['patches']:
            if bytes(self.uc.mem_read(patch['address'],len(patch['expected'])))!=patch['expected']:raise ValueError('Original bytes changed')
            self.uc.mem_write(patch['address'],patch['replacement'])
        self.uc.reg_write(r.UC_ARM64_REG_CPACR_EL1,3<<20);self._hook=self.uc.hook_add(UC_HOOK_CODE,self._observe)

    def _input(self,blob):
        blob=bytes(blob)
        if not 4<=len(blob)<=4096:raise ValueError('NP3 length')
        offset=4
        while offset<len(blob):
            if offset+8>len(blob):
                # Original zero-key terminator (0x410add9c cbz): 4..7 zero bytes only.
                if len(blob)-offset>=4 and not any(blob[offset:]):break
                raise ValueError('TLV header')
            _,length=struct.unpack_from('>II',blob,offset);offset+=8+length
            if offset>len(blob):raise ValueError('TLV data')
        self.uc.mem_write(self.layout.address('input'),blob)
        return len(blob)

    def import_np3(self,slot,blob,mode=0):
        self.layout.validate(slot,mode);length=self._input(blob)
        if self.call(self.entries['import_np3'],self.layout.address('input'),length,slot,mode)!=1:raise ValueError('Native NP3 import failed')
        return self.select(slot,mode)

    def select(self,slot,mode=0):
        self.layout.validate(slot,mode)
        if not self.call(self.entries['select'],slot,mode):raise ValueError('Empty or invalid slot')
        return self.record(slot,mode)

    def record(self,slot,bank):return bytes(self.uc.mem_read(self.layout.record(slot,bank),0x400))
    def records(self):return bytes(self.uc.mem_read(self.layout.address('records'),self.layout.record_bytes))

    def current_selection(self,mode=0):
        address=self.layout.address('scratch')
        if self.call(0x410A7B58 if mode==0 else 0x410A7D10,address,address+0x100)!=1:raise ValueError('Native current getter failed')
        return struct.unpack('<I',self.uc.mem_read(address,4))[0],bytes(self.uc.mem_read(address+0x100,0x158))

    def slots(self,mode=0):
        self.call(self.entries['list'],mode)
        total=struct.unpack('<I',self.uc.mem_read(0xA02983CB8+mode*24,4))[0]
        if not 31<=total<=31+self.layout.slots:raise ValueError('Invalid published count')
        codes=struct.unpack('<'+'I'*(total-31),self.uc.mem_read(self.layout.address(f'list{mode}')+0x7C,(total-31)*4))
        return [code-(27 if code<=36 else 30) for code in codes]

    def guards_intact(self):
        return all(bytes(self.uc.mem_read(self.layout.address(f'list{bank}')+0x3F0,16))==b'SLOT-LIST-GUARD!' for bank in (0,1))
