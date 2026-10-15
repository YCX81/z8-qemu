# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual NR U16 source -> full RAW plus cropped NV16; no Nikon firmware."""
import argparse,hashlib,json,tempfile
from pathlib import Path
import numpy as np
from check_z8_machine import sha256
from test_z8_d980_internal import require
from test_z8_hsfc153_u16 import Lab as NRLab,NR,NR0,TARGET,OUTPUT_BYTES,decode_second
from test_z8_producer_journal import configure151

OPTION='z8-hsfc151-pixels-model'
STATE='z8-hsfc151-state'
Y,UV,RAW=0x860000000,0x864000000,0x868000000
PLANE=4128*2752

class Lab(NRLab):
    def __init__(self,binary,folder,*,hsfc=True):
        super().__init__(binary,folder,nr0_enabled=True)
        self.command[self.command.index('-M')+1]+= ',engine151-capture-pixels-model=on,engine151-raw-output-model=on,engine151-average-result-model=on'
        if hsfc:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
    def pair(self,pa,value):
        self.write(pa,value&0xffffffff);self.write(pa+4,value>>32)
    def prepare_source(self,nr=True,count=1):
        self.source(count);self.configure_decoder();self.decode();self.output()
        if nr:
            self.prepare_nr();self.write(NR+4,1);self.write(NR+0x2c,1)
    def prepare_isp(self,source=TARGET,*,raw_target=RAW,y_target=Y,uv_target=UV):
        configure151(self,source,initial=True)
        fields={0x40:0x28b,0x220:2,0x400:(2756<<16)|4140,0x430:8288,
                0x450:(2752<<16)|4128,0x454:(2<<16)|6,0x480:4128,0x484:4128,
                0x4a0:(2756<<16)|4140,0x4a4:0,0x4ac:2,0x4d0:8288}
        for off,value in fields.items():self.write(0x15100000+off,value)
        for off,pa in ((0x900,source),(0x980,y_target),(0x9c0,uv_target),(0x4c0,raw_target)):
            self.write(0x15100000+off,pa&0xffffffff);self.write(0x15100004+off,pa>>32)
        for pa,n in ((y_target,PLANE),(uv_target,PLANE),(raw_target,OUTPUT_BYTES)):
            self.tx(f'write {pa:#x} 0x20 0x'+'75'*32)
            self.tx(f'write {pa+n:#x} 0x20 0x'+'69'*32)
    def irq151(self):
        self.tx('readl 0x15100044');return self.irqs.get(0x10d-32,False)
    def start_isp(self):self.write(0x15100008,1)
    def verify_outputs(self):
        source=self.read_ram(TARGET,OUTPUT_BYTES);raw=self.read_ram(RAW,OUTPUT_BYTES)
        require(source==raw,'RAW side did not preserve all actual U16 source bytes')
        image=np.frombuffer(source,dtype='<u2').reshape(2756,4144)[:,:4140][2:2754,6:4134].astype(np.uint32)
        t=np.arange(16384,dtype=np.float64)/16383
        tone=np.rint(np.where(t<=0.0031308,12.92*t,1.055*np.power(t,1/2.4)-0.055)*255).astype(np.int32)
        r=tone[image[::2,::2]];g=tone[(image[::2,1::2]+image[1::2,::2]+1)//2];b=tone[image[1::2,1::2]]
        yy=((77*r+150*g+29*b+128)>>8).astype(np.uint8)
        uu=np.clip(((-43*r-85*g+128*b+128)>>8)+128,0,255).astype(np.uint8)
        vv=np.clip(((128*r-107*g-21*b+128)>>8)+128,0,255).astype(np.uint8)
        expected_y=yy.repeat(2,axis=0).repeat(2,axis=1)
        expected_uv=np.empty((2752,4128),dtype=np.uint8)
        expected_uv[:,::2]=uu.repeat(2,axis=0);expected_uv[:,1::2]=vv.repeat(2,axis=0)
        actual_y=self.read_ram(Y,PLANE);actual_uv=self.read_ram(UV,PLANE)
        require(actual_y==expected_y.tobytes() and actual_uv==expected_uv.tobytes(),'Complete NV16 output differs from declared functional convention')
        for pa,n in ((Y,PLANE),(UV,PLANE),(RAW,OUTPUT_BYTES)):
            require(self.read_ram(pa+n,32)==b'\x69'*32,'Output exceeded exact span')
        s=self.state(STATE);q=s['records'][-1]
        require(q['frame_id']==1 and q['decoder_job']==1 and q['raw_sha256']==hashlib.sha256(raw).hexdigest(),'Historical RAW identity/hash differs')
        require(q['y_sha256']==hashlib.sha256(actual_y).hexdigest() and q['uv_sha256']==hashlib.sha256(actual_uv).hexdigest(),'Preview hashes differ')
        require(self.state('primary-d980-state')['frames']==0,'Invented D980 input')
        return s

def case(binary,name,body,*,reject=None,**kw):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-history-isp-') as tmp:
        g=Lab(binary,Path(tmp),**kw)
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==bool(reject),'Unexpected strict stop')
            if reject:require(f'addr=0x{reject:x}' in g.text(),'Wrong strict address')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row

def complete(g):
    g.prepare_source();g.prepare_isp();g.start_isp()
    require(g.irq151() and g.tx('readl 0x15100044')&2,'Historical151 data did not complete')
    return g.verify_outputs()

def run(binary,red=False):
    if red:return [case(binary,'old151 cannot consume historical U16',complete,hsfc=False)]
    rows=[case(binary,'all RAW and NV16 bytes plus canaries and lineage',complete)]
    def reject(g,change=None,nr=True,source=True):
        if source:g.prepare_source(nr=nr)
        g.prepare_isp()
        if change:change(g)
        g.start_isp();require(not g.irq151(),'Rejected151 produced completion')
        for pa in (Y,UV,RAW):require(g.read_ram(pa,32)==b'\x75'*32,'Rejected151 modified output')
    rows.append(case(binary,'default off remains strict',reject,hsfc=False,reject=0x15100008))
    rows.append(case(binary,'no actual decoder source rejected',lambda g:reject(g,source=False),reject=0x15100008))
    rows.append(case(binary,'source without completed NR rejected',lambda g:reject(g,nr=False),reject=0x15100008))
    rows.append(case(binary,'corrupt source cannot be restored from host',lambda g:reject(g,lambda q:q.tx(f'write {TARGET+10000:#x} 1 0xff')),reject=0x15100008))
    rows.append(case(binary,'wrong input stride rejected',lambda g:reject(g,lambda q:q.write(0x15100430,6240)),reject=0x15100008))
    rows.append(case(binary,'wrong RAW geometry rejected',lambda g:reject(g,lambda q:q.write(0x151004a0,0)),reject=0x15100008))
    rows.append(case(binary,'RAW cannot overlap input',lambda g:reject(g,lambda q:(q.write(0x151004c0,TARGET&0xffffffff),q.write(0x151004c4,TARGET>>32))),reject=0x15100008))
    rows.append(case(binary,'RAW span crossing real RAM gap rejected before writes',lambda g:reject(g,lambda q:(q.write(0x151004c0,0xadf8d9e0),q.write(0x151004c4,8))),reject=0x15100008))
    def reuse(g):
        complete(g);g.write(0x15100044,2);require(not g.irq151(),'ACK did not lower IRQ')
        before=g.state(STATE);g.start_isp();require(g.state(STATE)==before,'One decoder generation processed twice')
    rows.append(case(binary,'same history generation cannot repeat after ACK',reuse,reject=0x15100008))
    def two_frames(g,repeat=False):
        g.prepare_source(count=2);g.prepare_isp();g.start_isp();first=g.verify_outputs()['records'][-1]
        g.write(0x15100044,2);second=decode_second(g)
        g.prepare_nr(source=second,bank=0);g.write(NR0+4,1);g.write(NR0+0x2c,1)
        g.prepare_isp(source=second);g.start_isp()
        actual=g.read_ram(RAW,OUTPUT_BYTES)
        require(actual==g.read_ram(second,OUTPUT_BYTES),'Second RAW copy differs from actual second source')
        s=g.state(STATE);require([r['valid'] for r in s['records']]==[False,True],'Output reuse retained stale receipt')
        require(s['records'][-1]['frame_id']==2 and s['records'][-1]['raw_sha256']!=first['raw_sha256'],'Distinct frames collapsed')
        if repeat:
            g.write(0x15100044,2);g.prepare_isp(source=TARGET);before=g.state(STATE);g.start_isp()
            require(g.state(STATE)==before,'A B A history input was consumed again')
        return s
    rows.append(case(binary,'two frames reuse output only with fresh source generation',two_frames))
    rows.append(case(binary,'nonconsecutive A B A input rejected',lambda g:two_frames(g,True),reject=0x15100008))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline)
    r=dict(passed=all(x['passed'] for x in rows),cases=rows,qemu_sha256=sha256(a.qemu),raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(passed=r['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not r['passed'])
