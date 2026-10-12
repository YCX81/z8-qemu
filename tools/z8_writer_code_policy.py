# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only physical-region and HMP parsing policy for the pinned original-image runtime."""
import json
import re

IMAGE_RANGES=((0x840000000,0x8adf8da00),(0x8adf8dc00,0x8fff47400))


class MappingError(RuntimeError):pass


def parse_translation(reply):
    row={'raw_reply':reply,'state':'invalid_reply','pa':None}
    if not isinstance(reply,str) or len(reply)>4096:return row
    if reply.rstrip('\r\n')=='Unmapped':row['state']='unmapped';return row
    if reply.rstrip('\r\n')=='No cpu':row['state']='no_cpu';return row
    m=re.fullmatch(r'gpa: (0x[0-9a-fA-F]+|0)\r?\n?',reply)
    if m:
        value=int(m[1],0)
        if 0<=value<1<<64:row.update(state='mapped',pa=value)
    return row


def safe_regions(tree,ram_bytes,banks):
    """Use only physical address_space_memory's rendered flatview.

    Printed endpoints are inclusive (QEMU MR_SIZE is size-1). Whitelist is
    bound to the15-file verified0ff source snapshot, then intersected with
    actual QMP RAM geometry and flatview region names/types, never addresses
    alone. RAM devices and I/O, including NOR with ROMD disabled, are excluded.
    """
    if not isinstance(tree,str) or len(tree)>1<<20:return [],['mtree output absent/too large']
    blocks=re.split(r'(?m)^FlatView #\d+\r?\n',tree)
    blocks=[b for b in blocks if re.search(r'(?m)^ AS "memory", root: system\r?$',b)]
    if len(blocks)!=1:return [],['No unique physical memory flatview; reads disabled']
    names={'z8.bringup-work-ram':(0xa00000000,0xa00000000+ram_bytes,{'ram'}),
           'z9.main-image':(0x40000000,0x48000000,{'ram','rom','romd'})}
    if banks:
        names.update({f'z9.image-bank-{i}':(a,b,{'ram'}) for i,(a,b) in enumerate(IMAGE_RANGES)})
    region_re=re.compile(r'^\s+([0-9a-fA-F]{8,16})-([0-9a-fA-F]{8,16}) '
        r'\(prio -?\d+, ([a-z/\-]+)\): ([\w.\-]+)(?: @[0-9a-fA-F]{8,16})?(?: (?:tcg|kvm))*\r?$')
    regions=[]
    for line in blocks[0].splitlines():
        m=region_re.fullmatch(line)
        if not m:continue
        lo,hi,kind,name=int(m[1],16),int(m[2],16)+1,m[3],m[4]
        if name not in names:continue
        low,high,kinds=names[name]
        if kind in kinds and low<=lo<hi<=high:
            regions.append(dict(base=lo,end=hi,kind=kind,name=name,mtree_line=line))
    warnings=[] if regions else ['No recognized ordinary RAM/ROMD regions; reads disabled']
    return regions,warnings


def decode(value):return json.loads(value) if isinstance(value,str) else value


def cpu_state(g,cpu):
    raw=g.qmp.hmp('info registers',cpu)
    if not isinstance(raw,str) or len(raw)>65536:raise MappingError('Invalid bounded CPU register output')
    fields={key:int(value,16) for key,value in re.findall(r'\b(PC|SP|PSTATE|X\d\d)=([0-9a-fA-F]+)',raw)}
    return dict(raw_reply=raw,fields=fields,pc=fields.get('PC'),pstate=fields.get('PSTATE'))
