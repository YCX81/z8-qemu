# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed video geometry setup; this does not emulate pixel processing."""
import argparse
import json
from pathlib import Path
import tempfile
from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    fields={0:3,4:0x3fff3fff,8:0x3fff3fff,0x10:0x7f,0x100:0xffff,
            0x104:255,0x108:1,0x10c:1,0x110:255,0x114:3,0x118:3,0x11c:3}
    fields.update({off:15 if off&4 else 0xffffffff for off in range(0x120,0x160,4)})
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'setup',kernel,accelerator='qtest',
                     properties=('engine150c-control-model=on','engine150c-stop-model=on','z8-video-interface-model=on')) as m:
            for off,mask in fields.items():
                address=0x150d9800+off;m.q(f'writel {address:#x} {mask:#x}')
                if int(m.q(f'readl {address:#x}'),0)!=mask:raise AssertionError(f'Geometry word {off:x}')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Observed setup rejected')
            cases.append(dict(name='bounded geometry and address words',words=len(fields),passed=True))
            for address in (0x150c0060,0x150c0064,0x150c0008):m.q(f'writel {address:#x} 0x4000')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine150c-state'))
            if state['active']!=0x4000 or state['latched']!=0x4000:raise AssertionError('Video geometry start chain')
            if state['processed_jobs'] or state['completion_events']:raise AssertionError('Configuration invented frames')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Native geometry start rejected')
            cases.append(dict(name='configure commit enable without completion',passed=True))
            second=dict(fields);second.update({8:0x3fff,12:0x3fffffff,16:0x3fff3fff,20:0x1ff})
            for off,mask in second.items():
                address=0x150d9a00+off;m.q(f'writel {address:#x} {mask:#x}')
                if int(m.q(f'readl {address:#x}'),0)!=mask:raise AssertionError(f'Second geometry word {off:x}')
            for off,mask in fields.items():
                if int(m.q(f'readl {0x150d9800+off:#x}'),0)!=mask:raise AssertionError('Aliased geometry bank')
            for address in (0x150c0060,0x150c0064,0x150c0008):m.q(f'writel {address:#x} 0x8000')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine150c-state'))
            if state['active']!=0xc000 or state['latched']!=0xc000:raise AssertionError('Second native geometry chain')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Second geometry setup rejected')
            cases.append(dict(name='second video geometry bank and submit',passed=True))
            # Original40c1fc78 sends stop, hold, then the mapped kick.
            for address,value in ((0x150c0218,0x6000000),(0x150c000c,0xc000),(0x150c0018,0xc000),
                                  (0x150c0034,0x18000)):
                m.q(f'writel {address:#x} {value:#x}')
            if int(m.q('readl 0x150c021c'),0)!=0x6000000:
                raise AssertionError('Video stop causes must match native ISR bits25/26\n'+m.log_path.read_text()[-1800:])
            for address,value in ((0x150c021c,0x6000000),(0x150c0000,0xc000),
                                  (0x150c0004,0xc000),(0x150c0018,0),(0x150c0014,0xc000)):
                m.q(f'writel {address:#x} {value:#x}')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine150c-state'))
            if state['active'] or state['latched'] or state['processed_jobs'] or state['completion_events']:
                raise AssertionError('Video stop did not drain only control state')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Native video stop rejected')
            cases.append(dict(name='native stop hold kick ISR cleanup without frames',passed=True))
        for name,command in (('unknown word','readl 0x150d9820'),('reserved bits','writel 0x150d9804 0x4000'),
                             ('status not invented','readl 0x150d9960'),('byte access','readb 0x150d9800')):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',
                         properties=('engine150c-control-model=on','z8-video-interface-model=on')) as m:
                m.q(command)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError(name)
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
