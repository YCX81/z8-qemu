# SPDX-License-Identifier: GPL-2.0-or-later
"""Second native video interface: scoped control/reset/configuration contract."""
import argparse
import json
import tempfile
from pathlib import Path

from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root = Path(temporary)
        kernel = root / 'zero.bin'
        kernel.write_bytes(bytes(4096))
        with Machine(binary, root/'sequence', kernel, accelerator='qtest',
                     properties=('z8-video-interface-model=on',)) as m:
            # Native40cc4920 releases ID27(hex), table entry {4,14}.
            m.q('writel 0xd154244 0x4000')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if not state.get('unit39_released'):
                raise AssertionError('Native shared video interface release')
            m.q('writel 0x15649008 1920')
            m.q('writel 0xd154344 0x4000')
            if int(m.q('readl 0x15649008'),0):raise AssertionError('Shared interface reset did not clear configuration')
            m.q('writel 0xd154244 0x4000')
            for value in (7,3):
                m.q(f'writel 0xd154150 {value}')
                m.q('writel 0xd154030 1')
                if int(m.q('readl 0xd154030'),0):raise AssertionError('Clock configuration still busy')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if state.get('clock_latched')!=3 or state.get('clock_commits')!=2:
                raise AssertionError('Native video clock configuration')
            for address in (0x15002100,0x15002104,0x15002108):
                m.q(f'writel {address:#x} 1');m.q(f'writel {address:#x} 0')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if state.get('task_pulses')!=[1,1,1]:raise AssertionError('Native task control pulses')
            # 40c1fbac control, 40c20338 selects ID15, reset table4164c494.
            m.q('writel 0x150c0010 0x200000')
            if int(m.q('readl 0x150c0010'), 0) != 0x200000:
                raise AssertionError('Second interface control request')
            m.q('writel 0x150c0014 0x200000')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Second interface control acknowledgement')
            # Different values demonstrate that the sister devices do not alias.
            m.q('writel 0x152c0500 1920')
            m.q('writel 0x15300500 3840')
            m.q('writel 0x15b00084 0x12345678')
            m.q('writel 0xd154340 0x200000')
            m.q('writel 0xd154240 0x200000')
            for address, expected in ((0x152c0500,1920),(0x15300500,0),(0x15b00084,0)):
                if int(m.q(f'readl {address:#x}'),0) != expected:
                    raise AssertionError('Reset isolation')
            # Native40c30e60 and40c30ea4, with no producer or IRQ.
            for offset,value in ((0x404,1),(4,1),(0,1),(4,0),(0,0),(0x1c,0x7ff),
                                 (0x404,0),(8,0),(0x500,3840),(0x504,0x201),
                                 (0x508,2160),(0x528,1),(0x52c,0x7f),
                                 (0x554,15),(0x558,0x7ff),(0x404,1),
                                 (0x14,1),(0x404,0),(8,1)):
                m.q(f'writel {0x15300000+offset:#x} {value:#x}')
            if int(m.q('readl 0x15300010'),0):
                raise AssertionError('Configuration invented event')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if state['unit21_reset'] or state['unit21_reset_asserts']!=1 or state['unit21_reset_releases']!=1:
                raise AssertionError('Second reset sequence')
            if state['transferred_bytes'] or state['completion_events']:
                raise AssertionError('Reset invented data or completion')
            if 'Z9_UNKNOWN' in m.log_path.read_text():
                raise AssertionError('Native sequence rejected')
            cases.append(dict(name='control reset configuration isolation',passed=True))
        for name, command, opt in (
            ('control opt in','writel 0x150c0010 0x200000',False),
            ('control acknowledgement opt in','writel 0x150c0014 0x200000',False),
            ('reset opt in','writel 0xd154340 0x200000',False),
            ('configuration opt in','readl 0x15300500',False),
            ('shared interface opt in','writel 0xd154244 0x4000',False),
            ('shared assert opt in','writel 0xd154344 0x4000',False),
            ('shared duplicate assert','writel 0xd154344 0x4000',True),
            ('clock commit before configuration','writel 0xd154030 1',True),
            ('clock fields bounded','writel 0xd154150 8',True),
            ('clock opt in','writel 0xd154150 7',False),
            ('pulse order','writel 0x15002100 0',True),
            ('pulse value','writel 0x15002100 2',True),
            ('pulse opt in','writel 0x15002100 1',False),
            ('pending read only','writel 0x15300010 1',True),
            ('processed buffer unsupported','readl 0x15300160',True),
            ('unknown offset','readl 0x15300090',True),
            ('reserved field','writel 0x15300554 16',True),
            ('reset release without assert','writel 0xd154240 0x200000',True),
            ('byte access','readb 0x15300500',True)):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',
                         properties=('z8-video-interface-model=on',) if opt else ()) as m:
                m.q(command)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():
                    raise AssertionError(name)
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
