# SPDX-License-Identifier: GPL-2.0-or-later
"""C0/C1 stop acknowledgements, separate from frames and encoded output."""
import argparse,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_persistent_media import Machine

OPTION='z8-video-ring-stop-model'
STATE='z8-video-ring-stop-state'
BASE=0x15431000
IRQ=0x6a-32


def require(value,message):
    if not value:raise AssertionError(message)


class Lab(Machine):
    def q(self,command):
        self.stream.write(command.encode()+b'\n')
        while True:
            reply=self.stream.readline().decode().strip()
            if not reply.startswith('IRQ '):break
            _,action,number=reply.split();self.irqs[int(number)]=action=='raise'
        require(reply.startswith('OK'),command+': '+reply)
        return reply.split()[1] if len(reply.split())>1 else None
    def observe_irq(self):
        self.irqs={}
        gics=[x for x in self.qmp.command('qom-list',path='/machine/unattached') if x['type']=='child<arm-gicv3>']
        require(len(gics)==1,'Unique GIC')
        self.q('irq_intercept_in /machine/unattached/'+gics[0]['name'])
    def write(self,off,value,j=0):self.q(f'writel {BASE+j*4096+off:#x} {value:#x}')
    def pending(self,j=0):return int(self.q(f'readl {BASE+j*4096+20:#x}'),0)
    def stop_state(self):return json.loads(self.qmp.command('qom-get',path='/machine',property=STATE))
    def control(self):return json.loads(self.qmp.command('qom-get',path='/machine',property='engine1543-control-state'))
    def ring(self,j=0,mask=0x29,start=True):
        for off,value in ((4,0x103),(8,0x302),(12,0),(16,mask)):self.write(off,value,j)
        for row in range(4):
            for word in range(4 if j==1 else 16):self.write(0x200+row*128+word*4,0,j)
        if start:self.write(4,0x101,j)


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    props=('engine1543-control-model=on','z8-video-interface-model=on')
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        def case(name,body,*,enabled=True,reject=False,machine='nikon-z8-bringup',omit=(),startup=False):
            selected=tuple(x for x in props if x.split('=')[0] not in omit)+((OPTION+'=on',) if enabled else ())
            try:
                with Lab(binary,root/name,kernel,properties=selected,machine=machine,accelerator='qtest') as m:
                    require(not startup,'Expected creation rejection')
                    m.observe_irq();body(m)
                    require(('Z9_UNKNOWN' in m.log_path.read_text())==reject,'Strict rejection mismatch: '+name)
            except RuntimeError as exc:
                if not startup or OPTION not in str(exc):raise
            cases.append(dict(name=name,passed=True))
        for j in (0,1):
            def transaction(m,j=j):
                m.ring(j);m.write(4,0x100,j)
                require(m.pending(j)==0x20 and m.irqs.get(IRQ),'Stop IRQ')
                require(not m.control()['blocks'][j]['active'],'Stopped before notification')
                require(m.stop_state()['stops'][j]==1,'One generation')
                m.write(20,0x20,j);require(m.pending(j)==0 and not m.irqs.get(IRQ),'W1C deasserts IRQ')
                m.write(4,0x100,j);require(m.pending(j)==0,'Repeated stop has no event')
                m.write(4,0x101,j);m.write(4,0x100,j)
                require(m.pending(j)==0x20 and m.stop_state()['stops'][j]==2,'New generation')
                require(m.stop_state()['acks'][j]==1,'Only original W1C acknowledgement')
                require(m.control()['pixel_bytes']==0,'No fabricated pixels')
            case(f'channel-{j}',transaction)
        def masked(m):
            m.ring();m.write(16,0);m.write(4,0x100)
            require(m.pending()==0x20 and not m.irqs.get(IRQ),'Masked pending')
            m.write(16,0x20);m.pending();require(m.irqs.get(IRQ),'Unmask pending')
            m.write(20,1);require(m.pending()==0x20,'Unrelated W1C preserves cause')
        case('masked',masked)
        def shared(m):
            for j in (0,1):m.ring(j);m.write(4,0x100,j)
            m.write(20,0x20);require(m.pending(1)==0x20 and m.irqs.get(IRQ),'Independent channel owns shared IRQ')
            m.write(20,0x20,1);m.pending();require(not m.irqs.get(IRQ),'Both acknowledged')
        case('shared-irq',shared)
        def inactive(m):
            m.ring(start=False);m.write(4,0x100)
            require(m.pending()==0 and m.stop_state()['stops']==[0,0],'Inactive stop is not completion')
        case('inactive',inactive)
        def disabled(m):
            m.ring();m.write(4,0x100);require(m.pending()==0,'Default retains old behavior')
        case('disabled',disabled,enabled=False)
        def third(m):
            m.ring(2);m.write(4,0x100,2);require(m.pending(2)==0,'C2 unchanged')
        case('third-channel',third)
        def malformed(m,kind):
            m.ring(mask=0x21 if kind=='mask' else 0x29)
            if kind=='held':m.write(4,0x102)
            if kind=='unacked-start':
                m.write(4,0x100);m.write(4,0x101)
            else:m.write(4,0 if kind=='bank' else 0x100)
            require(m.stop_state()['stops'][0]==(1 if kind=='unacked-start' else 0),'Rejected transition cannot invent event')
        for name in ('mask','held','bank','unacked-start'):
            case(name,lambda m,n=name:malformed(m,n),reject=True)
        def immutable(m):
            try:m.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
            except RuntimeError:pass
            else:raise AssertionError('Mutable after creation')
        case('immutable',immutable)
        case('z9',lambda m:None,machine='nikon-z9-lab',omit=('z8-video-interface-model',),startup=True)
        for name in ('engine1543-control-model','z8-video-interface-model'):
            case('missing-'+name,lambda m:None,omit=(name,),startup=True)
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),recording=False,physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
