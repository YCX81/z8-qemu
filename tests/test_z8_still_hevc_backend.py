# SPDX-License-Identifier: GPL-2.0-or-later
"""Full photo producer -> packed10 RAM -> x265 -> real lane/T DMA and GIC.

No firmware execution or host receipt injection. Native MMIO snapshots are
fixtures; C1 pixel/hash provenance is generated through actual device writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import time
from unittest.mock import patch
import numpy as np
import test_z8_hlg_pipeline as pipeline
import test_z8_hlg_thumbnail as thumbnail
from test_z8_producer_journal import require
from z8_still_hevc_fixture import RESET,INIT

BASE=0x16020000;STATE='z8-still-hevc-state';CODEC='z8-still-hevc-codec-state'
OPTION='z8-still-hevc-x265-model'

class Lab(pipeline.Lab):
    def __init__(self,binary,folder,c1,enabled):
        super().__init__(binary,folder,True,c1)
        self.command[self.command.index('-M')+1]+=','+'z8-still-hevc-model=on'
        if enabled:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'

def write_sequence(g,values):
    for off,value in values:g.write(BASE+off,value)

def wait(g):
    until=time.monotonic()+180
    while True:
        s=g.state(CODEC)
        if not s['busy']:return s
        require(time.monotonic()<until,'Bounded encoder completion')
        time.sleep(.02)

def saved(g,pa,size,label):
    dest=g.root/label
    g.qmp.command('human-monitor-command',**{'command-line':f'pmemsave {pa:#x} {size} "{dest}"'})
    data=dest.read_bytes();require(len(data)==size,'Complete RAM receipt')
    return data

def check_tile_inputs(g,source,receipts):
    arrays=[]
    for key,height in (('y',5504),('uv',2752)):
        raw=saved(g,source[key],source[key+'_bytes'],'input-'+key+'.bin')
        require(hashlib.sha256(raw).hexdigest()==source[key+'_sha256'],'Independent full input RAM hash')
        arrays.append(np.frombuffer(raw,dtype='<u4').reshape(height,2752))
    def unpack(words):
        decoded=np.empty((words.shape[0],words.shape[1]*3),dtype='<u2')
        for component in range(3):decoded[:,component::3]=(words>>(10*component))&1023
        return decoded
    for i,r in enumerate(receipts):
        row,col=divmod(i,11);width=min(768,8256-col*768);x=col*256
        y=unpack(arrays[0][row*2752:(row+1)*2752,x:x+width//3])
        uv=unpack(arrays[1][row*1376:(row+1)*1376,x:x+width//3])
        y=np.pad(y,((0,0),(0,768-width)),mode='edge')
        u=np.pad(uv[:,::2],((0,0),(0,(768-width)//2)),mode='edge')
        v=np.pad(uv[:,1::2],((0,0),(0,(768-width)//2)),mode='edge')
        digest=hashlib.sha256(y.tobytes()+u.tobytes()+v.tobytes()).hexdigest()
        require(digest==r['input_sha256'],'Every padded I420 tile comes from actual packed10 RAM')

def check_result(g,out):
    s=wait(g);require(s['jobs']==1 and s['result_valid'] and not s['failed'],'Real encoder result, not synthetic completion')
    require(s['source_c1_job']==s['source_frame']==1,'Producer job/frame binding')
    source=g.state(pipeline.STATE)['outputs'][0]
    require(s['input_sha256']==[source['y_sha256'],source['uv_sha256']],'Actual C1 output hash binding')
    check_tile_inputs(g,source,s['tiles'])
    require(s['profile_idc'] in (2,4),'Parsed supported PTL')
    core=g.state(STATE);require(core['pending']==0x60000,'Both real completed stages pending')
    require(g.tx(f'readl {BASE+0x920:#x}')==0x2000c,'Internal 12x2 geometry')
    for off in (0x900,0x904):require(g.tx(f'readl {BASE+off:#x}')==0x90009,'Nine actual records per lane')
    require(g.tx(f'readl {BASE+0x110:#x}')==0,'Retired finite DMA')
    reg=core['registers'];images={};params=None;total=0
    for lane,row in enumerate(s['lanes']):
        pa=reg[f'0x{0xe40+lane*8:03x}']['value']<<4
        tp=reg[f'0x{0xe90+lane*8:03x}']['value']<<4
        data=saved(g,pa,row['bytes'],f'lane{lane}.bin');table=saved(g,tp,4096,f'table{lane}.bin')
        require(hashlib.sha256(data).hexdigest()==row['sha256'],'Actual stream hash')
        require(hashlib.sha256(table).hexdigest()==row['table_sha256'],'Actual table hash')
        require(g.tx(f'readl {BASE+0xf60+lane*4:#x}')==len(data),'Lane report contains actual staged bytes')
        ps=[]
        for i in range(9):
            typ,length,offset,reserved=struct.unpack_from('<4I',table,i*16)
            require(reserved==0 and 6<=length and offset+length<=len(data),'T fields inside committed span')
            nal=data[offset:offset+length]
            require(nal[:4]==b'\0\0\0\1' and nal[4]>>1==typ and nal[5]==1,'T truthfully matches actual NAL header')
            if i<3:
                require(typ==32+i,'Exactly one VPS/SPS/PPS group per lane');ps.append(nal)
            else:
                require(typ==19,'Actual transformed image type19')
                slot=lane+4*(i-3);tile=(slot//12)*11+min(slot%12,10)
                r=s['tiles'][tile];require(r['nal_header_transform']=={'from':20,'to':19},'Declared transformation')
                require(hashlib.sha256(nal).hexdigest()==r['dma_nal_sha256'],'DMA NAL receipt')
                raw=nal if r['raw_prefix']==4 else nal[1:]
                require(hashlib.sha256(raw).hexdigest()==r['after_sha256'],'Raw transformed NAL hash')
                original=bytearray(raw);original[r['raw_prefix']]=0x28
                require(hashlib.sha256(original).hexdigest()==r['before_sha256'],'Exactly one header byte differs')
                require(r['single_picture_no_leading_verified'],'One actual encoded picture per tile')
                images[slot]=nal
        require(not any(table[9*16:]),'Unused table bytes deterministic zero')
        require(params is None or params==ps,'All parameter sets identical');params=ps
        total+=len(data)+4096
        (out/f'lane{lane}.bin').write_bytes(data);(out/f'table{lane}.bin').write_bytes(table)
    require(len(images)==24 and images[11]==images[10] and images[23]==images[22],'Declared invisible slot reuse')
    visible=[images[i] for i in range(24) if i%12<11]
    require(len(visible)==22 and total==s['dma_bytes'],'Complete visible grid, actual DMA accounting')
    for i,nal in enumerate(visible):(out/f'tile{i:02}.h265').write_bytes(b''.join(params)+nal)
    return s

def run_case(args,name):
    dest=args.output/name;dest.mkdir(parents=True,exist_ok=False)
    result=dict(case=name,passed=False,native_mmio_fixture=True,firmware_executed=False)
    c1=json.loads(args.c1_snapshot.read_text());snapshot=json.loads(args.snapshot.read_text())
    words=[(int(k,16),v['value']) for k,v in snapshot['device_states'][STATE]['registers'].items() if v['request_written']]
    regs=dict(words);y=regs[0xf00]+(regs[0xf04]<<32);uv=regs[0xf08]+(regs[0xf0c]<<32)
    targets=((y,uv),*pipeline.TARGETS[1:])
    with tempfile.TemporaryDirectory(prefix='z8-encode-') as tmp:
        g=Lab(args.qemu,Path(tmp),c1,name!='default-off')
        try:
            g.start()
            with patch.object(pipeline,'TARGETS',targets):
                pipeline.producer(g);pipeline.configure(g)
                g.write(0x15432004,0x101);g.write(0x15180030,1)
            g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
            require(g.state(pipeline.STATE)['jobs']==1 and not g.state(pipeline.STATE)['owned'],'Actual C1 source retired')
            write_sequence(g,RESET+INIT+words)
            for lane in range(4):
                g.write_ram(regs[0xe40+lane*8]<<4,b'\xa5'*64)
                g.write_ram(regs[0xe90+lane*8]<<4,b'\xa5'*64)
            if name=='capacity':
                g.write(BASE+0xe60,4096);g.expected_unknown=BASE+0x100;g.write(BASE+0x100,2)
                require(not g.state(STATE)['prepared'],'Insufficient configured capacity rejected before START')
            else:
                if name=='profile':g.write(BASE+0x704,13)
                g.write(BASE+0x100,2)
                if name=='source':g.write_ram(y,bytes([g.read_ram(y,1)[0]^1]))
                if name in ('default-off','source','profile'):g.expected_unknown=BASE+0x100
                g.write(BASE+0x100,1)
                if name=='abort':
                    require(g.state(CODEC)['busy'],'Exercise an in-flight abort')
                    write_sequence(g,[(0x128,0),(0xfe8,0),(8,0),(0xff4,1)])
                    s=wait(g);require(s['cancels']==1 and not s['result_valid'],'Cancel cannot publish stale completion')
                elif name=='changed-inflight':
                    require(g.state(CODEC)['busy'],'Exercise source mutation during encode')
                    g.expected_unknown=BASE+0x100;g.write_ram(y,bytes([g.read_ram(y,1)[0]^1]))
                    s=wait(g);require(s['failed'] and 'source-hash' in s['last_error'],'Commit rechecks actual source')
                elif name in ('encode','mask','repeat'):
                    if name=='mask':g.write(BASE+0x128,0)
                    s=check_result(g,dest)
                    if name=='mask':
                        require(not g.irq(0xfa),'Mask suppresses line, not real completion pending')
                        g.write(BASE+0x128,regs[0x128])
                    require(g.irq(0xfa),'Real completion IRQ')
                    g.write(BASE+0x120,0);require(g.irq(0xfa),'W1C zero preserves both stages')
                    g.write(BASE+0x120,0x40000);require(g.irq(0xfa) and g.state(STATE)['pending']==0x20000,'Stage1 ACK leaves stage2')
                    g.write(BASE+0x120,0x20000);require(not g.irq(0xfa),'Stage2 ACK deasserts IRQ')
                    s=g.state(CODEC);require(s['stage1_acks']==s['stage2_acks']==1,'Separate stage receipts')
                    if name=='repeat':
                        g.expected_unknown=BASE+0x100;g.write(BASE+0x100,1)
                        require('Z9_UNKNOWN' in g.text(),'Duplicate START is strictly rejected')
                        require(g.state(CODEC)['jobs']==1,'Repeated START cannot reuse producer')
                    result['codec']=s
            if name not in ('encode','mask','repeat'):
                s=wait(g);require(s['jobs']==s['dma_bytes']==0 and not g.irq(0xfa),'No fake DMA/IRQ')
                for lane in range(4):
                    require(g.read_ram(regs[0xe40+lane*8]<<4,64)==b'\xa5'*64,'Failure preserves output sentinels')
                    require(g.read_ram(regs[0xe90+lane*8]<<4,64)==b'\xa5'*64,'Failure preserves T sentinels')
                if name!='abort':require('Z9_UNKNOWN' in g.text(),'Failure uses strict UNKNOWN')
                result['codec']=s
            result['protocol']=g.state(STATE)
            require(not g.qmp.command('query-status')['running'],'No firmware execution')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();(dest/'stderr.txt').write_text(g.text())
            (dest/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(name,result['passed'],result.get('error',''),flush=True)
    return result

def run_sdr(args,enabled):
    name='sdr-on' if enabled else 'sdr-off';dest=args.output/name;dest.mkdir(parents=True,exist_ok=False)
    result=dict(case=name,passed=False,firmware_executed=False)
    with tempfile.TemporaryDirectory(prefix='z8-hevc-sdr-') as tmp:
        g=Lab(args.qemu,Path(tmp),json.loads(args.c1_snapshot.read_text()),enabled)
        g.command[g.command.index('-M')+1]+=','+','.join(x+'=on' for x in (
            'engine1542-ram-input-control-model','capture-thumbnail-nv16-model','z8-capture-hlg-thumbnail-model'))
        try:
            g.start();result['hashes']=thumbnail.sdr_thumbnail(g)
            result['codec']=g.state(CODEC)
            require(result['codec']['jobs']==result['codec']['dma_bytes']==0 and not g.irq(0xfa),'SDR never starts HEVC')
            require(not g.qmp.command('query-status')['running'],'Stopped CPU')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();(dest/'stderr.txt').write_text(g.text())
            (dest/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(name,result['passed'],flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qemu','snapshot','c1-snapshot','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    require(hashlib.sha256(args.snapshot.read_bytes()).hexdigest()=='274db49ac0a56223bc5fb2fd9ce1ebe6da7dd3e507f92bbc49b785cd99a854ac','Pinned run06 snapshot')
    rows=[run_case(args,name) for name in ('encode','mask','repeat','default-off','capacity','source','profile','abort','changed-inflight')]
    rows += [run_sdr(args,enabled) for enabled in (False,True)]
    equivalent=all(r['passed'] for r in rows[-2:]) and rows[-2].get('hashes')==rows[-1].get('hashes')
    summary=dict(passed=all(r['passed'] for r in rows) and equivalent,sdr_equivalent=equivalent,cases=rows,
                 firmware_executed=False,native_mmio_fixture=True,binary_sha256=hashlib.sha256(args.qemu.read_bytes()).hexdigest())
    (args.output/'qtest-result.json').write_text(json.dumps(summary,indent=2)+'\n')
    if not summary['passed']:raise SystemExit(1)
if __name__=='__main__':main()
