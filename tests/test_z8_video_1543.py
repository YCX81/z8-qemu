# SPDX-License-Identifier: GPL-2.0-or-later
"""Video output has two configured addresses per row, not the photo eight."""
import argparse,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for name,opt,index,omit,accepted in (
            ('native video two plane setup',True,1,False,True),
            ('video partial configuration',True,1,True,False),
            ('photo still requires all addresses',True,0,False,False),
            ('video opt in required',False,1,False,False)):
            props=('engine1543-control-model=on',)+(('z8-video-interface-model=on',) if opt else ())
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',properties=props) as m:
                base=0x15431000+index*0x1000
                for off,value in ((4,0x103),(8,0),(12,0),(16,0)):
                    m.q(f'writel {base+off:#x} {value:#x}')
                for row in range(4):
                    for word in range(4):
                        if omit and row==3 and word==3:continue
                        m.q(f'writel {base+0x200+row*0x80+word*4:#x} {10 if word%2 else 0x100000+row*4096+word*1024:#x}')
                m.q(f'writel {base+4:#x} 0x101')
                bad='Z9_UNKNOWN' in m.log_path.read_text()
                if bad==accepted:raise AssertionError(name)
                state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine1543-control-state'))
                if state['blocks'][index]['active']!=accepted:raise AssertionError('Active state')
                if state['pixel_bytes'] or state['completion_events']:raise AssertionError('Invented output')
                if accepted:
                    m.q(f'writel {base+4:#x} 0x100')
                    after=json.loads(m.qmp.command('qom-get',path='/machine',property='engine1543-control-state'))
                    if after['blocks'][index]['active'] or after['blocks'][index]['stops']!=1:raise AssertionError('Stop')
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
