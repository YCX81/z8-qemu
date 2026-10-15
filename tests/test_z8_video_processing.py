# SPDX-License-Identifier: GPL-2.0-or-later
"""Retained NR initialization and reset ownership; no processing completion."""
import argparse,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_video_ring_stop import Lab,require

PROPS=('video-processing-config-model=on','video-initial-reset-model=on','video-nr-reset-model=on')

def initialize(m,port=0):
    base=0x16019000+port*0x1000
    for off,v in ((4,1),(8,0),(12,0),(16,0),(0x100,0)):
        m.q(f'writel {base+off:#x} {v:#x}')
    for off in range(0x120,0x190,4):
        if off!=0x17c:m.q(f'writel {base+off:#x} {off:#x}')

def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for name in ('config','disabled','incomplete','active-reset','active-coefficients','bad-reset-mask','byte'):
            enabled=name!='disabled';bad=name!='config'
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=PROPS if enabled else ()) as m:
                m.observe_irq()
                def w(a,v):m.q(f'writel {a:#x} {v:#x}')
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='video-processing-config-state'))
                if name=='disabled':w(0xd154248,0x800000)
                elif name=='bad-reset-mask':w(0xd154248,0x802000)
                elif name=='byte':m.q('writeb 0x16018000 1')
                elif name=='incomplete':w(0x16018008,0x111);w(0x16019000,1)
                else:
                    w(0xd154248,0x800000);w(0xd154248,0x1000000)
                    w(0x16018008,0x111)
                    for port in range(2):initialize(m,port)
                    w(0x16019000,1)
                    if name=='active-reset':w(0xd154348,0x800000)
                    elif name=='active-coefficients':w(0x16019120,9)
                    else:
                        require(state()['ports'][0]['initialized'],'Complete configuration admitted')
                        w(0x16019008,1);w(0x16019008,0)
                        require(int(m.q('readl 0x16019014'),0)==0,'Arming is not completed processing')
                        require(state()['pixel_bytes']==0 and state()['completion_events']==0,'No invented pixel work')
                        w(0x16019000,0);w(0xd154348,0x800000);w(0xd154248,0x800000)
                        require(int(m.q('readl 0x1601a120'),0)==0x120,'Independent coefficient bank')
                        try:m.qmp.command('qom-set',path='/machine',property='video-processing-config-model',value=False)
                        except RuntimeError:pass
                        else:raise AssertionError('Mutable NR configuration model')
                require(('Z9_UNKNOWN' in m.log_path.read_text())==bad,'Strict admission mismatch: '+name)
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),nr_algorithm_verified=False,recording_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
