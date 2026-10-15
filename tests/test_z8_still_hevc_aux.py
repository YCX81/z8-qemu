# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite native preview + static small-image profiles, real producer/RAM/x265.

No firmware executes. The 320x212 request is a static candidate, not a native
request fixture; only its C1 source addresses were observed in run07.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from unittest.mock import patch
import numpy as np
import test_z8_still_hevc_backend as backend
from test_z8_still_hevc_backend import BASE, STATE, CODEC, Lab, wait, saved, write_sequence
from test_z8_producer_journal import require
from z8_still_hevc_fixture import RESET, INIT
import test_z8_hlg_pipeline as pipeline

RUN07='341ab2ba7d0ba91ec8d92464fc33eeea0825cb6f86d9bdcea32d6752723d58d4'
RUN06='274db49ac0a56223bc5fb2fd9ce1ebe6da7dd3e507f92bbc49b785cd99a854ac'

def profile(snapshot, small=False):
    words={int(k,16):v['value'] for k,v in snapshot['device_states'][STATE]['registers'].items() if v['request_written']}
    if small:
        words.update({0x310:0x00d40140,0x318:0x23c,0xe40:0x8fac85e0,
                      0xf00:0xd8c6c200,0xf08:0xd8c83500,0xf10:448})
    return words

def configure(g,words):
    write_sequence(g,RESET+INIT+list(words.items()))
    g.write(BASE+0x100,2)

def check_aux(g,index,job,dest):
    s=wait(g)
    require(s['jobs']==job and s['result_valid'] and not s['failed'],'Actual auxiliary encoder completion')
    require(s['source_c1_output']==index and s['source_c1_job']==s['source_frame']==1,'Selected C1 surface/job/frame')
    require(s['native_profile_observed']==(index==2),'Small candidate never claimed native')
    require(s['logical_tiles']==s['transport_slots']==1 and len(s['tiles'])==1,'Non-grid one-picture request')
    source=g.state(pipeline.STATE)['outputs'][index];w,h,stride=source['width'],source['height'],source['stride']
    decoded=[]
    for plane,rows in (('y',h),('uv',h//2)):
        raw=saved(g,source[plane],source[plane+'_bytes'],f'aux{index}-{plane}.bin')
        require(hashlib.sha256(raw).hexdigest()==source[plane+'_sha256'],'Actual full source hash')
        words=np.frombuffer(raw,dtype='<u4').reshape(rows,stride//4)
        samples=np.empty((rows,stride//4*3),dtype='<u2')
        for shift in range(3):samples[:,shift::3]=(words>>(10*shift))&1023
        decoded.append(samples[:,:w])
    packed=decoded[0].tobytes()+decoded[1][:,::2].tobytes()+decoded[1][:,1::2].tobytes()
    r=s['tiles'][0]
    require(hashlib.sha256(packed).hexdigest()==r['input_sha256'],'Independent all-pixel I420 unpack, excluding stride padding')
    require(s['input_sha256']==[source['y_sha256'],source['uv_sha256']],'C1 hash receipt binding')
    core=g.state(STATE);require(core['pending']==0x20000 and core['irq'],'Native non-grid mask enables only final event')
    require(core['native_profile_observed']==(index==2),'MMIO observation scope')
    require(g.tx(f'readl {BASE+0x900:#x}')==4 and g.tx(f'readl {BASE+0x904:#x}')==0,'One lane four T records')
    require(g.tx(f'readl {BASE+0x110:#x}')==0,'All DMA retired')
    reg=core['registers'];row=s['lanes'][0]
    data=saved(g,reg['0xe40']['value']<<4,row['bytes'],f'aux{index}.h265')
    table=saved(g,reg['0xe90']['value']<<4,4096,f'aux{index}-table.bin')
    require(hashlib.sha256(data).hexdigest()==row['sha256'],'Committed stream hash')
    require(hashlib.sha256(table).hexdigest()==row['table_sha256'],'Committed T hash')
    require(not any(table[64:]),'Unused T records zero')
    for i,typ in enumerate((32,33,34,19)):
        actual,length,offset,reserved=struct.unpack_from('<4I',table,i*16)
        require(actual==typ and reserved==0 and length>=6 and offset+length<=len(data),'Bounded truthful T record')
        nal=data[offset:offset+length]
        require(nal[:4]==b'\0\0\0\1' and nal[4]==typ<<1 and nal[5]==1,'Actual canonical NAL header')
        if typ==19:
            require(r['single_picture_no_leading_verified'] and r['encoder_header_type']==20,'Actual original single IDR20')
            require(hashlib.sha256(nal).hexdigest()==r['dma_nal_sha256'],'Actual DMA image hash')
            raw=nal if r['raw_prefix']==4 else nal[1:]
            require(hashlib.sha256(raw).hexdigest()==r['after_sha256'],'Transformed raw NAL hash')
            original=bytearray(raw);original[r['raw_prefix']]=0x28
            require(hashlib.sha256(original).hexdigest()==r['before_sha256'],'Only type byte changed')
    for lane in range(4):
        require(g.tx(f'readl {BASE+0xf60+lane*4:#x}')==s['lanes'][lane]['bytes'],'Actual lane byte report')
        if lane:require(s['lanes'][lane]['bytes']==s['lanes'][lane]['records']==0,'No stale main lane results')
    (dest/f'aux{index}.h265').write_bytes(data);(dest/f'aux{index}-table.bin').write_bytes(table)
    before=s['stage1_acks'];g.write(BASE+0x120,0x40000)
    require(g.irq(0xfa) and g.state(CODEC)['stage1_acks']==before,'No fabricated masked stage ACK')
    g.write(BASE+0x120,0x20000);require(not g.irq(0xfa),'Final W1C clears IRQ')
    return g.state(CODEC)

def run_case(args,name):
    dest=args.output/name;dest.mkdir(parents=True,exist_ok=False)
    result=dict(case=name,passed=False,native_mmio_fixture=True,firmware_executed=False,small_profile_native_observed=False)
    native=json.loads(args.snapshot.read_text());c1=json.loads(args.c1_snapshot.read_text())
    targets=tuple((r['y'],r['uv']) for r in native['device_states'][pipeline.STATE]['outputs'])
    with tempfile.TemporaryDirectory(prefix='z8-aux-') as tmp:
        g=Lab(args.qemu,Path(tmp),c1,name!='default-off')
        try:
            g.start()
            with patch.object(pipeline,'TARGETS',targets):
                pipeline.producer(g);pipeline.configure(g)
                g.write(0x15432004,0x101);g.write(0x15180030,1)
            g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
            require(g.state(pipeline.STATE)['retired']==1,'Real producer completion and retirement')
            jobs=0
            if name=='continuous':
                configure(g,profile(json.loads(args.main_snapshot.read_text())));g.write(BASE+0x100,1)
                backend.check_result(g,dest)
                g.write(BASE+0x120,0x40000);g.write(BASE+0x120,0x20000);jobs=1
            indices=(2,1) if name=='continuous' else (1 if name.startswith('small') else 2,)
            for index in indices:
                words=profile(native,index==1)
                if name in ('profile','small-profile'):words[0x704]=13
                configure(g,words)
                address=words[0xe40]<<4;table=words[0xe90]<<4
                g.write_ram(address,b'\xa5'*64);g.write_ram(table,b'\xa5'*64)
                negative=name in ('profile','small-profile','source','small-source','default-off')
                if name in ('source','small-source'):
                    y=targets[index][0];g.write_ram(y,bytes([g.read_ram(y,1)[0]^1]))
                if negative:g.expected_unknown=BASE+0x100
                g.write(BASE+0x100,1)
                if negative:
                    s=wait(g);require(s['jobs']==s['dma_bytes']==0 and not s['result_valid'] and not g.irq(0xfa),'No rejected completion')
                    require(g.read_ram(address,64)==b'\xa5'*64 and g.read_ram(table,64)==b'\xa5'*64,'No rejected output writes')
                    require('Z9_UNKNOWN' in g.text(),'Strict UNKNOWN retained')
                    reason=g.state(STATE)['last_reject']
                    expected='start-unimplemented' if name=='default-off' else 'backend-source-hash' if 'source' in name else 'backend-unobserved-profile'
                    require(reason==expected,'Exact intended rejection, not accidental setup failure')
                else:
                    jobs+=1;s=check_aux(g,index,jobs,dest)
                    if name=='repeat':
                        configure(g,words);g.expected_unknown=BASE+0x100;g.write(BASE+0x100,1)
                        require(g.state(STATE)['last_reject']=='backend-source-ownership','Same C1 output cannot be reused after reset')
                        require(g.state(CODEC)['jobs']==1 and not g.irq(0xfa),'No duplicate result')
            if name=='continuous':
                s=g.state(CODEC);history=s['history']
                require(s['jobs']==3 and s['stage1_acks']==1 and s['stage2_acks']==3,'Grid and non-grid native IRQ contracts')
                require([r['source_c1_output'] for r in history]==[0,1,2],'All three bounded receipts retained')
                require([len(r['tiles']) for r in history]==[22,1,1],'Main transform evidence survives buffer reuse')
                require(history[0]['lanes'][0]['sha256']!=history[1]['lanes'][0]['sha256'],'Reused main staging holds a distinct small-image result')
            result['codec']=g.state(CODEC);result['protocol']=g.state(STATE)
            require(not g.qmp.command('query-status')['running'],'No firmware executed')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();(dest/'stderr.txt').write_text(g.text())
            (dest/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(name,result['passed'],result.get('error',''),flush=True);return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qemu','snapshot','main-snapshot','c1-snapshot','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    for path,digest in ((args.snapshot,RUN07),(args.main_snapshot,RUN06)):
        require(hashlib.sha256(path.read_bytes()).hexdigest()==digest,'Pinned original native MMIO evidence')
    rows=[run_case(args,name) for name in ('preview','small','profile','small-profile','source','small-source','continuous','repeat','default-off')]
    report=dict(passed=all(r['passed'] for r in rows),cases=rows,firmware_executed=False,
                native_mmio_fixture=True,binary_sha256=hashlib.sha256(args.qemu.read_bytes()).hexdigest())
    (args.output/'qtest-result.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)
if __name__=='__main__':main()
