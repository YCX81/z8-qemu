# SPDX-License-Identifier: GPL-2.0-or-later
"""Compose guarded C32/C64 simulation images; no Nikon update container."""
import hashlib
import json
from pathlib import Path
import struct
from z8_slots_layout import Layout
from z8_slots_core import build_plan
from z8_slots_imenu import build_menu_plan
from z8_slots_main_menu import build_main_menu_plan
from z8_slots_labels import build_label_plan
from z8_slots_media import build_media_code,SAVE,LOAD
from z8_c10_memory import reservation_patch
from z8_c10_image import source_offset
from z8_picture_control import _assemble


def compose(source,layout=Layout(),*,extend_copy_page=False):
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
    media=build_media_code();regions.append(dict(media,data=bytearray(media['data'])))
    regions.append(dict(address=L.data_base,data=data,permissions='rw'))
    for name,target in (('save_callback',SAVE),('load_callback',LOAD)):
        address=core['entries'][name]
        owner=next(r for r in regions if r['address']<=address<r['address']+len(r['data']))
        at=address-owner['address'];owner['data'][at:at+4]=_assemble(f'b #{target}',address)
    spans=sorted((r['address'],r['address']+len(r['data'])) for r in regions)
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('Overlapping loader regions')
    for r in regions:
        start,end=r['address'],r['address']+len(r['data'])
        if r['permissions']=='rw':valid=start==L.data_base and end==L.data_base+L.reserved_bytes
        else:valid=L.code_base<=start<end<=L.code_base+L.code_bytes
        if not valid:raise ValueError('Loader exceeds the explicit RX/RW reservation')
    image=bytearray(source);offsets=[]
    for p in patches:
        old,new=p['expected'],p['replacement']
        if not old or len(old)!=len(new):raise ValueError('Patch length changed')
        off=source_offset(p['address'],len(old),len(source))
        if p.get('source_offset',off)!=off or source[off:off+len(old)]!=old:raise ValueError('Original patch byte mismatch')
        p['source_offset']=off;offsets.append((off,off+len(old)));image[off:off+len(old)]=new
    offsets.sort()
    if any(a[1]>b[0] for a,b in zip(offsets,offsets[1:])):raise ValueError('Overlapping source patches')
    return dict(layout=L,plans=plans,patches=patches,regions=regions,image=bytes(image),
        media=media,memory_reservation=metadata,source_sha256=hashlib.sha256(source).hexdigest())


def build_experiment(source,output,layout=Layout(),*,extend_copy_page=False):
    result=compose(source,layout,extend_copy_page=extend_copy_page);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    image=output/'simulation-main-not-an-update.bin';image.write_bytes(result['image'])
    loaders=[]
    for index,region in enumerate(result['regions']):
        path=output/f'owned-region-{index}.bin';path.write_bytes(region['data'])
        loaders.append(dict(address=region['address'],path=path,bytes=len(region['data']),sha256=hashlib.sha256(region['data']).hexdigest()))
    metadata=dict(simulation_only=True,camera_update_container=False,slots_per_bank=layout.slots,reserved_capacity=64,
        extend_copy_page=extend_copy_page,
        memory_reservation=result['memory_reservation'],original_sha256=result['source_sha256'],
        derived_sha256=hashlib.sha256(result['image']).hexdigest(),
        source_patches=[dict(address=hex(p['address']),source_offset=hex(p['source_offset']),name=p['name'],
            expected=p['expected'].hex(),replacement=p['replacement'].hex()) for p in result['patches']],
        loader_regions=[dict(r,address=hex(r['address']),path=r['path'].name) for r in loaders],
        state_scope='New isolated laboratory NAND; no physical camera setting migration',
        slot_map=[dict(bank=b,slot=s,selector=layout.selector(s,b),code=layout.code(s),keys=layout.keys(s,b))
            for b in (0,1) for s in range(1,layout.slots+1)])
    (output/'experiment.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return dict(result,derived_main=image,loader_regions=loaders,metadata=metadata)
