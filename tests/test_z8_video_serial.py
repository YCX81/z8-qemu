# SPDX-License-Identifier: GPL-2.0-or-later
"""Configuration-only serial5 checks for the observed Z8 video transition.

No firmware execution, transfer completion, audio samples or recording claims.
"""
import argparse
import json
from pathlib import Path
import tempfile
from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'configuration',kernel,accelerator='qtest',properties=('z8-video-interface-model=on',)) as m:
            offsets=(8,12,20,24,16,64,68)
            expected={}
            for channel in range(5):
                for offset in offsets:
                    address=0xe200100+channel*256+offset
                    if int(m.q(f'readl {address:#x}'),0)!=0:raise AssertionError('Initial configuration')
                    value=0x81000000|channel<<16|offset
                    m.q(f'writel {address:#x} {value:#x}');expected[address]=value
            for address,value in expected.items():
                if int(m.q(f'readl {address:#x}'),0)!=value:raise AssertionError('Configuration/channel isolation')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Observed video configuration rejected')
            cases.append(dict(name='five independent configuration banks',passed=True,words=len(expected)))
        for name,command in (('transfer still unsupported','readl 0xe200100'),
                             ('FIFO still unsupported','readl 0xe200148'),
                             ('byte access rejected','readb 0xe200110'),
                             ('sixth bank rejected','readl 0xe200610'),
                             ('extra configuration requires opt-in','readl 0xe200110')):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest') as m:
                m.q(command)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError(name)
                cases.append(dict(name=name,passed=True))
        with Machine(binary,root/'gates',kernel,accelerator='qtest',
                     properties=('z8-video-interface-model=on',)) as m:
            for channel in range(5):
                if int(m.q(f'readl {0xe20011c+channel*256:#x}'),0):raise AssertionError('Empty interface has no pending transfer')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Empty transfer state unsupported')
            for value in (0xc0001,0xc1001,0xc3001,0xc1001,0xc0001):
                m.q(f'writel 0xe200108 {value:#x}')
                if int(m.q('readl 0xe200108'),0)!=value:raise AssertionError('Requested interface gates not retained')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Observed interface gates rejected')
            m.q('readl 0xe200138')
            if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError('Gate enable invented FIFO data')
            cases.append(dict(name='explicit gates without FIFO or completion',passed=True))
        with Machine(binary,root/'video-reset',kernel,accelerator='qtest',
                     properties=('z8-video-interface-model=on',)) as m:
            m.q('writel 0xd154340 2');m.q('writel 0xd154240 2')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Reset pair rejected')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if state['unit1_reset'] or state['reset_asserts']!=1 or state['reset_releases']!=1:
                raise AssertionError('Native unit1 reset pair')
            if state['transferred_bytes'] or state['completion_events']:raise AssertionError('Reset invented transfer')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Reset pair rejected')
            cases.append(dict(name='unit1 reset pair without data or completion',passed=True))
        with Machine(binary,root/'gates-off',kernel,accelerator='qtest') as m:
            m.q('writel 0xe200108 0xc1001')
            if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError('Gate model implicitly enabled')
            cases.append(dict(name='gates require explicit opt-in',passed=True))
        with Machine(binary,root/'external-input',kernel,accelerator='qtest',
                     properties=('z8-video-interface-model=on','z8-video-serial-input=1:2:3:4')) as m:
            for index in range(4):
                if int(m.q(f'readl {0xe200154+index*4:#x}'),0)!=index+1:raise AssertionError('External words')
            for invalid in ('1:2:3','1:2:3:-1','1:2:3:4294967296','1:2:3:x'):
                try:m.qmp.command('qom-set',path='/machine',property='z8-video-serial-input',value=invalid)
                except RuntimeError:pass
                else:raise AssertionError('Malformed external input accepted')
                if int(m.q('readl 0xe200160'),0)!=4:raise AssertionError('Partial invalid input update')
            m.q('writel 0xe200154 5')
            if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError('External readback is writable by guest')
            cases.append(dict(name='explicit read-only input with atomic validation',passed=True))
    result=dict(qemu_sha256=sha256(binary),cases=cases,passed=True,firmware_execution=False,
                transfer_completion_modeled=False,audio_verified=False,recording_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
