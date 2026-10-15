# SPDX-License-Identifier: GPL-2.0-or-later
"""Retained movie processing setup must never invent processed pixels."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    props=('z8-video-interface-model=on','z8-video-processing-config-model=on',
           'z8-video-plane-reader-model=on')
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'valid',kernel,accelerator='qtest',properties=props) as m:
            def w(a,v):m.q(f'writel {a:#x} {v:#x}')
            w(0x15660000,1);w(0x15661008,0x7fff)
            m.q('writeq 0x15661280 0x203000000')
            if int(m.q('readq 0x15661280'),0)!=0x203000000:
                raise AssertionError('Plane reader retains complete address')
            if int(m.q('readl 0x15661020'),0):raise AssertionError('Empty reader invented job')
            w(0x15660010,1)
            if int(m.q('readq 0x15661280'),0):raise AssertionError('Reader reset retained old descriptors')
            w(0x16018000,0);w(0x16018004,1);w(0x16018008,0x111)
            for port in range(2):
                base=0x16019000+port*0x1000
                for off in (4,8,12,16,0x100,*range(0x120,0x17c,4),*range(0x180,0x190,4)):
                    w(base+off,0)
                w(base+0x200,0x1234+port)
                w(base,1)
                if int(m.q(f'readl {base+0x14:#x}'),0):raise AssertionError('Invented completion')
                w(base+8,1);w(base+0x14,0xffffffff);w(base,0)
            if int(m.q('readl 0x1601800c'),0):raise AssertionError('Invented aggregate IRQ')
            w(0x0d154348,0x800000)
            if int(m.q('readl 0x16019200'),0):raise AssertionError('Reset target config survives')
            if int(m.q('readl 0x1601a200'),0)!=0x1235:raise AssertionError('Reset erased other target')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Valid configuration rejected')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-processing-state'))
            if state['pixel_bytes'] or state['completion_events']:raise AssertionError('Configuration produced data')
            cases.append('retained independent targets and empty status')
        for name,command,on in (
            ('disabled','readl 0x16018000',False),
            ('reserved','writel 0x16018008 0x112',True),
            ('unknown','readl 0x16019018',True),
            ('width','readb 0x16018000',True),
            ('premature start','writel 0x16019000 1',True)):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',
                         properties=props if on else ()) as m:
                m.q(command)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError(name)
                cases.append(name)
    report=dict(passed=True,cases=cases,pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
