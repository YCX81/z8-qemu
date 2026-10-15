# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent original object1f4-1f6 configuration; no invented frame data."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_persistent_media import Machine
from check_z8_machine import sha256

def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'config',kernel,accelerator='qtest',properties=('z8-video-interface-model=on',)) as m:
            for bank in range(4):
                base=0x15661000+bank*0x400
                m.q(f'writel {base:#x} 1')
                m.q(f'writel {base+8:#x} {64+bank}')
                for half in (0x200,0x300):
                    for index in range(8):
                        address=base+half+0x80+index*8;value=0xa01000000+bank*0x100000+half*4096+index*4096
                        m.q(f'writeq {address:#x} {value:#x}')
                        assert int(m.q(f'readq {address:#x}'),0)==value
            m.q('writel 0x15660000 1')
            for bank in range(4):assert int(m.q(f'readl {0x15661008+bank*0x400:#x}'),0)==64+bank
            assert int(m.q('readl 0x15648000'),0)==0,'Independent control domains'
            m.q('writel 0x15660010 1')
            for bank in range(4):assert int(m.q(f'readl {0x15661000+bank*0x400:#x}'),0)==0
            assert 'Z9_UNKNOWN' not in m.log_path.read_text()
            cases.append(dict(name='four channels and two descriptor banks per channel',passed=True))
        for name,cmd,opt in (('off','readl 0x15660000',False),('status','readl 0x15660064',True),('queue','readl 0x15661020',True),('reserved','writel 0x15661244 0x8000',True),('byte','readb 0x15661000',True)):
            with Machine(binary,root/name,kernel,accelerator='qtest',properties=('z8-video-interface-model=on',) if opt else ()) as m:
                m.q(cmd);assert 'Z9_UNKNOWN' in m.log_path.read_text(),name
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
