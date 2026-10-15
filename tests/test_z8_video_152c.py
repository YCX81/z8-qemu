# SPDX-License-Identifier: GPL-2.0-or-later
"""Original video interface setup sequence, with no producer/completion."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_persistent_media import Machine
from check_z8_machine import sha256

def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'native-init',kernel,accelerator='qtest',properties=('z8-video-interface-model=on',)) as m:
            # 40c31610..40c31648, followed by original 40c31658 enable.
            for off,value in ((0x404,1),(4,1),(0,1),(4,0),(0,0),(0x18,0),(0x1c,0x7ff),(0x404,0),(8,0),
                              (0x500,1920),(0x504,0x201),(0x508,1080),(0x510,2),(0x528,1),
                              (0x220,0x12340000),(0x224,0xd),(0x404,1),(0x14,1),(0x404,0),(8,1)):
                m.q(f'writel {0x152c0000+off:#x} {value:#x}')
            for off,value in ((0x500,1920),(0x508,1080),(0x220,0x12340000),(0x224,0xd),(8,1),(0x10,0)):
                if int(m.q(f'readl {0x152c0000+off:#x}'),0)!=value:raise AssertionError(f'Interface readback {off:x}')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError(m.log_path.read_text()[-1600:])
            cases.append(dict(name='native init and request without completion',passed=True))
            # The same native object uses a separate configuration aperture
            # at15ac0000 (40c31988..40c31a48); port50 receives table words.
            for off,value in ((0x14,0x102c),(0x1c,0x0914),(0x2c,0x82),(0x3c,16),(0x44,0),
                              (0x48,1),(0x4c,1),(0x50,123),(0x50,456),(0xa0,12),(0xa4,500),
                              (0xa8,500),(0xac,100),(0xb0,100),(0xb4,4),(0x38,1)):
                m.q(f'writel {0x15ac0000+off:#x} {value:#x}')
            if int(m.q('readl 0x15ac0014'),0)!=0x102c:raise AssertionError('Second interface aperture')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Native side configuration rejected')
            if int(m.q('readl 0x152c0010'),0):raise AssertionError('Configuration fabricated event')
            cases.append(dict(name='native side configuration without encoded data',passed=True))
        for name,cmd in (('pending is read only','writel 0x152c0010 1'),('reserved bit','writel 0x152c0504 0x1000'),
                         ('unimplemented pixel status','readl 0x152c0160'),('unknown register','readl 0x152c0090')):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',properties=('z8-video-interface-model=on',)) as m:
                m.q(cmd)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError(name)
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
