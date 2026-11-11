# SPDX-License-Identifier: GPL-2.0-or-later
"""Compose C32/C64 simulation images with all code and initial data inside MAIN.

Derived from .publish-20261029/slots/tools/z8_slots_image.py. The external loader
regions are gone: code is linked at Layout.core_code..; a one-shot hook at the
point the host used to call the original mapper (0x4016e6a8) now maps the data
reservation RW/NX with the same arguments and unpacks the initial data from MAIN.
No Nikon update container is produced.
"""
import hashlib
import struct
from z8_slots_layout import Layout
from z8_slots_core import build_plan
from z8_slots_imenu import build_menu_plan
from z8_slots_main_menu import build_main_menu_plan
from z8_slots_labels import build_label_plan
from z8_c10_memory import reservation_patch
from z8_c10_image import source_offset
from z8_picture_control import _assemble,_constant as c

HOOK=0x4016E6A8
HOOK_INSN=bytes.fromhex('15cb91d2')          # mov x21,#0x8e58
MAPPER,MMU_CONTEXT=0x40172B88,0xA04B58D00
COPY_END_SITE=0x41184D8C
STOCK_COPY_END_BYTES=bytes.fromhex('081e9fd26811a8f2')
STOCK_COPY_END=0x408BF8F0


def copy_end_bytes(end):
    if end&0xFFF or not STOCK_COPY_END<=end<=Layout.section_end:raise ValueError('Copy end')
    return struct.pack('<II',0xD2800008|((end&0xFFFF)<<5),0xF2A00008|((end>>16)<<5))


def decode_copy_end(raw):
    low,high=struct.unpack('<II',raw)
    if low&0xFFE0001F!=0xD2800008 or high&0xFFE0001F!=0xF2A00008:raise ValueError('Unexpected copy-end instructions')
    return (low>>5&0xFFFF)|(high>>5&0xFFFF)<<16


def data_spans(data,gap=64):
    """Nonzero 8-byte-aligned spans; boot init clears the whole reservation before copying them."""
    spans=[];start=None;last=None
    for at in range(0,len(data),8):
        if any(data[at:at+8]):
            if start is None:start=at
            elif at-last>gap:spans.append((start,last-start));start=at
            last=at+8
    if start is not None:spans.append((start,last-start))
    return spans


def build_boot_init(L,data):
    spans=data_spans(data)
    table=b''.join(struct.pack('<II',a,n) for a,n in spans)+bytes(8)
    payload=b''.join(data[a:a+n] for a,n in spans)
    address=L.boot_init;capacity=0x200
    table_at=address+capacity;payload_at=table_at+len(table)
    # DRAM taken from the General heap tail is not zero on hardware, and hooks treat
    # nonzero words there (e.g. params) as live state, so clear it before the copy.
    code=_assemble(f'''stp x19,x30,[sp,#-16]!;
        {c('x0',MMU_CONTEXT)} {c('x1',L.data_base)} {c('x2',L.reserved_bytes)} {c('x3',L.data_base)}
        mov x4,#0xc; mov x5,#1; mov w6,#-1; bl #{MAPPER}; cbnz x0,fail;
        {c('x9',table_at)} {c('x15',payload_at)} {c('x10',L.data_base)}
        mov x13,x10; {c('x12',L.reserved_bytes)}
        clear: stp xzr,xzr,[x13],#16; subs x12,x12,#16; b.ne clear;
        next: ldp w11,w12,[x9],#8; cbz w12,done; add x13,x10,x11;
        copy: ldr x14,[x15],#8; str x14,[x13],#8; subs w12,w12,#8; b.ne copy; b next;
        done: ldp x19,x30,[sp],#16; .word {int.from_bytes(HOOK_INSN,'little')}; b #{HOOK+4};
        fail: brk #0x323;''',address)
    if len(code)>capacity:raise ValueError('Boot init exceeds its slot')
    blob=code.ljust(capacity,b'\0')+table+payload
    if len(blob)%8:blob+=bytes(8-len(blob)%8)
    if address+len(blob)>L.section_end:raise ValueError('Init image exceeds the text section')
    return blob,dict(spans=len(spans),payload_bytes=len(payload),blob_bytes=len(blob))


def compose(source,layout=Layout(),*,extend_copy_page=True):
    L=layout
    core=build_plan(source,L)
    plans=dict(core=core,imenu=build_menu_plan(source,L),labels=build_label_plan(source,L),
               main_menu=build_main_menu_plan(source,L,core_plan=core,extend_copy_page=extend_copy_page))
    memory=reservation_patch(source)
    memory['patch']=dict(memory['patch'],replacement=struct.pack('<I',L.general_bytes),
        name='C32/C64 simulation reservation: remove General heap tail 1 MiB')
    metadata=memory['metadata']
    metadata['general_after']=dict(address=hex(L.general_base),bytes=L.general_bytes,end=hex(L.data_base))
    metadata['reserved']=dict(address=hex(L.data_base),bytes=L.reserved_bytes,end=hex(L.data_base+L.reserved_bytes))
    patches=[p for plan in plans.values() for p in plan['patches']]+[memory['patch']]
    data=bytearray(next(r['data'] for r in core['regions'] if r['permissions']=='rw'))
    regions=[]
    for name,plan in plans.items():
        for r in plan['regions']:
            if r['permissions']=='rx':regions.append(dict(r,data=bytearray(r['data'])))
            elif name!='core':
                at=r['address']-L.data_base;size=len(r['data'])
                if not 0<=at<=len(data)-size or any(data[at:at+size]):
                    raise ValueError('Owned data overlay overlaps initialized state')
                data[at:at+size]=r['data']
    spans=sorted((r['address'],r['address']+len(r['data'])) for r in regions)
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('Overlapping code regions')
    for start,end in spans:
        if not L.code_base<=start<end<=L.code_base+L.code_bytes:raise ValueError('Code exceeds its reservation')
    if L.code_base+L.code_bytes>L.boot_init:raise ValueError('Code overlaps boot init')
    blob,init=build_boot_init(L,bytes(data))
    if source[HOOK-0x40160000:HOOK-0x40160000+4]!=HOOK_INSN:raise ValueError('Boot hook site changed')
    patches.append(dict(address=HOOK,expected=HOOK_INSN,replacement=_assemble(f'b #{L.boot_init}',HOOK),
        name='One-shot early data mapping and unpack'))
    for r in regions:
        trimmed=bytes(r['data']).rstrip(b'\0') or b'\0'
        patches.append(dict(address=r['address'],expected=bytes(len(trimmed)),replacement=trimmed,name='Owned code in text section'))
    patches.append(dict(address=L.boot_init,expected=bytes(len(blob)),replacement=blob,name='Boot init and initial data image'))
    copy_end=(L.boot_init+len(blob)+0xFFF)&~0xFFF
    image=bytearray(source);offsets=[]
    for p in patches:
        old,new=p['expected'],p['replacement']
        if not old or len(old)!=len(new):raise ValueError('Patch length changed')
        off=source_offset(p['address'],len(old),len(source))
        if p.get('source_offset',off)!=off or source[off:off+len(old)]!=old:raise ValueError(f"Original patch byte mismatch {p['address']:#x}")
        p['source_offset']=off;offsets.append((off,off+len(old)));image[off:off+len(old)]=new
    offsets.sort()
    if any(a[1]>b[0] for a,b in zip(offsets,offsets[1:])):raise ValueError('Overlapping source patches')
    off=COPY_END_SITE-0x40160000
    if source[off:off+8]!=STOCK_COPY_END_BYTES:raise ValueError('Copy end site changed')
    image[off:off+8]=copy_end_bytes(copy_end)
    return dict(layout=L,plans=plans,patches=patches,regions=regions,image=bytes(image),data=bytes(data),
        init=init,copy_end=copy_end,memory_reservation=metadata,
        source_sha256=hashlib.sha256(source).hexdigest())


def overlay(base,result):
    """Lay the guarded patches over an already-derived MAIN; refuse any collision."""
    image=bytearray(base)
    if len(image)!=len(result['image']):raise ValueError('Base size differs')
    for p in result['patches']:
        at,old=p['source_offset'],p['expected']
        if bytes(image[at:at+len(old)])!=old:raise ValueError(f"Base differs at {p['address']:#x} ({p['name']})")
        image[at:at+len(old)]=p['replacement']
    off=COPY_END_SITE-0x40160000
    prior=decode_copy_end(bytes(image[off:off+8]))
    if result['copy_end']<prior:raise ValueError(f'Copy end would shrink from {prior:#x}')
    image[off:off+8]=copy_end_bytes(result['copy_end'])
    return bytes(image),prior
