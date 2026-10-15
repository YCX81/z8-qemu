# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual retained14-bit packet to U16 RAM; complete samples, ownership and IRQ."""
import argparse,hashlib,json,tempfile
from pathlib import Path
import numpy as np
from test_z8_hsfc1530_decode import Lab as BaseLab,D,CORE,TARGET,STATE,WIDTH,HEIGHT,PHYSICAL
from test_z8_d980_internal import require
from check_z8_machine import sha256

OPTION='z8-hsfc1530-u16-model'
STRIDE=8288
OUTPUT_BYTES=STRIDE*HEIGHT

class Lab(BaseLab):
    def __init__(self,binary,folder,*,u16=True,**kw):
        super().__init__(binary,folder,**kw)
        if u16:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
    def configure_decoder(self,source=PHYSICAL[0],target=TARGET):
        super().configure_decoder(source,target)
        self.write(D+0x530,0);self.write(D+0x544,STRIDE)
        self.tx(f'write {TARGET+OUTPUT_BYTES:#x} 0x20 0x'+'6b'*32)
    def output(self,frame_id=1):
        raw=self.read_ram(TARGET,OUTPUT_BYTES)
        ys=(np.arange(HEIGHT)//2*16//(HEIGHT//2))*2+(np.arange(HEIGHT)&1)
        xs=(np.arange(WIDTH)//2*32//(WIDTH//2))*2+(np.arange(WIDTH)&1)
        source=((np.arange(2048,dtype=np.uint32)*131)%16384).reshape(32,64)
        samples=source[ys[:,None],xs]
        for y in range(16):
            for x in range(64):
                bit=((frame_id>>(x//2))&1)^(y>=8)
                samples[y,x]=16383*7//8 if bit else 16383//8
        expected=np.zeros((HEIGHT,STRIDE//2),dtype='<u2');expected[:,:WIDTH]=samples
        require(raw==expected.tobytes(),'All14-bit samples and U16 padding must match without rescaling')
        require(np.any(samples&3),'Fixture must exercise low two bits')
        require(self.read_ram(TARGET-32,32)==b'\x5a'*32 and self.read_ram(TARGET+OUTPUT_BYTES,32)==b'\x6b'*32,'Output exceeded span')
        s=self.state(STATE);records=[r for r in s['records'] if r['valid'] and r['target']==TARGET and r['frame_id']==frame_id]
        require(len(records)==1,'Unique current output generation required');r=records[0]
        require(r['frame_id']==frame_id and r['stride']==STRIDE and r['storage_bits']==16 and r['output_bits']==14 and r['precision_preserved'],'Incorrect precision/identity receipt')
        require(r['output_sha256']==hashlib.sha256(raw).hexdigest(),'Actual U16 RAM hash differs')
        require(s['precision_preserved'] and s['u16_jobs']==s['jobs'],'Decoder precision accounting differs')
        return s

def case(binary,name,body,*,reject=None,**kw):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-hsfc-u16-') as tmp:
        g=Lab(binary,Path(tmp),**kw)
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==bool(reject),'Unexpected strict boundary')
            if reject:require(f'addr=0x{reject:x}' in g.text(),'Wrong strict boundary')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row

def complete(g):
    g.source();g.configure_decoder();g.decode()
    require(g.tx(f'readl {D+0x1c:#x}')==6 and g.irq_decoder(),'Actual U16 DMA must precede job completion')
    return g.output()

def run(binary,red=False):
    if red:return [case(binary,'old packed12 profile refuses U16 request',complete,u16=False)]
    rows=[case(binary,'all14-bit samples survive actual packet to U16 RAM',complete)]
    def historical(g):
        g.source(2);g.configure_decoder();g.decode();s=g.output(1)
        require(g.state('z8-hsfc152c-pixels-state')['frames']==2,'Newer producer fixture missing')
        return s
    rows.append(case(binary,'requested older frame preserved while latest is newer',historical))
    def ack(g):
        g.source();g.configure_decoder();g.write(D+0x18,0);g.decode();g.output()
        require(not g.irq_decoder() and g.state(STATE)['pending']==6,'Masked notification lost')
        g.write(D+0x18,0x79f);g.write(D+0x1c,2)
        require(g.irq_decoder() and g.state(STATE)['pending']==4,'Partial W1C lost completion')
        g.write(D+0x1c,4);g.write(D+8,0);g.decode();s=g.output()
        require(s['jobs']==2 and [r['valid'] for r in s['records']]==[False,True],'Target generation reuse differs')
        return s
    rows.append(case(binary,'mask W1C and explicit target generation reuse',ack))
    def rejected(g,change=None,produce=True):
        if produce:g.source()
        g.configure_decoder()
        if change:change(g)
        g.decode();require(g.read_ram(TARGET,32)==b'\xa5'*32 and not g.irq_decoder() and g.state(STATE)['jobs']==0,'Rejected request produced pixels')
        return g.state(STATE)
    rows.append(case(binary,'default-off remains strict',rejected,u16=False,reject=D+8))
    rows.append(case(binary,'missing actual producer rejected',lambda g:rejected(g,produce=False),reject=D+8))
    rows.append(case(binary,'corrupt retained packet cannot use host cache',lambda g:rejected(g,lambda q:q.tx(f'write {PHYSICAL[0]+81:#x} 1 0xff')),reject=D+8))
    rows.append(case(binary,'U16 mode cannot use packed12 stride',lambda g:rejected(g,lambda q:q.write(D+0x544,6240)),reject=D+8))
    rows.append(case(binary,'packed12 mode cannot use U16 stride',lambda g:rejected(g,lambda q:q.write(D+0x530,2)),reject=D+8))
    def overlap(g):
        g.source();before=g.read_ram(PHYSICAL[0],128);g.configure_decoder(target=PHYSICAL[0]);g.decode()
        require(g.read_ram(PHYSICAL[0],128)==before and not g.irq_decoder(),'Overlap damaged source')
    rows.append(case(binary,'retained input cannot be overwritten by U16 output',overlap,reject=D+8))
    def reset(g):
        complete(g);g.write(D+0x404,1);g.write(D,1);s=g.output()
        require(s['cancelled']==1 and s['pending']==0 and s['acks']==0,'Reset is not completion ACK')
        return s
    rows.append(case(binary,'reset retains completed RAM and cancels notification',reset))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as e:require('machine creation' in str(e),'Unexpected live-toggle result')
        else:raise AssertionError('Live U16 toggle accepted')
    rows.append(case(binary,'U16 choice is immutable',immutable))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--red-baseline',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    rows=run(a.qemu,a.red_baseline);r=dict(passed=all(x['passed'] for x in rows),cases=rows,qemu_sha256=sha256(a.qemu),raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(passed=r['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not r['passed'])
