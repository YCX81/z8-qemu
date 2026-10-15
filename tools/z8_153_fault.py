# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in, read-only capture at an existing153 strict fault; no breakpoints.

The caller pins C3.11 via the HEVC observer. A complete snapshot is not an
accepted153 processing contract or a hardware/algorithm verification.
"""
import hashlib,json,re
from pathlib import Path

PROGRAMS=(0xa02c97500,0xa02c97d00)
PROGRAM_BYTES=0x7c0
STATES=('engine153-config-state','engine153-frame-pass-state','engine153-inplace-identity-state')
MAX_QOM=256*1024;MAX_SINGLE_QOM=128*1024;MAX_RAM=8192

def need(ok,message):
    if not ok:raise ValueError(message)

def capture(g,first_unknown,cpu_registers=None):
    if not getattr(g,'_still_153_observer_enabled',False):return None
    if not re.search(r'\baddr=0x153[48][0-9a-fA-F]{4}\b',first_unknown or ''):return None
    folder=Path(g.out)/'153-fault';folder.mkdir(exist_ok=False)
    row=dict(collection_complete=False,guest_state_modified=False,firmware_executed_by_observer=False,
        scope='Existing strict stop; original MMIO shadow and RAM programs only; no accepted processing contract',
        first_unknown=first_unknown,states={},programs=[],ram_bytes=0,qom_bytes=0)
    def qom(name):
        raw=g.qmp.command('qom-get',path='/machine',property=name)
        text=raw if isinstance(raw,str) else json.dumps(raw)
        n=len(text.encode());row['qom_bytes']+=n
        need(n<=MAX_SINGLE_QOM and row['qom_bytes']<=MAX_QOM,'153 QOM budget')
        obj=json.loads(text);need(isinstance(obj,dict),'153 QOM object required');return obj
    def read(pa,size):
        need(type(pa) is int and 0<size<=512 and 0xa00000000<=pa<=0xa20000000-size,'153 RAM address bound')
        need(row['ram_bytes']+size<=MAX_RAM,'153 RAM budget');row['ram_bytes']+=size
        part=g.read_bytes(pa,size);need(len(part)==size,'153 short program/context read');return part
    try:
        need(g.qmp.command('query-status').get('running') is False,'153 snapshot requires stopped VM')
        names=list(STATES)
        props=getattr(g,'data',{}).get('capture_device_hypotheses',{}).get('properties',[])
        if 'z8-heif-1534-inplace-model=on' in props:names.append('z8-heif-1534-state')
        row['states']={n:qom(n) for n in names}
        cfg=row['states']['engine153-config-state'];banks=cfg.get('banks')
        need(isinstance(banks,list) and len(banks)==2,'153 two banks required')
        for bank,b in enumerate(banks):
            c=b.get('fields');need(isinstance(c,list) and len(c)==84 and
                all(type(v) is int and 0<=v<=0xffffffff for v in c),'153 register array')
            pa=c[0x48//4]|(c[0x4c//4]<<32)
            if not pa:
                row['programs'].append(dict(bank=bank,program_not_configured=True));continue
            need(pa==PROGRAMS[bank],'153 foreign program pointer')
            data=bytearray()
            for at in range(0,PROGRAM_BYTES,512):
                size=min(512,PROGRAM_BYTES-at)
                data.extend(read(pa+at,size))
            name=f'bank{bank}-program.bin'
            with (folder/name).open('xb') as f:f.write(data)
            row['programs'].append(dict(bank=bank,address=hex(pa),bytes=len(data),path=name,
                sha256=hashlib.sha256(data).hexdigest()))
        hit=re.search(r'\bcpu=([0-3])\b',first_unknown or '')
        if cpu_registers is not None and hit:
            text=cpu_registers.get(hit[1]);need(isinstance(text,str),'153 fault CPU registers missing')
            regs={k:int(v,16) for k,v in re.findall(r'\b(SP|X23|X30)=([0-9a-fA-F]+)',text)}
            need('SP' in regs and not regs['SP']&15,'153 fault stack alignment')
            row['cpu']=int(hit[1]);row['registers_text']=text
            for name,pa,size in [('stack',regs['SP'],256)]+(
                [('sync_request',regs['X23'],0x134)] if regs.get('X30')==0x40cba4d4 and 'X23' in regs else []):
                raw=read(pa,size);row[name]=dict(address=hex(pa),bytes=size,hex=raw.hex(),sha256=hashlib.sha256(raw).hexdigest())
        need(qom('engine153-config-state')==cfg,'153 configuration changed during capture')
        row['collection_complete']=True
    except Exception as e:
        row['error']=repr(e);raise
    finally:
        with (folder/'state.json').open('x',encoding='utf-8') as f:json.dump(row,f,indent=2);f.write('\n')
    return row
