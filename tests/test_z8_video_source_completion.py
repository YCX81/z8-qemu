# SPDX-License-Identifier: GPL-2.0-or-later
"""Source notification only after real RAW-derived preview RAM writes."""
import argparse,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require

OPTION='raw-pipeline-151c-completion-model'
STATE='raw-pipeline-151c-completion-state'


def setup(m, *, before_start=None):
    def w(a,v):m.q(f'writel {a:#x} {v:#x}')
    base=0x15140000
    for off,value in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff),
        (0x1c,0x3f),(0x260,0x1fff),(0x264,0x3ff),(0x400,32<<16|64),(0x450,32<<16|64)):
        w(base+off,value)
    for off in range(0x268,0x320,8):w(base+off,0x1000);w(base+off+4,0xa)
    w(base+8,1)
    w(0x151c0014,1);w(0x151c0014,0);w(0x151c0008,0x08100000);w(0x151c0030,1)
    for channel in range(3):
        b=0x15422000+channel*0x100;f=0x15423000+channel*0x100
        for off,value in ((0xc,0),(0x10,1),(0x38,64),(0x3c,64)):w(b+off,value)
        w(f+0x2c,64);w(f+0x30,32)
    m.ring(start=False)
    for row in range(4):
        for channel in range(3):
            y=0x840000000+row*0x10000+channel*0x2000;off=0x210+row*0x80+channel*0x10
            for at,p in ((off,y),(off+8,y+0x1000)):
                m.write(at,p&0xffffffff);m.write(at+4,p>>32)
    if before_start is not None:before_start(m)
    m.write(4,0x101)


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temp:
        root=Path(temp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096));raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        props=tuple(x+'=on' for x in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model'))+('statistics-raw='+str(raw),)
        for name,enabled in (('enabled',True),('disabled',False)):
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props+((OPTION+'=on',) if enabled else ())) as m:
                m.observe_irq()
                def advance():
                    m.qmp.command('cont');m.q('clock_step 100000000');m.qmp.command('stop')
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property=STATE))
                def pixels():return bytes.fromhex(m.q('read 0x840000000 0x800')[2:])
                before=pixels();advance()
                if enabled:require(state()['completions']==0,'No notification without configured output')
                setup(m);advance();after=pixels()
                require(before!=after and any(after),'Actual RAW-derived output required')
                pending=int(m.q('readl 0x151c0010'),0)
                require(pending==(0x08100000 if enabled else 0),'Source completion is opt-in')
                if enabled:
                    require(state()['completions']==1,'One frame/notification generation')
                    m.write(20,1);advance()
                    require(state()['completions']==1,'Source W1C required before reuse')
                    m.q('writel 0x151c000c 0x100000');advance()
                    require(state()['acks']==0 and state()['completions']==1,'Partial W1C does not release ownership')
                    m.q('writel 0x151c000c 0x08000000');advance()
                    require(state()['acks']==1 and state()['completions']==2,'Original W1C permits next real output')
                    m.q('writel 0x151c0014 1');advance()
                    require(state()['cancelled']==1 and state()['pending']==0 and state()['completions']==2,'Reset cancels pending and source gate')
                require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected MMIO')
                cases.append(dict(name=name,passed=True,real_ram_changed=True))
        with Lab(binary,root/'isp-completion',kernel,accelerator='qtest',
                 properties=props+('raw-pipeline-1514-completion-model=on',)) as m:
            m.observe_irq();setup(m)
            def advance():
                m.qmp.command('cont');m.q('clock_step 100000000');m.qmp.command('stop')
            def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='raw-pipeline-1514-completion-state'))
            advance();require(state()['completions']==1 and state()['pending']==8,'ISP completion after real output')
            require(any(bytes.fromhex(m.q('read 0x840000000 0x800')[2:])),'Actual ISP output required')
            m.write(20,1);advance();require(state()['completions']==1,'ISP acknowledgement gates reuse')
            m.q('writel 0x15140044 8');advance()
            require(state()['acks']==1 and state()['completions']==2,'Next actual output after W1C')
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected ISP MMIO')
            cases.append(dict(name='isp-completion',passed=True,real_ram_changed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),encoded_video=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
