# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest for C0 control stop; authored inputs, no firmware execution."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_d980_internal import Lab as BaseLab, require

OPTION='z8-1543-c0-stop-model'
STATE='z8-1543-c0-stop-state'
C0=0x15431000
IRQ=0x6a-32


class Lab(BaseLab):
    def __init__(self,binary,folder,*,enabled=True,machine='nikon-z8-bringup',omit=()):
        super().__init__(binary,folder,enabled=False,machine=machine,
            omit=('primary-stopped-source-reset-model',*omit))
        self.irqs={};self.irq_events=[]
        if enabled:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
    def start(self):
        super().start()
        gics=[x for x in self.qmp.command('qom-list',path='/machine/unattached') if x['type']=='child<arm-gicv3>']
        require(len(gics)==1,'Unique GIC required for real IRQ observation')
        self.tx('irq_intercept_in /machine/unattached/'+gics[0]['name'])
    def tx(self,command):
        self.stream.write(command.encode()+b'\n')
        while True:
            reply=self.stream.readline().decode().strip()
            if not reply.startswith('IRQ '):break
            _,action,number=reply.split();self.irqs[int(number)]=action=='raise';self.irq_events.append(reply)
        if not reply.startswith('OK'):raise RuntimeError(f'QTest {command}: {reply}')
        words=reply.split()[1:]
        return int(words[0],0) if words else None
    def pending(self,j=0):return self.tx(f'readl {C0+j*0x1000+0x14:#x}')
    def irq(self):
        self.pending()  # drain any queued asynchronous QTest line event
        return self.irqs.get(IRQ,False)
    def block(self,j=0):return self.state('engine1543-control-state')['blocks'][j]
    def read_ram(self,address,size):
        self.stream.write(f'read {address:#x} {size:#x}\n'.encode())
        while True:
            reply=self.stream.readline().decode().strip()
            if not reply.startswith('IRQ '):break
            _,action,number=reply.split();self.irqs[int(number)]=action=='raise';self.irq_events.append(reply)
        require(reply.startswith('OK 0x'),'QTest RAM read failed')
        result=bytes.fromhex(reply[5:]);require(len(result)==size,'Short QTest RAM read');return result


def ring(g,j=0,*,mask=0x29,start=True,control=0x101):
    base=C0+j*0x1000
    for off,value in ((4,0x100),(8,0),(0xc,0),(0x10,mask)):g.write(base+off,value)
    for row in range(4):
        for off in range(0,0x40,4):g.write(base+0x200+row*0x80+off,0)
        for channel in range(3):
            y=0x840000000+row*0x10000+channel*0x2000
            off=base+0x210+row*0x80+channel*0x10
            for at,p in ((off,y),(off+8,y+0x1000)):
                g.write(at,p&0xffffffff);g.write(at+4,p>>32)
    require(g.block(j)['ready'],'Authored complete ring did not become ready')
    if start:g.write(base+4,control)


def preview(g):
    # Complete the existing151 parameter-pointer and source-gate protocol.
    base=0x15140000
    for off,value in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff),
        (0x1c,0x3f),(0x260,0x1fff),(0x264,0x3ff),(0x400,32<<16|64),(0x450,32<<16|64)):
        g.write(base+off,value)
    for off in range(0x268,0x320,8):g.write(base+off,0x1000);g.write(base+off+4,0xa)
    g.write(base+8,1)
    g.write(0x151c0014,1);g.write(0x151c0014,0);g.write(0x151c0030,1)
    for channel in range(3):
        b=0x15422000+channel*0x100;f=0x15423000+channel*0x100
        for off,value in ((0xc,0),(0x10,1),(0x38,64),(0x3c,64)):g.write(b+off,value)
        g.write(f+0x2c,64);g.write(f+0x30,32)
    ring(g)


def pixel_state(g):
    p=g.state('raw-pipeline-state')
    return {k:p[k] for k in ('frames','pixel_bytes','row','batches') if k in p}


def snapshot(g):
    return dict(control=g.block(),pending=g.pending(),irq=g.irq(),stop=g.state(STATE))


def case(binary,name,body,*,enabled=True,reject=None,startup_error=None,**kwargs):
    result=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-c0-stop-') as tmp:
        g=Lab(binary,Path(tmp),enabled=enabled,**kwargs)
        try:
            try:g.start()
            except (RuntimeError,OSError):
                if not startup_error or startup_error not in g.text():raise
            else:
                require(startup_error is None,'Expected creation dependency failure')
                result['observed']=body(g)
                require(('Z9_UNKNOWN' in g.text())==bool(reject),'Unexpected strict boundary')
                if reject:require(f'addr=0x{reject:x}' in g.text(),'Wrong strict boundary')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();result['stderr']=g.text();result['irq_events']=g.irq_events
            result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
    return result


def exact(g):
    ring(g);g.write(C0+4,0x100)
    require(g.pending()==0x20,'Original C0 stop produced no bit5 acknowledgement')
    require(g.irq(),'Unmasked stop did not reach actual GIC input')
    s=g.state(STATE);require(s['stops']==1 and s['owned'],'Missing stop generation')
    g.write(C0+0x14,0x20)
    require(g.pending()==0 and not g.irq(),'Original W1C did not lower IRQ')
    require(g.state(STATE)['acks']==1 and not g.state(STATE)['owned'],'W1C did not retire stop')
    g.no_pixels();return snapshot(g)


def positive_pixels(g,*,expect_stop=True,refill=False):
    preview(g);before=g.read_ram(0x840000000,0x1800);g.advance()
    p=g.state('raw-pipeline-state');require(p['frames']>0,'No real preview frame before stop')
    require(before!=g.read_ram(0x840000000,0x1800),'No actual preview RAM change before stop')
    if refill:
        for _ in range(3):g.write(C0+0x14,1);g.advance()
        require(g.pending()==8,'Genuine refill bit3 not reached')
    else:require(g.pending()==1,'Genuine preview bit0 not reached')
    pending=g.pending();g.write(C0+4,0x100)
    require(g.pending()==pending|(0x20 if expect_stop else 0),'Stop changed previous pixel/refill causes')
    frozen=pixel_state(g);data=g.read_ram(0x840000000,0x40000)
    if expect_stop:
        g.write(C0+0x14,0x20)
        require(g.pending()==pending and g.irq(),'Stop W1C consumed other cause or lowered shared IRQ')
    g.write(C0+0x14,pending);require(not g.irq(),'Previous cause W1C did not lower IRQ')
    g.advance();require(pixel_state(g)==frozen,'Stopped C0 generated another frame')
    require(g.read_ram(0x840000000,0x40000)==data,'Stopped C0 changed destination RAM')
    g.no_pixels();return dict(positive_frames=frozen['frames'],ram_sha256=hashlib.sha256(data).hexdigest(),after=pixel_state(g))


def run(binary,red=False):
    if red:return [case(binary,'red active stop lacks original bit5',exact,enabled=False),
                   case(binary,'baseline real preview then stop',lambda g:positive_pixels(g,expect_stop=False),enabled=False)]
    cases=[case(binary,'exact stop and IRQ W1C',exact)]
    for refill in (False,True):
        cases.append(case(binary,'real preview stop preserves '+('refill' if refill else 'frame'),
                          lambda g,refill=refill:positive_pixels(g,refill=refill)))
    cases.append(case(binary,'default off real preview unchanged',lambda g:positive_pixels(g,expect_stop=False),enabled=False))
    def masked(g):
        ring(g);g.write(C0+0x10,9);g.write(C0+4,0x100)
        require(g.pending()==0x20 and not g.irq(),'Masked stop lost cause or raised IRQ')
        g.write(C0+0x10,0x29);require(g.irq(),'Unmask did not deliver pending stop')
        g.write(C0+0x14,1);require(g.pending()==0x20 and g.irq(),'Unrelated W1C consumed stop')
        g.write(C0+0x14,0x20);require(not g.irq(),'Stop ACK left IRQ asserted')
        return snapshot(g)
    cases.append(case(binary,'masked pending then actual unmask IRQ',masked))
    def shared(g):
        preview(g);g.write(0x15421010,1);g.write(0x15421018,0x11);g.advance()
        require(g.tx('readl 0x1542101c')==0x11 and g.pending()==1,'Real A0/C0 causes not produced')
        g.write(C0+4,0x100);g.write(C0+0x14,0x21)
        require(g.pending()==0 and g.tx('readl 0x1542101c')==0x11 and g.irq(),'C0 ACK consumed A0 or lowered shared IRQ')
        g.write(0x1542101c,0x11);require(not g.irq(),'A0 W1C left shared IRQ high')
        return snapshot(g)
    cases.append(case(binary,'shared IRQ stays high for real A0 cause',shared))
    def generation(g):
        ring(g);g.write(C0+4,0x100);g.write(C0+0x14,0x20)
        g.write(C0+4,0x101);g.write(C0+4,0x100)
        require(g.state(STATE)['stops']==2 and g.state(STATE)['acks']==1,'New acknowledged generation not distinct')
        g.write(C0+0x14,0x20);require(g.state(STATE)['acks']==2,'Second generation not acknowledged')
        return snapshot(g)
    cases.append(case(binary,'new generation only after original W1C',generation))
    def simultaneous_c1(g):
        ring(g,1);other=g.block(1);ring(g);g.write(C0+4,0x100);g.write(C0+0x14,0x20)
        require(g.block(1)==other and g.pending(1)==0,'C0 transaction changed active C1')
        g.write(C0+0x1004,0x100);require(g.pending(1)==0,'C1 stop incorrectly acquired control bit5')
        return dict(c1_before=other,c1_after=g.block(1),c0=snapshot(g))
    cases.append(case(binary,'C0 stop and ACK leave active C1 untouched',simultaneous_c1))
    def repeat(g):
        ring(g);g.write(C0+4,0x100);s=g.state(STATE)
        g.write(C0+4,0x100);require(g.state(STATE)==s and g.pending()==0x20,'Repeated stop duplicated ownership')
        g.write(C0+0x14,0x20);g.write(C0+4,0x100)
        require(g.pending()==0 and g.state(STATE)['stops']==1,'Inactive stop created completion')
        g.write(C0+4,0x102);require(not g.block()['held'] and g.pending()==0,'Inactive mode2 completed or armed')
        return snapshot(g)
    cases.append(case(binary,'repeated and inactive stops and mode2',repeat))
    for j in (1,2):
        def other(g,j=j):
            ring(g,j);before=g.block(j);g.write(C0+j*0x1000+4,0x100)
            require(g.pending(j)==0 and not g.block(j)['active'],'Non-C0 control behavior changed')
            require(g.state(STATE)['stops']==0,'Non-C0 produced owned stop')
            return dict(before=before,after=g.block(j))
        cases.append(case(binary,f'legacy C{j} control unchanged',other))
    for name,setup,command in (
        ('held active mode2',lambda g:(ring(g),g.write(C0+4,0x102)),0x100),
        ('wrong current start bit8',lambda g:ring(g,control=1),0x100),
        ('wrong latched mask',lambda g:ring(g,mask=9),0x100),
        ('wrong stop bit8',ring,0),
        ('start before stop acknowledgement',lambda g:(ring(g),g.write(C0+4,0x100)),0x101)):
        def bad(g,setup=setup,command=command):
            setup(g);before=snapshot(g);g.write(C0+4,command)
            require(snapshot(g)==before,'Rejected C0 command partially mutated state')
        cases.append(case(binary,name,bad,reject=C0+4))
    def incomplete(g):
        g.write(C0+4,0x101);require(not g.block()['active'] and g.pending()==0,'Incomplete start accepted')
    cases.append(case(binary,'incomplete ring cannot start',incomplete,reject=C0+4))
    for verb in ('writeb','writew'):
        def width(g,verb=verb):
            ring(g);before=snapshot(g);g.write(C0+4,0x100,verb)
            require(snapshot(g)==before,'Invalid device width changed control')
        cases.append(case(binary,'reject '+verb,width,reject=C0+4))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as error:require('machine creation' in str(error),'Wrong live mutation rejection')
        else:raise AssertionError('Live option mutation accepted')
    cases.append(case(binary,'creation-only option',immutable))
    cases.append(case(binary,'reject Z9 opt-in',lambda g:None,machine='nikon-z9-lab',startup_error='requires Z8'))
    cases.append(case(binary,'requires shared IRQ pipeline',lambda g:None,omit=('raw-pipeline-model',),startup_error='requires'))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cases=run(a.qemu,a.red_baseline)
    result=dict(cases=cases,passed=all(c['passed'] for c in cases),qemu_sha256=sha256(a.qemu),source_sha256=sha256(Path(__file__)))
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],cases=len(cases),failures=[{k:c[k] for k in ('name','error') if k in c} for c in cases if not c['passed']])))
    raise SystemExit(not result['passed'])


if __name__=='__main__':main()
